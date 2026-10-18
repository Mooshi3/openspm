// Scheduler simulation for renderer/aurora/present_timeline.hpp.
//
// Simulates a 60 Hz game (retrace grid locked to the display when vanilla 60
// fits it, else the host 60.000 Hz clock) whose frames finish after jittery
// CPU/GPU work, a FIFO display whose vblanks run a few ppm off the host clock,
// and a presenter that sleeps until the timeline's wake time (with wake-up
// jitter), selects, submits and blocks in Present() until the vblank.
// Asserts, per refresh rate: one present per vblank, timeline alpha error
// < 0.5 ms vs the ideal game timeline at the real scan-out time, monotonic
// position, recovery from a 100 ms stall, and in Vanilla mode on whole
// multiples of 60 Hz exactly k refreshes per game frame.
#include "display_pacing.hpp"
#include "present_timeline.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <vector>

using openspm::timeline::Config;
using openspm::timeline::Mode;
using openspm::timeline::PresentTimeline;

namespace {

struct Scenario {
  const char* name;
  uint64_t num, den;
  Mode mode;
  bool stall;
  double display_ppm;  // display clock vs host clock
  uint32_t seed;
  double phase_shift;  // game clock re-anchored by this many periods at 8 s
};

struct Result {
  bool ok = true;
  double max_err_ms = 0.0, rms_err_ms = 0.0, delay_pp_ms = 0.0, delay_ms = 0.0;
  uint64_t presents = 0, gaps = 0, mispredicted = 0, late = 0, steady_late = 0;
  uint64_t reanchors = 0, recenters = 0;
  uint64_t holds[5] = {};  // holds of 1,2,3,4,5+ refreshes (steady window)
  double recover_ms = -1.0;
};

struct GameFrame {
  uint64_t id;
  int64_t slot;
  double done;
};

Result run(const Scenario& sc, bool verbose) {
  std::mt19937_64 rng(sc.seed);
  std::uniform_real_distribution<double> u01(0.0, 1.0);
  std::normal_distribution<double> gauss(0.0, 1.0);

  const auto pacing = openspm::pacing::choose_vanilla_pacing(sc.num, sc.den);
  const double game_period = pacing.locked ? static_cast<double>(pacing.period_ns) : 16666667.0;
  const double t_nominal = openspm::timeline::refresh_period_ns(sc.num, sc.den);
  const double t_true = t_nominal * (1.0 + sc.display_ppm * 1e-6);

  Config cfg;
  cfg.refresh_numerator = sc.num;
  cfg.refresh_denominator = sc.den;
  cfg.game_period_ns = game_period;
  cfg.mode = sc.mode;
  PresentTimeline tl(cfg);

  const double start = 1.0e12;  // host ns
  const double duration = 12.0e9;
  const double warmup = 2.0e9;
  const double stall_at = start + 6.0e9, stall_len = 100.0e6;
  const double recover_window = 0.6e9;
  const double v0 = start + u01(rng) * t_true;
  const double g0 = start + u01(rng) * game_period;

  // Game frames: retrace grid slot, finished after jittery work. No catch-up
  // after a stall: the grid slots inside the stall simply never produce.
  std::vector<GameFrame> frames;
  uint64_t id = 1;
  for (int64_t slot = 0;; ++slot) {
    double retrace = g0 + static_cast<double>(slot) * game_period;
    if (sc.phase_shift != 0.0 && retrace >= start + 8.0e9) retrace += sc.phase_shift * game_period;
    if (retrace > start + duration + 1e9) break;
    if (sc.stall && retrace >= stall_at && retrace < stall_at + stall_len) continue;
    double work = 3.0e6 + 0.7e6 * gauss(rng);
    work = std::clamp(work, 1.0e6, 8.0e6);
    if (u01(rng) < 0.005) work += 3.0e6;  // occasional heavy frame
    frames.push_back({id++, slot, retrace + work});
  }
  // The producer publishes in slot order: a frame never completes before the
  // previous one.
  for (size_t i = 1; i < frames.size(); ++i) frames[i].done = std::max(frames[i].done, frames[i - 1].done + 0.2e6);

  size_t next_frame = 0;
  int64_t first_slot = frames.front().slot;
  auto deliver = [&](double until) {
    while (next_frame < frames.size() && frames[next_frame].done <= until) {
      tl.on_frame(frames[next_frame].id, frames[next_frame].done);
      ++next_frame;
    }
  };
  auto slot_of = [&](uint64_t fid) { return frames[fid - 1].slot; };

  Result res;
  double now = start;
  int64_t last_m = -1;
  double prev_pos = -1e300;
  uint64_t prev_show = 0, run_len = 0;
  bool prev_steady = false;
  std::vector<double> errs;
  double dmin = 1e300, dmax = -1e300;
  uint64_t recenters_at_warmup = 0;
  const bool stall_excl = sc.stall;
  struct Sample {
    double t, err;
    bool late;
  };
  std::vector<Sample> samples;

  while (now < start + duration) {
    double wake = std::max(now, tl.next_wake_ns(now));
    double jitter = 0.4e6 * u01(rng);
    if (u01(rng) < 0.02) jitter = 0.4e6 + 0.6e6 * u01(rng);
    const double actual = wake + jitter;
    deliver(actual);
    const auto sel = tl.select(actual);
    const double submit = actual + 0.1e6 + 0.3e6 * u01(rng);
    int64_t m = static_cast<int64_t>(std::ceil((submit - v0) / t_true));
    if (last_m >= 0) {
      if (m <= last_m) m = last_m + 1;  // FIFO queue: next free vblank
    }
    const double scan = v0 + static_cast<double>(m) * t_true;
    if (actual <= start + warmup) recenters_at_warmup = tl.stats().phase_recenters;
    const bool steady = actual > start + warmup &&
                        !(stall_excl && actual >= stall_at && actual < stall_at + stall_len + recover_window) &&
                        !(sc.phase_shift != 0.0 && actual >= start + 8.0e9 && actual < start + 9.0e9);
    if (last_m >= 0 && m != last_m + 1 && actual > start + warmup) res.gaps += static_cast<uint64_t>(m - last_m - 1);
    if (std::fabs(sel.scanout_ns - scan) > 0.5 * t_true && actual > start + warmup) ++res.mispredicted;
    last_m = m;
    ++res.presents;
    if (sel.valid) {
      if (sel.position + 1e-9 < prev_pos) {
        if (res.ok) std::printf("  %s: position went backwards %.6f -> %.6f\n", sc.name, prev_pos, sel.position);
        res.ok = false;
      }
      prev_pos = sel.position;
      if (sel.late) {
        ++res.late;
        if (steady) ++res.steady_late;
      }
      // Ideal: the game's grid timeline (slot index relative to the first
      // frame) at the real scan-out time, with the timeline's current delay.
      const double shift = sc.phase_shift != 0.0 && actual >= start + 8.5e9 ? sc.phase_shift : 0.0;
      const double ideal = (scan - (g0 + static_cast<double>(first_slot) * game_period)) / game_period - shift -
                           tl.delay_frames();
      const double err_ns = (sel.position - ideal) * game_period;
      // Alpha consistency: position == slot(A) + alpha * (slot(B) - slot(A)).
      const double sa = static_cast<double>(slot_of(sel.frame_a) - first_slot);
      const double sb = static_cast<double>(slot_of(sel.frame_b) - first_slot);
      if (sc.mode == Mode::Smooth && sel.frame_b != sel.frame_a && std::fabs(sa + sel.alpha * (sb - sa) - sel.position) > 1e-6) {
        if (res.ok) std::printf("  %s: alpha inconsistent with position\n", sc.name);
        res.ok = false;
      }
      if (steady) {
        errs.push_back(err_ns);
        const double d = tl.delay_frames() * game_period;
        if (std::getenv("TL_TRACE")) std::printf("D %.3f %.3f margin=%.3f\n", (actual-start)/1e9, d/1e6, tl.margin_ns()/1e6);
        dmin = std::min(dmin, d);
        dmax = std::max(dmax, d);
      }
      if (actual > start + warmup) samples.push_back({actual, err_ns, sel.late});
      if (std::getenv("TL_ERR") && std::strstr(sc.name, std::getenv("TL_ERR")))
        std::printf("E %.4f %.4f P=%.1f T=%.1f margin=%.3f pos=%.4f D=%.4f show=%llu rc=%llu\n", (actual - start) / 1e9, err_ns / 1e6,
                    tl.game_period_ns(), tl.display_period_ns(), tl.margin_ns() / 1e6, sel.position,
                    tl.delay_frames(), (unsigned long long)sel.show, (unsigned long long)tl.stats().phase_recenters);
      // Holds (consecutive vblanks showing the same frame).
      if (sel.show == prev_show) {
        ++run_len;
      } else {
        if (prev_show != 0 && prev_steady && steady) res.holds[std::min<uint64_t>(run_len, 5) - 1]++;
        prev_show = sel.show;
        run_len = 1;
        prev_steady = steady;
      }
    }
    const double ret = scan + 0.05e6 + 0.2e6 * u01(rng);
    deliver(ret);
    tl.on_present(ret);
    now = ret;
  }
  double mean = 0.0;
  for (double e : errs) mean += e;
  mean /= errs.empty() ? 1.0 : static_cast<double>(errs.size());
  if (sc.stall) {
    // Recovered = from here on every present is on time and within 0.5 ms.
    double last_bad = stall_at + stall_len;
    for (const auto& s : samples)
      if (s.t >= stall_at && (s.late || std::fabs(s.err - mean) >= 0.5e6)) last_bad = std::max(last_bad, s.t);
    res.recover_ms = (last_bad - (stall_at + stall_len)) / 1e6;
  }
  double sq = 0.0;
  for (double e : errs) {
    res.max_err_ms = std::max(res.max_err_ms, std::fabs(e - mean) / 1e6);
    sq += (e - mean) * (e - mean);
  }
  res.rms_err_ms = errs.empty() ? 0.0 : std::sqrt(sq / static_cast<double>(errs.size())) / 1e6;
  res.delay_pp_ms = (dmax - dmin) / 1e6;
  res.delay_ms = dmax / 1e6;
  res.reanchors = tl.stats().reanchors;
  res.recenters = tl.stats().phase_recenters - recenters_at_warmup;

  // Assertions.
  auto fail = [&](const char* what) {
    std::printf("  FAIL %s: %s\n", sc.name, what);
    res.ok = false;
  };
  if (res.gaps != 0) fail("a vblank without a present (steady state)");
  if (res.mispredicted != 0) fail("present scanned out at a different vblank than predicted");
  if (res.max_err_ms >= 0.5) fail("timeline error >= 0.5 ms vs ideal");
  if (res.steady_late != 0) fail("frame not finished when needed (steady state)");
  // A phase re-centre is a deliberate one-refresh latency step (at most one
  // after warm-up); otherwise latency may only drift by < 2 ms.
  if (res.recenters > (sc.phase_shift != 0.0 ? 2u : 1u)) fail("too many phase re-centres after warm-up");
  if (sc.phase_shift != 0.0 && res.reanchors == 0) fail("game phase change not re-anchored");
  if (sc.phase_shift == 0.0 && res.delay_pp_ms >= 2.0 + static_cast<double>(res.recenters) * t_nominal / 1e6)
    fail("latency wandered >= 2 ms after warm-up");
  if (sc.stall && res.recover_ms > recover_window / 1e6) fail("no recovery within 0.6 s of a 100 ms stall");
  const uint32_t k = pacing.locked ? pacing.multiple : 0u;
  if (sc.mode == Mode::Vanilla && k != 0u) {
    for (int i = 0; i < 5; ++i) {
      // A re-centre is a one-refresh latency step: one k+1 or k-1 hold.
      const uint64_t allowed = static_cast<uint32_t>(i) == k || static_cast<uint32_t>(i + 2) == k ? res.recenters : 0u;
      if (static_cast<uint32_t>(i + 1) != k && res.holds[i] > allowed) fail("vanilla frame not held exactly k refreshes");
    }
    if (res.holds[k - 1] == 0) fail("vanilla: no frames held k refreshes");
  }
  if (verbose || !res.ok) {
    std::printf("%-26s presents=%-5llu err_max=%.3fms err_rms=%.3fms delay=%.2fms(pp %.2f) late=%llu/%llu "
                "gaps=%llu mispred=%llu holds=[%llu,%llu,%llu,%llu,%llu] reanchor=%llu recentre=%llu%s\n",
                sc.name, (unsigned long long)res.presents, res.max_err_ms, res.rms_err_ms, res.delay_ms,
                res.delay_pp_ms, (unsigned long long)res.steady_late, (unsigned long long)res.late,
                (unsigned long long)res.gaps, (unsigned long long)res.mispredicted,
                (unsigned long long)res.holds[0], (unsigned long long)res.holds[1],
                (unsigned long long)res.holds[2], (unsigned long long)res.holds[3],
                (unsigned long long)res.holds[4], (unsigned long long)res.reanchors,
                (unsigned long long)res.recenters, sc.stall ? " stall" : "");
    if (sc.stall) std::printf("%-26s recovered %.1f ms after the stall ended\n", "", res.recover_ms);
  }
  return res;
}

// Direct unit checks of the pure model (no scheduler).
bool unit_checks() {
  bool ok = true;
  // Exact rational periods, never integer truncated.
  const double t = openspm::timeline::refresh_period_ns(99943, 1000);
  if (std::fabs(t - 1.0e12 / 99943.0) > 1e-3) ok = false;
  Config c;
  c.refresh_numerator = 120;
  c.refresh_denominator = 1;
  PresentTimeline tl(c);
  if (tl.locked_multiple() != 2u) ok = false;
  c.refresh_numerator = 99943;
  c.refresh_denominator = 1000;
  PresentTimeline tl2(c);
  if (tl2.locked_multiple() != 0u) ok = false;
  // No frames yet: selection is invalid but still targets a vblank.
  const auto sel = tl2.select(1.0e9);
  if (sel.valid || sel.scanout_ns <= 1.0e9) ok = false;
  if (!ok) std::printf("FAIL unit checks\n");
  return ok;
}

}  // namespace

int main(int argc, char** argv) {
  const bool verbose = argc > 1 && std::strcmp(argv[1], "-v") == 0;
  bool ok = unit_checks();
  struct Rate {
    const char* label;
    uint64_t num, den;
  };
  const Rate rates[] = {{"59.94", 60000, 1001}, {"60", 60, 1},   {"75", 75, 1},   {"99.943", 99943, 1000},
                        {"100", 100, 1},        {"120", 120, 1}, {"144", 144, 1}, {"165", 165, 1},
                        {"240", 240, 1}};
  // -s N: robustness sweep over N extra seeds (not part of the default gate).
  const int sweeps = argc > 2 && std::strcmp(argv[1], "-s") == 0 ? std::atoi(argv[2]) : 0;
  for (int sweep = 0; sweep < sweeps; ++sweep) {
    uint32_t sseed = 100000u + static_cast<uint32_t>(sweep) * 97u;
    for (const auto& r : rates)
      for (const Mode mode : {Mode::Smooth, Mode::Vanilla})
        for (const bool stall : {false, true}) {
          char name[64];
          std::snprintf(name, sizeof(name), "%s Hz %s%s #%d", r.label, mode == Mode::Smooth ? "smooth" : "vanilla",
                        stall ? "+stall" : "", sweep);
          const Scenario sc{name, r.num, r.den, mode, stall, (sseed % 2 ? 25.0 : -18.0), sseed++, 0.0};
          ok = run(sc, false).ok && ok;
        }
  }
  uint32_t seed = 1234;
  for (const auto& r : rates) {
    for (const Mode mode : {Mode::Smooth, Mode::Vanilla}) {
      for (const bool stall : {false, true}) {
        char name[64];
        std::snprintf(name, sizeof(name), "%s Hz %s%s", r.label, mode == Mode::Smooth ? "smooth" : "vanilla",
                      stall ? "+stall" : "");
        const Scenario sc{name, r.num, r.den, mode, stall, (seed % 2 ? 25.0 : -18.0), seed++, 0.0};
        ok = run(sc, verbose).ok && ok;
      }
    }
  }
  // Game clock re-anchored mid-run (e.g. after a load): later and earlier.
  for (const double shift : {0.5, -0.4}) {
    for (const Rate& r : {Rate{"99.943", 99943, 1000}, Rate{"120", 120, 1}}) {
      char name[64];
      std::snprintf(name, sizeof(name), "%s Hz smooth shift%+.1f", r.label, shift);
      Scenario sc{name, r.num, r.den, Mode::Smooth, false, 25.0, seed++, 0.0};
      sc.phase_shift = shift;
      ok = run(sc, verbose).ok && ok;
    }
  }
  std::printf(ok ? "PASS present timeline scheduler simulation\n" : "FAIL present timeline scheduler simulation\n");
  return ok ? 0 : 1;
}
