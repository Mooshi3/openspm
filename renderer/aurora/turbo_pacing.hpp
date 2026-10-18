// OpenSPM fast-forward ("turbo") present policy (pure C++, no SDL/Aurora).
//
// While turbo is engaged the game finishes N frames per display refresh (or
// as many as the PC can with "unlimited"). With FIFO vsync every present waits
// for a vblank, so presenting all of them would throttle the game back to the
// display rate. The renderer asks this gate once per finished game frame:
// true = present it, false = submit it without presenting
// (aurora_m14_set_turbo AURORA_M14_TURBO_SKIP_PRESENT).
//
// The gate keeps a grid at the display's refresh period and shows the first
// frame finished at or after each grid point, i.e. at most one image per
// refresh, the newest the game had by then. If the game falls behind (a frame
// takes longer than a refresh) every frame is shown and the grid restarts.
// Outside turbo the renderer never consults the gate.
#pragma once

#include <cstdint>

namespace openspm::turbo {

class PresentGate {
public:
  // period_ns: display refresh period; <= 0 means 60 Hz.
  void reset(double period_ns) {
    period_ns_ = period_ns > 0.0 ? period_ns : 1.0e9 / 60.0;
    started_ = false;
    next_ns_ = 0.0;
  }

  bool should_present(uint64_t now_ns) {
    const double now = static_cast<double>(now_ns);
    if (!started_ || now + period_ns_ < next_ns_) {  // first frame, or the clock went back
      started_ = true;
      next_ns_ = now + period_ns_;
      return true;
    }
    if (now < next_ns_) return false;
    next_ns_ += period_ns_;
    if (now >= next_ns_) next_ns_ = now + period_ns_;  // fell behind: restart the grid here
    return true;
  }

  double period_ns() const { return period_ns_; }

private:
  double period_ns_ = 1.0e9 / 60.0;
  double next_ns_ = 0.0;
  bool started_ = false;
};

} // namespace openspm::turbo
