// OpenSPM display pacing policy (pure C++, no SDL/Aurora dependency).
//
// Vanilla SPM simulates at 60 Hz. A frame only looks even on screen when every
// simulated frame is held for the same whole number of display refreshes. With
// a free-running 60.000 Hz clock that is impossible on a 99.94 Hz panel
// (frames alternate 2,2,1 refreshes) and it even beats slowly against a panel
// that is really 59.94/59.98/60.02 Hz (a doubled/dropped frame every few
// seconds). Emulators solve this by syncing the console clock to the host
// refresh; OpenSPM does the same for the "vanilla" frame-rate mode:
//
//   display refresh R, multiple k = round(R / 60)
//   if R / k is within 0.5% of 60 -> simulate at exactly R / k Hz
//   otherwise                     -> stay at 60.000 Hz (judder is unavoidable
//                                    without VRR or interpolation)
//
// A 0.5% speed change is inaudible/invisible; 59.978 Hz is only 0.037% slow.
#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>

namespace openspm::pacing {

inline constexpr double kVanillaHz = 60.0;
inline constexpr double kMaxVanillaDeviation = 0.005; // 0.5%
inline constexpr uint32_t kMaxRefreshMultiple = 8u;   // up to 480 Hz panels

struct VanillaPacing {
  bool locked = false;       // simulation clock follows the display
  uint32_t multiple = 0u;    // refreshes per simulated frame when locked
  uint64_t period_ns = 0u;   // simulation period when locked
  double display_hz = 0.0;
  double simulation_hz = kVanillaHz;
};

// numerator/denominator is the display's exact rational refresh rate
// (SDL3 SDL_DisplayMode::refresh_rate_numerator/denominator).
inline VanillaPacing choose_vanilla_pacing(uint64_t numerator, uint64_t denominator) {
  VanillaPacing out;
  if (numerator == 0u || denominator == 0u) return out;
  out.display_hz = static_cast<double>(numerator) / static_cast<double>(denominator);
  if (!std::isfinite(out.display_hz) || out.display_hz < kVanillaHz * (1.0 - kMaxVanillaDeviation))
    return out;
  const auto k = static_cast<uint32_t>(std::llround(out.display_hz / kVanillaHz));
  if (k == 0u || k > kMaxRefreshMultiple) return out;
  const double sim = out.display_hz / static_cast<double>(k);
  if (std::fabs(sim - kVanillaHz) / kVanillaHz > kMaxVanillaDeviation) return out;
  // period = k / R seconds, computed from the exact rational to avoid float drift.
  const long double period = 1.0e9L * static_cast<long double>(k) *
                             static_cast<long double>(denominator) /
                             static_cast<long double>(numerator);
  out.locked = true;
  out.multiple = k;
  out.period_ns = static_cast<uint64_t>(std::llround(static_cast<double>(period)));
  out.simulation_hz = sim;
  return out;
}

struct RefreshCandidate {
  uint64_t numerator = 0u;
  uint64_t denominator = 0u;
};

// Exclusive-fullscreen refresh choice among the display's modes at the
// desktop resolution. requested_hz > 0 picks the offered rate nearest to it
// (within 1 Hz). Otherwise ("auto") pick a rate vanilla 60 locks to: the
// highest such refresh (more refreshes per game frame = lower latency), ties
// broken by the simulation rate closest to 60. Returns -1 when nothing fits;
// the caller then keeps the desktop refresh.
inline int choose_exclusive_refresh(const RefreshCandidate* modes, size_t count,
                                    double requested_hz) {
  int best = -1;
  double best_score = 0.0, best_error = 0.0;
  for (size_t i = 0; i < count; ++i) {
    if (modes[i].numerator == 0u || modes[i].denominator == 0u) continue;
    const double hz = static_cast<double>(modes[i].numerator) /
                      static_cast<double>(modes[i].denominator);
    if (requested_hz > 0.0) {
      const double error = std::fabs(hz - requested_hz);
      if (error <= 1.0 && (best < 0 || error < best_error)) {
        best = static_cast<int>(i);
        best_error = error;
      }
      continue;
    }
    const auto pacing = choose_vanilla_pacing(modes[i].numerator, modes[i].denominator);
    if (!pacing.locked) continue;
    const double error = std::fabs(pacing.simulation_hz - kVanillaHz);
    if (best < 0 || hz > best_score + 0.5 || (std::fabs(hz - best_score) <= 0.5 && error < best_error)) {
      best = static_cast<int>(i);
      best_score = hz;
      best_error = error;
    }
  }
  return best;
}

// Convenience for modes that only report a float refresh rate.
inline void rational_from_hz(double hz, uint64_t& numerator, uint64_t& denominator) {
  numerator = hz > 0.0 && std::isfinite(hz) ? static_cast<uint64_t>(std::llround(hz * 1000.0)) : 0u;
  denominator = 1000u;
}

} // namespace openspm::pacing
