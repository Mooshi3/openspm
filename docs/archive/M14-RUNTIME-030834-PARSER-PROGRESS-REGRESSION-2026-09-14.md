# M14 runtime 030834 — parser-progress regression and resize AV

## Tested player

- Log: `launch(20260914-030834).log`
- Repo tested: `5b60f351290305e59f7573c0e59673546e04a45a`
- Backend: `openspm.native 14.0-dev26`
- Renderer: `openspm.aurora-gx 14.0-dev7`
- FIFO correctness mode: synchronous Drain
- Protected v19 display-list bridge active
- Native retrace phase-lock active

## Player result

The player reported that formerly normal areas felt almost as laggy as the known laggy area. Resizing the window then crashed the process.

This is a failed performance experiment, not a candidate to tune further.

## Failed experiment: 32 KiB command-boundary parser progress

The candidate reused the backend's command-aware GX framer and emitted a parser-only sync after roughly 32 KiB of FIFO bytes, but only when the framer had returned to a proven complete-command boundary. Aurora handled that sync with synchronous `raw::drain()` on Broadway's thread. It did not present, recycle the frame, or wait for GPU completion, and it did not revive the retired async FIFO worker.

Despite those correctness constraints, the runtime produced far too many synchronous parser boundaries. The log reaches 16,384 parser-progress syncs during ordinary gameplay. Most individual drains remained below the 16 ms slow-log threshold, but the player's A/B result shows their aggregate/frequency cost made cheap scenes feel much closer to expensive ones.

Conclusion: **do not try to smooth DrawDone by inserting frequent synchronous parser drains.** Reducing peak work by redistributing it into thousands of small Broadway-thread stalls is not a win for this runtime.

The experiment was rolled back by:

- `cd795088ef2510fbc04f98cba0caa7693740c00e` — `M14: revert failed parser progress experiment`

That commit restores the exact pre-experiment tree from `106540eedba5b85269823898b7ac2b608414d763` while keeping the already-proven retrace phase-lock work.

## Resize crash

Near the end of 030834, the log records:

- Aurora `update-events` stall: about 161.7 ms
- matching outer renderer `begin_frame` / `fifo_submit` stall: about 161.8 ms
- immediately afterward, second-chance Windows access violation `0xC0000005`
- exception address `0x00007FFAD6947644`
- exception parameters indicate a read from address `0x9`

The old debug wrapper printed the raw exception but failed to capture module/RIP/stack information because `_capture_native_frontier()` was accidentally nested inside the `STATUS_STACK_BUFFER_OVERRUN`-only branch. Therefore **030834 does not prove that the parser-progress experiment caused the resize AV**. The temporal association is real, but ownership is unresolved.

Diagnostic fix:

- `238f04ad74fcafe3c71f3266bda94c93ad42e31c` — `M14: capture native frontier for access violations`

After that fix, any second-chance native exception should print the owning module, RIP/RSP/RBP and module-bearing stack candidates before Windows terminates the process. The debugger still returns exceptions with `DBG_EXCEPTION_NOT_HANDLED`; it does not intercept or recover from them.

## Resize/event ordering audit and candidate fix

Pinned Aurora `749d6ee7a22bdfab78c8ece9047bca5d79aa72ca` processes `SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED` synchronously inside `window::poll_events()`. That event immediately calls `webgpu::resize_swapchain()`, which synchronizes the render worker and then clears graphics caches, reconfigures the surface, and replaces framebuffer/resolved/depth textures.

Aurora's own simple example uses this ordering:

1. `aurora_update()`
2. `aurora_begin_frame()`
3. draw
4. `aurora_end_frame()`

OpenSPM's base bridge historically called `aurora_update()` before checking whether `state.frame_open` was already true, so a resize could rebuild Aurora window/GPU resources while OpenSPM intentionally kept a recording frame open across GXDrawDone. That ordering is outside the upstream usage contract and matches the 030834 crash timing closely enough to justify a narrow correction without claiming that the anonymous 030834 AV is proven to be Aurora-owned.

Candidate fix:

- `116b80b572e32638e3299d67cb0368c7e7fd6e35` — `M14: defer Aurora events until frame boundaries`

Dev7 now returns an empty Aurora event list while `g_renderer.frame_open` is true. Event processing resumes before the next host frame is opened; begin-frame retry/startup paths still pump normally. This keeps resize/surface mutation out of an active recording frame and also removes redundant SDL polling from live-frame FIFO submissions. GX bytes, parser boundaries, DrawDone, presentation, timebase, input latching and frame ownership are otherwise unchanged.

Expected marker:

```text
M14 Aurora host events deferred while a recording frame is open; resize/surface mutation runs only at host-frame boundaries.
```

Shutdown stats now distinguish real event pumps, cadence skips and frame deferrals.

Rollback condition: if this causes window-close/pause/startup responsiveness problems, revert only `116b80b...`; do not restore parser-progress.

## Performance direction after rollback

Do not add more parser boundaries. The next optimization pass should remove or amortize actual work while retaining current semantic boundaries. Useful measured buckets remain:

1. DrawDone parser CPU work.
2. PE bbox submit-for-readback render-worker submission cost.
3. PE bbox map/fence cost.
4. host event-pump stalls, especially resize.
5. display presentation / scheduler-idle work.
6. guest-array exact snapshot/copy work.

Prefer low-frequency aggregate timing over per-draw clocks. A remaining candidate is to eliminate duplicate guest-array snapshot copying by reusing already-staged `FramePacket::storage` bytes while preserving exact `memcmp`, pointer-index lookup and same-address mutation detection. Do not revive the rejected no-guest-index hotpath or async FIFO worker.
