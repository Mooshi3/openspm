// OpenSPM M14 present timeline presenter (Stage A). Embedded into Aurora's
// lib/aurora.cpp by apply_present_timeline_patch.py, after the dev139 async
// presenter state, together with present_timeline.hpp.
//
// Opt-in: OPENSPM_M14_ASYNC_PRESENTER=1 and OPENSPM_M14_PRESENT_TIMELINE=1
// (OPENSPM_M14_PRESENT_TIMELINE_MODE=vanilla for the alpha-0 cadence).
// Otherwise nothing here runs and the dev139 presenter / Vanilla 60 paths are
// byte-for-byte what they were.
//
// Thread model (unchanged from dev139): the presenter thread only schedules;
// Aurora's render worker owns every WebGPU call. The presenter wakes one guard
// before the predicted vblank, selects via the timeline under
// g_m14PresenterMutex and enqueues one present. The worker reports the time
// its FIFO Present() returned; that blocking is the vblank clock.
//
// Stage A shows the frame the timeline selects: the nearer in time of the
// interpolation pair (A while alpha < 0.5, then B), or A in vanilla mode.
// With Stage C on (OPENSPM_M14_INTERP=1, frame_interp_presenter.inl) the worker
// instead asks m14_frame_interp_choose() for the image of (A, B, alpha): an
// interpolated replay, A's or B's own image, and B whenever a replay is not
// safe. Stage C also keeps a guard that covers its measured replay cost.
bool m14_present_timeline_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_PRESENT_TIMELINE");
    return m14_async_presenter_enabled() && value != nullptr && value[0] == '1' && value[1] == '\0';
  }();
  return enabled;
}

uint32_t m14_presenter_ring_slots() noexcept {
  // The timeline may still need the pair before the newest publish, so it
  // retains four images; dev139 keeps its original two-slot ring.
  return m14_present_timeline_enabled() ? 4u : 2u;
}

struct M14PresentTimelineState {
  openspm::timeline::PresentTimeline timeline;
  bool configured = false;
  std::array<uint64_t, 4> slotFrame{};  // publish generation held by each slot
  // once-per-second histogram window
  std::chrono::steady_clock::time_point windowStart{};
  std::array<uint64_t, 10> alphaBins{};
  std::array<uint64_t, 5> holdBins{};  // frames held 1, 2, 3, 4, 5+ refreshes
  uint64_t presents = 0, framesIn = 0, busy = 0, evicted = 0, dropped = 0;
  uint64_t lastShow = 0, holdRun = 0;
  openspm::timeline::Stats statsAtWindow{};
};
M14PresentTimelineState g_m14Timeline;

double m14_timeline_now_ns() noexcept {
  return static_cast<double>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                 std::chrono::steady_clock::now().time_since_epoch())
                                 .count());
}

// Reads the renderer's published display rational (OPENSPM_M14_DISPLAY_REFRESH
// = "numerator/denominator", exact SDL mode rate) and the locked game period.
openspm::timeline::Config m14_timeline_config() noexcept {
  openspm::timeline::Config config;
  if (const char* refresh = std::getenv("OPENSPM_M14_DISPLAY_REFRESH"); refresh != nullptr) {
    char* end = nullptr;
    const unsigned long long numerator = std::strtoull(refresh, &end, 10);
    unsigned long long denominator = 1ull;
    if (end != nullptr && *end == '/') denominator = std::strtoull(end + 1, nullptr, 10);
    if (numerator != 0ull && denominator != 0ull) {
      config.refresh_numerator = numerator;
      config.refresh_denominator = denominator;
    }
  }
  if (const char* period = std::getenv("OPENSPM_M14_RETRACE_PERIOD_NS"); period != nullptr) {
    const unsigned long long ns = std::strtoull(period, nullptr, 10);
    if (ns >= 16000000ull && ns <= 17500000ull) config.game_period_ns = static_cast<double>(ns);
  }
  const char* mode = std::getenv("OPENSPM_M14_PRESENT_TIMELINE_MODE");
  config.mode = mode != nullptr && std::string_view{mode} == "vanilla" ? openspm::timeline::Mode::Vanilla
                                                                      : openspm::timeline::Mode::Smooth;
  if (m14_frame_interp_enabled()) {  // Stage C: lead time for the replay
    config.guard_ns = m14_frame_interp_guard_ns(
        openspm::timeline::refresh_period_ns(config.refresh_numerator, config.refresh_denominator));
  }
  return config;
}

// Producer side: called from m14_publish_presenter_slot under g_m14PresenterMutex.
void m14_timeline_note_frame_locked(uint64_t generation, uint32_t slot) {
  if (!m14_present_timeline_enabled()) return;
  auto& state = g_m14Timeline;
  if (!state.configured || generation == 1u) {  // first publish (again after a stop)
    state.timeline.configure(m14_timeline_config());
    state.configured = true;
    state.slotFrame = {};
    state.windowStart = std::chrono::steady_clock::now();
    state.statsAtWindow = {};
  }
  if (slot < state.slotFrame.size()) state.slotFrame[slot] = generation;
  state.timeline.on_frame(generation, m14_timeline_now_ns());
  ++state.framesIn;
}

void m14_timeline_log_window_locked(std::chrono::steady_clock::time_point now) {
  auto& state = g_m14Timeline;
  const auto& tl = state.timeline;
  const auto& stats = tl.stats();
  const auto& base = state.statsAtWindow;
  const auto& cfg = tl.config();
  const auto& a = state.alphaBins;
  const auto& h = state.holdBins;
  Log.info("M14 present timeline: mode={} display_hz={:.3f} game_hz={:.4f} locked_k={} presents={} frames={} "
           "hold[1,2,3,4,5+]={},{},{},{},{} alpha_would_use[0.0..0.9]={},{},{},{},{},{},{},{},{},{} "
           "late={} snaps={} reanchors={} recentres={} missed_vblanks={} busy_skips={} evicted={} dropped={} "
           "delay_ms={:.2f} margin_ms={:.2f} guard_ms={:.2f}",
           cfg.mode == openspm::timeline::Mode::Vanilla ? "vanilla" : "smooth", 1.0e9 / tl.display_period_ns(),
           1.0e9 / tl.game_period_ns(), tl.locked_multiple(), state.presents, state.framesIn, h[0], h[1], h[2],
           h[3], h[4], a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], stats.late - base.late,
           stats.snaps - base.snaps, stats.reanchors - base.reanchors,
           stats.phase_recenters - base.phase_recenters, stats.missed_vblanks - base.missed_vblanks,
           state.busy, state.evicted, state.dropped, tl.delay_frames() * tl.game_period_ns() / 1.0e6,
           tl.margin_ns() / 1.0e6, tl.guard_ns() / 1.0e6);
  state.alphaBins = {};
  state.holdBins = {};
  state.presents = state.framesIn = state.busy = state.evicted = state.dropped = 0;
  state.statsAtWindow = stats;
  state.windowStart = now;
  if (m14_frame_interp_enabled()) state.timeline.set_guard_ns(m14_frame_interp_guard_ns(tl.display_period_ns()));
}

void m14_timeline_presenter_main() {
  using Clock = std::chrono::steady_clock;
  {
    std::lock_guard lock{g_m14PresenterMutex};
    if (!g_m14Timeline.configured) {
      g_m14Timeline.timeline.configure(m14_timeline_config());
      g_m14Timeline.configured = true;
      g_m14Timeline.windowStart = Clock::now();
    }
    const auto& tl = g_m14Timeline.timeline;
    const auto& cfg = tl.config();
    Log.info("M14 present timeline active: mode={} display={}/{} ({:.3f} Hz) game_period_ns={:.0f} locked_k={} "
             "guard_ms={:.2f} ring=4 clock=fifo-present-return stage={} "
             "webgpu_thread=render-worker",
             cfg.mode == openspm::timeline::Mode::Vanilla ? "vanilla" : "smooth", cfg.refresh_numerator,
             cfg.refresh_denominator,
             static_cast<double>(cfg.refresh_numerator) / static_cast<double>(cfg.refresh_denominator),
             cfg.game_period_ns, tl.locked_multiple(), tl.guard_ns() / 1.0e6,
             m14_frame_interp_enabled() ? "C(interpolated image per vblank, fallback B)"
                                        : "A(show nearest, alpha logged)");
    if (std::getenv("OPENSPM_M14_DISPLAY_REFRESH") == nullptr)
      Log.warn("M14 present timeline: display refresh unknown (OPENSPM_M14_DISPLAY_REFRESH unset); assuming 60 Hz");
  }

  while (true) {
    {
      std::unique_lock lock{g_m14PresenterMutex};
      g_m14PresenterCv.wait(lock, [] { return g_m14PresenterStop || g_m14PresenterHaveImage; });
      if (g_m14PresenterStop) break;
    }

    double wakeNs = 0.0;
    {
      std::lock_guard lock{g_m14PresenterMutex};
      wakeNs = g_m14Timeline.timeline.next_wake_ns(m14_timeline_now_ns());
    }
    // Coarse sleep, then yield for the last millisecond: the guard covers
    // only ~1 ms of wake-up lateness at 240 Hz.
    const auto wake = Clock::time_point{std::chrono::duration_cast<Clock::duration>(
        std::chrono::nanoseconds{static_cast<int64_t>(wakeNs)})};
    if (wake - Clock::now() > std::chrono::milliseconds{1}) {
      std::this_thread::sleep_until(wake - std::chrono::milliseconds{1});
    }
    while (Clock::now() < wake) std::this_thread::yield();

    bool queue = false;
    uint64_t show = 0, frameA = 0, frameB = 0;
    double alpha = 0.0;
    {
      std::lock_guard lock{g_m14PresenterMutex};
      if (g_m14PresenterStop) break;
      auto& state = g_m14Timeline;
      if (g_m14PresenterWorkQueued) {
        // The previous present has not returned yet (worker busy): this vblank
        // keeps the old image. Never queue a second present behind it.
        ++state.busy;
      } else {
        const auto sel = state.timeline.select(m14_timeline_now_ns());
        if (sel.valid) {
          show = sel.show;
          frameA = sel.frame_a;
          frameB = sel.frame_b;
          alpha = sel.alpha;
          queue = true;
          g_m14PresenterWorkQueued = true;
          ++state.presents;
          const auto bin = std::min<size_t>(static_cast<size_t>(sel.alpha * 10.0), 9u);
          ++state.alphaBins[bin];
          if (sel.show == state.lastShow) {
            ++state.holdRun;
          } else {
            if (state.lastShow != 0u && state.holdRun != 0u)
              ++state.holdBins[std::min<uint64_t>(state.holdRun, 5u) - 1u];
            state.lastShow = sel.show;
            state.holdRun = 1;
          }
        } else {
          state.timeline.on_present_dropped();
        }
      }
      const auto now = Clock::now();
      if (now - state.windowStart >= std::chrono::seconds{1}) m14_timeline_log_window_locked(now);
    }

    if (!queue) {
      std::this_thread::sleep_for(std::chrono::microseconds{500});
      continue;
    }

    gfx::render_worker::enqueue_work([show, frameA, frameB, alpha] {
      // A window resize reconfigures the swapchain on the main thread; SDL's
      // SurfaceLock is a no-op on Windows, so without this the acquire ->
      // replay -> Present below could interleave with Configure() and crash
      // (apply_presenter_surface_lock_patch.py holds it across the resize).
      std::lock_guard surfaceGuard{webgpu::g_m14SurfaceMutex};
      M14PresenterImage image, imageA, imageB;
      bool haveB = false;
      const bool interp = m14_frame_interp_enabled();
      {
        std::lock_guard lock{g_m14PresenterMutex};
        // (bbox readback priority: never present over a pending CPU readback)
        const bool stale = g_m14PresenterStop || !g_m14PresenterHaveImage;
        if (stale || gx::bbox::readback_pending()) {
          g_m14Timeline.timeline.on_present_dropped();
          ++g_m14Timeline.dropped;
          g_m14PresenterWorkQueued = false;
          return;
        }
        // Resolve the slot on the worker: publishes are ordered with this
        // work, so the lookup sees exactly the images that exist now.
        uint32_t slot = g_m14PresenterLatestSlot;
        bool found = false;
        for (uint32_t i = 0; i < m14_presenter_ring_slots(); ++i) {
          if (g_m14Timeline.slotFrame[i] == show) {
            slot = i;
            found = true;
            break;
          }
        }
        if (!found) ++g_m14Timeline.evicted;
        image = g_m14PresenterImages[slot];
        if (interp) {  // Stage C needs both images of the pair
          for (uint32_t i = 0; i < m14_presenter_ring_slots(); ++i) {
            if (g_m14Timeline.slotFrame[i] == frameA) imageA = g_m14PresenterImages[i];
            if (g_m14Timeline.slotFrame[i] == frameB) {
              imageB = g_m14PresenterImages[i];
              haveB = true;
            }
          }
        }
      }
      if (interp && haveB) image = m14_frame_interp_choose(frameA, frameB, alpha, imageA, imageB);

      const bool presented = m14_present_retained_image(image);
      const double returnedNs = m14_timeline_now_ns();

      std::lock_guard lock{g_m14PresenterMutex};
      if (presented) {
        g_m14Timeline.timeline.on_present(returnedNs);
      } else {
        g_m14Timeline.timeline.on_present_dropped();
        ++g_m14Timeline.dropped;
      }
      g_m14PresenterWorkQueued = false;
    });
  }
}
