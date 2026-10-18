# M14 runtime 005106: single-copy snapshot comparison + handoff

Player log: `launch(20260914-005106).log`

Repo tested: `1c7e2aeeee17cd95192e03397a55c44402bb8217`

Comparison run: `launch(20260914-003347).log` at `5d24d64880baf26b56155f7a1dedf466da6d39b0` (pooled exact snapshots, before the single-copy append successor).

## What changed

The 005106 run includes the exact-snapshot pool plus `apply_guest_array_snapshot_single_copy_patch.py` (`ef740219389665aac4d24bbb077306e9b6b81779`, wired at `778843e348f499502a5abfbb9321a436da6a884c`).

Runtime marker:

```text
M14 raw guest-array pooled exact snapshots single-copy append active; cache semantics remain byte-exact
```

The migration only changes the append from `vector::resize(...) + memcpy(...)` to a single `vector::insert(...)`. Compact intervals, exact `memcmp`, pointer-index lookup, guest mutation visibility, `gfx::push_storage` bytes/ranges and v19 GX semantics are unchanged.

No user subjective verdict was supplied for the 005106 run before the chat hit the message limit, so visual/animation correctness must not be inferred from the log alone.

## Comparison methodology

003347 contains 8,293 complete 2,048-draw CPU windows and 159 one-second timing samples.

005106 is much longer: 29,161 complete 2,048-draw windows and 464 timing samples. Therefore whole-log maxima and DrawDone samples are not scene-aligned. In addition to whole-log statistics, compare the first 16,984,064 draw calls of 005106, matching the total profiled draw-call span of 003347 (8,293 windows each). This is still only a rough route alignment, not a frame-perfect A/B.

## 2,048-draw CPU windows

### Whole log

003347 pooled snapshots -> 005106 single-copy:

- mean: 4.676 ms -> 3.856 ms (**17.5% lower**)
- median: 3.932 ms -> 3.715 ms (**5.5% lower**)
- p90: 8.310 ms -> 4.678 ms (**43.7% lower**)
- p95: 9.686 ms -> 5.512 ms (**43.1% lower**)
- p99: 11.491 ms -> 9.498 ms (**17.3% lower**)
- worst complete window: 49.232 ms -> 62.463 ms (worse single outlier, but 005106 is ~3x longer)

### Call-count-matched prefix (8,293 windows each)

- mean: 4.676 ms -> 4.059 ms (**13.2% lower**)
- median: 3.932 ms -> 3.827 ms (**2.7% lower**)
- p90: 8.310 ms -> 5.254 ms (**36.8% lower**)
- p95: 9.686 ms -> 6.175 ms (**36.2% lower**)
- p99: 11.491 ms -> 8.678 ms (**24.5% lower**)
- worst complete window: 49.232 ms -> 62.463 ms

The distribution, especially the upper-middle tail, is directionally better. Because the route is not frame-perfect and the change itself is very small, do not attribute every percentile change solely to the single-copy append.

## Worst individual draw inside each 2,048-draw window

The call-count-matched tail improves more strongly:

- median: 0.896 ms -> 0.873 ms
- p90: 5.347 ms -> 1.879 ms (**64.9% lower**)
- p95: 6.338 ms -> 2.241 ms (**64.6% lower**)
- p99: 7.732 ms -> 4.021 ms (**48.0% lower**)
- worst observed single draw: 36.306 ms -> 21.168 ms (**41.7% lower**)

This is useful evidence that the single-copy successor is not obviously harmful to the measured draw-preparation path. Keep it provisionally unless player-visible correctness says otherwise.

## End-to-end timing did NOT show a second dramatic win

Retrace delivery is basically flat between these two already-optimized builds.

Whole-log timing:

- median: 52/s -> 51/s
- mean: 52.17/s -> 51.82/s
- worst one-second sample: 36/s -> 39/s (better floor)

Call-count-matched prefix:

- median: 52/s -> 52/s
- mean: 52.17/s -> 51.70/s
- worst sample: 36/s -> 39/s

The 60.75 MHz Broadway timebase remains healthy. This means the single-copy append may reduce draw CPU tails but does **not** by itself close the remaining real-time/retrace gap.

The 12 sparse `draw_done_drain` samples are also not comparable enough to claim improvement: 005106's sampled median/worst are slightly higher. Their logging cadence and scene placement differ, and the worst samples in both runs occur around the same high-volume SEQ_LOAD audit/file-select diagnostic region.

## Diagnostic overhead is now part of the performance frontier

005106 contains **29,161** `M14 GX draw CPU profile` log lines. The old automatic deep GX audits also emit the same ~4,454 diagnostic/audit lines in both runs.

Now that the renderer hot path is much cheaper than it was, this logging is no longer free enough to ignore. Before another end-to-end performance verdict:

1. sparsify or disable the 2,048-draw coarse profiler in normal runs (for example, report first few + powers-of-two / a much larger interval);
2. retire the automatic stage-7/SEQ_LOAD deep audit triggers and keep them manual/opt-in;
3. do not bundle either diagnostic cleanup with a semantic GX optimization.

## Exact next guest-array optimization: reuse Aurora's own storage copy as the immutable snapshot

The current exact cache still copies each miss twice:

1. `gfx::push_storage(source, uploadSize)` appends the guest bytes into the current frame's Aurora `FramePacket::storage` `ByteBuffer`;
2. the separate snapshot pool appends the same guest bytes again so later same-pointer requests can exact-`memcmp` against an immutable copy.

Aurora's pinned `ByteBuffer` is append-only for this path. Its `append()` grows via `realloc`, preserves earlier bytes, and old storage ranges are addressed by offset. Therefore the frame storage buffer already contains the immutable byte copy needed for exact validation.

High-value exact design for the next session:

- expose a narrow internal `gfx` helper such as `storage_range_equal(gfx::Range range, const u8* source, u32 size)` (or equivalent);
- resolve the current `FramePacket::storage.data()` at compare time using the stored range offset; **never retain a raw storage pointer across later appends/reallocations**;
- on a cache hit candidate, exact-`memcmp` the guest source against the bytes already stored at `range.offset`;
- remove the separate snapshot byte pool entirely if this is proven safe;
- keep pointer-index lookup, stride checks, compact interval semantics and exact same-address mutation detection unchanged.

This would remove one complete host copy per staging miss plus the separate snapshot pool, without weakening coherency. Treat it as runtime-unproven until Windows player A/B.

## Reproducible later crash: separate correctness frontier

Both 003347 and 005106 terminate at the same final fatal:

```text
Failed to map CP array base 0x003C7FA0 for attr 9
Failed to map CP array base 0x010AF5E4 for attr 9
Failed to map CP array base 0x010C52E4 for attr 10
Failed to map CP array base 0x010C94C4 for attr 11
Failed to map CP array base 0x010C59C4 for attr 13
GX_CMD_CALL_DL references unmapped guest memory: address=0x81349E80, size=224
```

The final fail-fast is therefore **not caused by the single-copy candidate**. The immediately preceding FIFO failure differs between the two runs (`-5` framer rejection in 003347 versus repeated renderer submit `-6` in 005106), but the same CP-base failures and same unmapped nested display list are the convergent fatal frontier.

Do not paper over `0x81349E80` with an address-specific mapping. Trace the upstream CP/DL state and the native migration boundary that precedes it. If this crash blocks normal progression, prioritize it ahead of further micro-optimization.

## Current correctness / polish frontiers to retain

Player-proven before this run:

- v19 systemic graphics remain the protected milestone;
- the rejected no-guest-index hotpath fast path is rolled back;
- Chapter 1-2 spinning devices are correct again after that rollback;
- doors no longer softlock after that rollback, but their visible open/close animation is still missing;
- flip can still turn Mario into a black square;
- UI-adjacent effects can flash the background black (item pickup / Pure Heart pillar were named examples);
- Flipside tower can be invisible briefly during camera fly-ins before it loads;
- audio is still silent;
- final input mapping/remapping is unfinished.

Do not infer that 005106 changed any of those without player observation.

## Suggested next-session order

1. Read this file, `M14-RESUME.md`, and `state/m14-runtime-frontier.json`.
2. Treat pooled exact snapshots as a proven major win; keep the single-copy successor provisionally.
3. First remove/sparsify measurement spam, then re-test the same enemy-heavy area for an honest end-to-end baseline.
4. If pursuing guest-array CPU next, use Aurora's already-staged frame-storage bytes as the exact immutable snapshot rather than adding a weaker cache heuristic.
5. If the `0x81349E80` nested-DL fatal blocks progression, switch priority to that reproducible crash.
6. Never restore the retired async FIFO worker without immutable/versioned guest resources, and never restore the rejected no-guest-index fast path.

No `.openspm` or save wipe is required.