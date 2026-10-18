// OpenSPM present-time pacing model (pure C++, no SDL/Aurora/clock dependency).
//
// Stage A of docs/PRESENTATION-ARCHITECTURE.md: decide, for every display
// refresh, which finished 60 Hz game frame(s) belong on screen and at what
// interpolation alpha, for the predicted scan-out time of that refresh.
//
// Two timelines, both small second-order PLLs anchored at their latest sample
// (so a slope correction never rotates the line around a far-away origin):
//
//  * display: vblank k scans out at v_ref + (k - k_ref) * T. T starts at the
//    display's exact rational refresh (e.g. 1e9 * 1000 / 99943 ns, never an
//    integer-Hz truncation) and only trims host-clock ppm drift. It is fed by
//    the time each FIFO Present() returns: with FIFO vsync the swapchain
//    blocks, so present completions are the vblank clock.
//  * game: frame n (a grid slot of the game's retrace clock) completes at
//    t_ref + (n - n_ref) * P. Publishes are assigned to grid slots by host
//    time, so a stall (no publishes for 100 ms) is an index gap on the same
//    line rather than a phase error. A persistent phase change (the game
//    re-anchored its clock) re-anchors the line: "snap".
//
// Presentation position for a scan-out at host time s:
//     g(s) = n_ref + (s - t_ref) / P - D
// Smooth:  D = 1 + (lead + margin) / P  -> frames A = floor(g), B = A + 1 are
//          both finished when the present is prepared (one frame of delay);
//          alpha = frac(g).
// Vanilla: D = (lead + margin) / P      -> newest frame finished on the
//          smoothed timeline, alpha = 0, each frame held k refreshes on a
//          display that is a whole multiple of the game rate.
// lead = time from preparing a present to its scan-out (the presenter's guard),
// margin = tracked lateness of frame completions vs the fitted line.
//
// g never decreases, D moves slowly (latency adaptation, not a timing error),
// and a missing frame holds the newest one instead of guessing.
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace openspm::timeline {

enum class Mode : uint8_t { Vanilla = 0, Smooth = 1 };

struct Config {
  uint64_t refresh_numerator = 60u;   // display refresh as an exact rational (Hz)
  uint64_t refresh_denominator = 1u;
  double game_period_ns = 1.0e9 / 60.0; // nominal game frame period (locked retrace period if any)
  Mode mode = Mode::Smooth;
  double guard_ns = 0.0;         // 0 = auto: clamp(0.4 * T, 1.5 ms, 5 ms)
  double min_lead_ns = 0.5e6;    // a present must be prepared at least this long before scan-out
  double min_margin_ns = 1.0e6;  // floor of the frame-lateness margin
  uint32_t retained_frames = 4u; // finished images the presenter keeps (2..8)
};

inline double refresh_period_ns(uint64_t numerator, uint64_t denominator) {
  if (numerator == 0u || denominator == 0u) return 1.0e9 / 60.0;
  return static_cast<double>(1.0e9L * static_cast<long double>(denominator) /
                             static_cast<long double>(numerator));
}

struct Selection {
  bool valid = false;        // false until the first game frame exists
  uint64_t frame_a = 0u;     // publish ids of the interpolation pair
  uint64_t frame_b = 0u;     // == frame_a when no newer frame is available
  uint64_t show = 0u;        // Stage A: the frame to present (nearest of A/B; A in Vanilla)
  double alpha = 0.0;        // interpolation weight of B for the predicted scan-out
  double position = 0.0;     // g(s), in game grid slots
  double scanout_ns = 0.0;   // predicted scan-out time of this present
  bool late = false;         // the frame the timeline wanted was not finished: held
  bool snapped = false;      // timeline jumped forward to the retained frames
};

struct Stats {
  uint64_t presents = 0, frames = 0, late = 0, snaps = 0, missed_vblanks = 0;
  uint64_t reanchors = 0, phase_recenters = 0;
};

class PresentTimeline {
public:
  static constexpr size_t kHistory = 8;

  explicit PresentTimeline(const Config& config = {}) { configure(config); }

  void configure(const Config& config) {
    config_ = config;
    T_nominal_ = refresh_period_ns(config.refresh_numerator, config.refresh_denominator);
    P_nominal_ = config.game_period_ns > 1.0e6 ? config.game_period_ns : 1.0e9 / 60.0;
    retained_ = std::clamp<size_t>(config.retained_frames, 2u, kHistory);
    guard_ = config.guard_ns > 0.0 ? config.guard_ns
                                   : std::clamp(0.4 * T_nominal_, 1.5e6, 5.0e6);
    reset();
  }

  void reset() {
    T_ = T_nominal_;
    P_ = P_nominal_;
    have_v_ = have_g_ = have_d_ = false;
    v_ref_ = 0.0;
    last_target_ = -1.0e300;
    pending_count_ = pending_head_ = 0;
    t_ref_ = 0.0;
    n_ref_ = 0;
    count_ = 0;
    margin_ = std::max(config_.min_margin_ns, 0.3 * P_nominal_);
    var_ = 0.0;
    offside_ = 0;
    D_ = 0.0;
    phase_ = 0.0;
    phase_init_ = false;
    vblank_samples_ = 0;
    g_prev_ = -1.0e300;
    stats_ = {};
  }

  // A game frame finished (its image is published and retained) at host_ns.
  void on_frame(uint64_t id, double host_ns) {
    ++stats_.frames;
    if (!have_g_) {
      have_g_ = true;
      t_ref_ = host_ns;
      n_ref_ = 0;
      push_frame(id, 0, host_ns);
      return;
    }
    const int64_t last = history_[(count_ - 1) % kHistory].index;
    // Assign the publish to a grid slot. Frames up to 0.75 P late stay in
    // their slot; anything later is the next slot (a dropped retrace/stall).
    const double x = static_cast<double>(n_ref_) + (host_ns - t_ref_) / P_;
    int64_t n = static_cast<int64_t>(std::floor(x + 0.25));
    n = std::max(n, last + 1);
    // Advance the line to slot n, then correct it.
    t_ref_ += P_ * static_cast<double>(n - n_ref_);
    n_ref_ = n;
    const double r = host_ns - t_ref_;
    // Persistent phase change (the game re-anchored its clock): re-anchor.
    if (std::fabs(r) > std::min(0.6 * P_, std::max(0.35 * P_, 1.5 * margin_))) {
      if (++offside_ >= 3) {
        t_ref_ = host_ns;
        offside_ = 0;
        var_ = 0.0;
        ++stats_.reanchors;
      }
    } else {
      offside_ = 0;
    }
    const double rc = std::clamp(host_ns - t_ref_, -0.25 * P_, 0.25 * P_);
    // Heavy frames are one-sided outliers: limit their pull on the line.
    const double pull_limit = std::max(1.0e6, 2.5 * std::sqrt(var_));
    const double rp = std::clamp(rc, -pull_limit, pull_limit);
    // Acquisition: average the phase over the first frames (1/n gain) before
    // the frequency term engages, so start-up jitter never biases the slope.
    const double seen = static_cast<double>(count_);
    const double kp = std::max(kGameKp, 1.0 / (seen + 1.0));
    const double ki = count_ < kAcquireFrames ? 0.0 : kGameKi;
    t_ref_ += kp * rp;
    P_ = std::clamp(P_ + ki * rp, P_nominal_ * 0.99, P_nominal_ * 1.01);
    // Lateness margin: starts conservative (0.3 P), grows at once to cover a
    // late frame, and decays slowly (~10 s time constant) toward 4 sigma.
    var_ += 0.01 * (rc * rc - var_);
    const double floor_margin = std::max(config_.min_margin_ns, 4.0 * std::sqrt(var_));
    margin_ = std::max(rc + 0.5e6, margin_ - (margin_ - floor_margin) * 0.0015);
    margin_ = std::clamp(margin_, config_.min_margin_ns, 0.6 * P_);
    push_frame(id, n, host_ns);
  }

  // A FIFO Present() returned at host_ns (the swapchain released us at a vblank).
  void on_present(double host_ns) {
    double expected = host_ns;
    if (pending_count_ > 0) {
      expected = pending_[pending_head_];
      pending_head_ = (pending_head_ + 1) % pending_.size();
      --pending_count_;
    }
    if (!have_v_ || vblank_samples_ == 0) {
      have_v_ = true;
      v_ref_ = host_ns;
      ++vblank_samples_;
      return;
    }
    const double k = std::round((host_ns - v_ref_) / T_);
    const double predicted = v_ref_ + k * T_;
    const double e = host_ns - predicted;
    const double late_vblanks = std::round((predicted - expected) / T_);
    if (late_vblanks >= 1.0) stats_.missed_vblanks += static_cast<uint64_t>(late_vblanks);
    v_ref_ = predicted + kVblankKp * e;
    T_ = std::clamp(T_ + kVblankKi * e, T_nominal_ * (1.0 - 3.0e-4), T_nominal_ * (1.0 + 3.0e-4));
    ++vblank_samples_;
  }

  // A selected present was not submitted (surface lost, readback priority):
  // forget its target so later completions pair with the right vblank.
  void on_present_dropped() {
    if (pending_count_ == 0) return;
    pending_head_ = (pending_head_ + 1) % pending_.size();
    --pending_count_;
  }

  // Host time at which the presenter should prepare the next present.
  double next_wake_ns(double now_ns) {
    if (!have_v_) return now_ns;
    return next_target(now_ns) - guard_;
  }

  // Choose the frame(s) for the next vblank. Call when the present is prepared.
  Selection select(double now_ns) {
    Selection out;
    if (!have_v_) {  // no present observed yet: pretend a vblank is one guard away
      have_v_ = true;
      v_ref_ = now_ns + guard_;
    }
    const double s = next_target(now_ns);
    last_target_ = s;
    push_pending(s);
    ++stats_.presents;
    out.scanout_ns = s;
    if (!have_g_ || count_ == 0) return out;
    out.valid = true;

    const bool smooth = config_.mode == Mode::Smooth;
    const double base = static_cast<double>(n_ref_) + (s - t_ref_) / P_;
    const double target_d = (smooth ? 1.0 : 0.0) + (guard_ + margin_) / P_;
    if (!have_d_) {
      D_ = target_d;
      have_d_ = true;
    } else if (target_d > D_) {
      // Latency grows promptly (<= 0.01 frame per refresh) when frames turn
      // out later than the margin allows...
      D_ += std::min(target_d - D_, 0.01);
    } else if (target_d < D_ - 0.1) {
      // ...and shrinks only on clear evidence, very slowly (0.006 frame/s at
      // 60 Hz), so small margin changes never move the latency.
      D_ -= 0.0001;
    }
    update_phase(base);
    double g = base - D_ - phase_;

    const Frame& newest = history_[(count_ - 1) % kHistory];
    const size_t first = count_ - std::min(count_, retained_);
    const double newest_n = static_cast<double>(newest.index);
    // Smooth needs B = floor(g) + 1 finished; Vanilla needs A = floor(g).
    if (std::floor(g) + (smooth ? 1.0 : 0.0) > newest_n) {
      out.late = true;
      g = newest_n;  // hold the newest finished frame
    }
    // Behind every retained frame (a burst after a hitch): snap forward.
    const double oldest_n = static_cast<double>(history_[first % kHistory].index);
    if (g < oldest_n) {
      g = oldest_n;
      out.snapped = true;
    }
    g = std::max(g, g_prev_);  // monotonic
    g_prev_ = g;
    if (out.late) ++stats_.late;
    if (out.snapped) ++stats_.snaps;
    out.position = g;

    // A = latest retained frame at or before g, B = the one after it.
    size_t ia = first;
    for (size_t i = count_; i-- > first;) {
      if (static_cast<double>(history_[i % kHistory].index) <= g) {
        ia = i;
        break;
      }
    }
    const Frame& a = history_[ia % kHistory];
    out.frame_a = out.frame_b = out.show = a.id;
    if (ia + 1 < count_) {
      const Frame& b = history_[(ia + 1) % kHistory];
      out.frame_b = b.id;
      const double span = static_cast<double>(b.index - a.index);
      out.alpha = std::clamp((g - static_cast<double>(a.index)) / span, 0.0, 1.0);
    }
    if (!smooth) {
      out.alpha = 0.0;
    } else if (out.alpha >= 0.5) {
      // Stage A (no draw interpolation yet): show the nearer of A/B in time.
      out.show = out.frame_b;
    }
    return out;
  }

  // Stage C: the presenter needs more lead time while it renders
  // interpolated images. The delay target follows gradually (D grows by at
  // most 0.01 frame per refresh), so the timeline never jumps.
  void set_guard_ns(double guard) {
    guard_ = std::clamp(guard, 0.25 * T_nominal_, 0.9 * T_nominal_);
  }

  // Introspection for logs and tests.
  double display_period_ns() const { return T_; }
  double game_period_ns() const { return P_; }
  double margin_ns() const { return margin_; }
  double delay_frames() const { return D_ + phase_; }
  double guard_ns() const { return guard_; }
  bool refresh_locked() const { return locked_multiple() != 0; }
  const Stats& stats() const { return stats_; }
  const Config& config() const { return config_; }

  // Refreshes per game frame when the display is a whole multiple of the game
  // rate (within 0.2%), else 0.
  uint32_t locked_multiple() const {
    const double k = std::round(P_nominal_ / T_nominal_);
    if (k < 1.0 || k > 8.0) return 0u;
    return std::fabs(P_nominal_ - k * T_nominal_) <= 0.002 * P_nominal_ ? static_cast<uint32_t>(k) : 0u;
  }

private:
  struct Frame {
    uint64_t id = 0;
    int64_t index = 0;
    double host_ns = 0.0;
  };

  static constexpr double kGameKp = 0.03;
  static constexpr double kGameKi = 0.03 * 0.03 / 4.0;
  static constexpr size_t kAcquireFrames = 60;
  static constexpr double kVblankKp = 0.1;
  static constexpr double kVblankKi = 0.1 * 0.1 / 4.0;

  void push_frame(uint64_t id, int64_t index, double host_ns) {
    history_[count_ % kHistory] = Frame{id, index, host_ns};
    ++count_;
  }

  void push_pending(double s) {
    if (pending_count_ == pending_.size()) {  // presents never returned: forget the oldest
      pending_head_ = (pending_head_ + 1) % pending_.size();
      --pending_count_;
    }
    pending_[(pending_head_ + pending_count_) % pending_.size()] = s;
    ++pending_count_;
  }

  // First predicted vblank that is far enough away to prepare for and after
  // the one last targeted (one present per vblank).
  double next_target(double now_ns) const {
    const double earliest = std::max(now_ns + config_.min_lead_ns, last_target_ + 0.5 * T_);
    const double k = std::ceil((earliest - v_ref_) / T_);
    return v_ref_ + k * T_;
  }

  // On a display that is a whole multiple k of the game rate, the vblanks see
  // g at a fixed phase modulo 1/k. Keep that phase away from the points where
  // a choice flips on timing noise: frac(g) = 0 (floor) in both modes and
  // frac(g) = 0.5 (nearest of A/B) in Smooth. Vanilla and even k centre at
  // frac(k g) = 0.5 (every frame held exactly k refreshes); Smooth with odd k
  // centres at 0.25. Re-centred only when the slow latency adaptation drifts
  // it near an edge: a latency step of less than one refresh (one k+1 or k-1
  // hold), the only irregular hold a locked display ever sees.
  void update_phase(double base) {
    const uint32_t k = locked_multiple();
    if (k == 0u) {
      phase_ = 0.0;
      return;
    }
    const double kk = static_cast<double>(k);
    const bool odd_smooth = config_.mode == Mode::Smooth && (k % 2u) == 1u;
    const double centre = odd_smooth ? 0.25 : 0.5;
    const double tolerance = odd_smooth ? 0.18 : 0.4;
    const double x = (base - D_ - phase_) * kk;
    double frac = x - std::floor(x);
    if (odd_smooth) frac = 2.0 * frac - std::floor(2.0 * frac);  // 0 and 0.5 both forbidden
    if (odd_smooth) frac *= 0.5;
    if (!phase_init_ || std::fabs(frac - centre) > tolerance) {
      double shift = (frac - centre) / kk;
      if (shift < 0.0) shift += (odd_smooth ? 0.5 : 1.0) / kk;
      if (phase_init_) ++stats_.phase_recenters;
      phase_ = std::fmod(phase_ + shift, 1.0 / kk);
      phase_init_ = true;
    }
  }

  Config config_;
  double T_nominal_ = 0.0, P_nominal_ = 0.0, guard_ = 0.0;
  size_t retained_ = 4;
  double T_ = 0.0, P_ = 0.0;
  bool have_v_ = false, have_g_ = false, have_d_ = false, phase_init_ = false;
  uint64_t vblank_samples_ = 0;
  double v_ref_ = 0.0, last_target_ = 0.0;
  std::array<double, 8> pending_{};
  size_t pending_count_ = 0, pending_head_ = 0;
  double t_ref_ = 0.0;
  int64_t n_ref_ = 0;
  std::array<Frame, kHistory> history_{};
  size_t count_ = 0;
  double margin_ = 0.0, var_ = 0.0;
  int offside_ = 0;
  double D_ = 0.0, phase_ = 0.0, g_prev_ = 0.0;
  Stats stats_;
};

} // namespace openspm::timeline
