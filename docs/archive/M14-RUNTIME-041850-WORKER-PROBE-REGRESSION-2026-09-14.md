# M14 runtime 041850: render-worker pacing probe regression

Tested revision: `098abc27c610c495fca78395c648c0d3b5b4fecf`.
Known-good comparison revision: `fe8842f4b768ba2f36a8339b2e378ab78ad5c7cf`.

## Player-visible regression

The first Windows run containing `apply_render_worker_pacing_diag_patch.py` regressed two correctness behaviors that were working in the immediately previous run:

- Mario becomes invisible again on Chapter 1-2 spinning/rotating devices.
- Door interaction softlock returns, preventing progression to the heavy-area performance test.

Because `fe8842` was good and the only runtime-affecting change between `fe8842` and `098abc` was the render-worker pacing instrumentation/wiring, treat the probe as behavior-changing and rejected. Do not attribute these two regressions to Astra's debug-heap change or to the old no-guest-index optimization without new evidence.

## Useful measurement retained from the rejected probe

The short 041850 run was already enough to answer the intended worker-vs-producer split. At the 512-frame checkpoint:

- producer average interval: ~18.467 ms;
- producer p95: ~19.0 ms;
- producer p99: ~20.5 ms;
- render-worker queue average: ~42 us;
- queue p95/p99: <=0.5 ms;
- end-frame worker average: ~0.488 ms;
- worker p95: ~1.0 ms;
- worker p99: ~1.5 ms;
- queue depth max: 3.

Conclusion: ordinary scene-dependent host-present lateness is produced upstream of the render worker. The worker queue and end-frame callback are not the leading throughput bottleneck in the measured light/early-game region.

## Rollback

Commit `f7ad1d3561f10625f23cbbd7e675ed918b5d7656` replaces the pacing migration with an exact retirement migration. During normal configure it detects the cached `kM14WorkerPacingBinUs` instrumentation and restores `lib/gfx/render_worker.cpp` from the pinned Aurora Git revision. Fresh/already-restored checkouts are verified and left alone.

The real swapchain-present pacing probe remains active because it did not regress gameplay in the known-good `fe8842` run and produced the useful scene-complexity scaling evidence.

## Next performance frontier

Do not re-instrument `render_worker::enqueue_end_frame()` or wrap worker callbacks again. The next profiling should stay on the producer/Broadway/GX-construction side and should be frame-aggregate or otherwise proven behavior-neutral.

Keep these established exclusions:

- actual `Surface::Present()` is cheap (~0.2 ms average in 041041);
- frame-slot/staging-slot/staging-map waits are effectively zero;
- worker queue/end-frame work are tiny in 041850;
- debug heap is not the main choppiness cause;
- scene complexity strongly worsens visible present cadence while Wii retrace delivery can remain at 60/s.

Acceptance test after rollback: rotating devices keep Mario visible and doors transition normally before resuming heavy-area performance work.