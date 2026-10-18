# M14 runtime 181358: synchronous GX draw hotpath proof

Player log: `launch(20260913-181358).log`

Tested repo revision: `dda366ec17866b3350ec593691e5544eee001727`

This run answers the main performance question left by the 174626 frontier. The remaining heavy-scene slowdown is not primarily the old raw guest-array prefix copy. A large fraction of wall time is now measurably spent inside Aurora's synchronous `handle_draw -> draw_prim` path itself, with additional DrawDone/readback boundary cost on top.

## Runtime state retained

- backend: `openspm.native 14.0-dev26`;
- renderer: `openspm.aurora-gx 14.0-dev6`;
- local native module: v19 / `m14-dev19`;
- no emulator fallback;
- safe synchronous Aurora `ProcessingMode::Drain` retained;
- continuation-safe GX display-list context repair retained;
- compact raw guest-array interval staging active;
- source-backed 608x480 -> 304x240 flip feedback copy active.

The player still reports the same major lag, a black square during flip, and missing door animations. The previously regressed rotating/spinning-device Mario rendering remains correct after the unsafe async worker rollback.

## Compact array staging is no longer the leading bottleneck

The compact interval marker fired:

```text
M14 raw guest-array compact interval staging active begin=984 end=1032 bytes=48 prefix_saved=984
```

The old `M14 raw guest-array stage profile` high-water never fired during this run. That means the previous >7 MiB/frame prefix-copy threshold was not reached. Keep compact intervals as a valid exact optimization, but do not spend the next iteration trying to squeeze more out of the old prefix-volume diagnosis.

## Coarse draw profiler result

The run produced 6,289 complete 2,048-draw windows, i.e. roughly 12.9 million raw GX primitive commands measured by the temporary profiler.

Across those windows:

- median draw-path CPU per 2,048 commands: ~9.3 ms;
- 90th percentile: ~39.4 ms;
- 95th percentile: ~50.7 ms;
- 99th percentile: ~70.8 ms;
- worst complete window: ~244.8 ms.

The slowest *single* draw inside a window was also important:

- median window max: ~3.3 ms;
- 90th percentile window max: ~22.4 ms;
- 95th percentile: ~31.3 ms;
- 99th percentile: ~40.5 ms;
- observed maximum: ~70.7 ms.

So there are two costs, not one:

1. very high raw primitive volume in complex scenes; and
2. occasional individual draw preparations that take tens of milliseconds.

The latter can dominate a whole aggregate window and strongly suggests a conditional pipeline/texture/bind/uniform/cache operation rather than simple vertex-byte copying alone.

## DrawDone is where synchronous work is forced, not necessarily the root operation

Representative `draw_done_drain` stalls include ~117 ms, ~121 ms, ~130 ms and a peak of ~349 ms. Nearby 2,048-draw windows simultaneously report tens to hundreds of milliseconds of CPU inside `draw_prim`.

This means DrawDone is functioning as the synchronous boundary that forces accumulated parser/draw preparation work. Do not classify the entire stall as a generic GPU `DrawDone` wait without breaking down the CPU draw path first.

## Timing split is confirmed

Early/light scenes still deliver roughly 56-58 retraces per real second while the host-derived 60.75 MHz Broadway timebase tracks ~1 real second per ~1 guest second.

In the heavy scene, the same healthy wall-clock timebase continues advancing while delivered retraces repeatedly collapse to roughly 18-25/s, with some samples around 17/s. This directly matches the player's visible desynchronization:

- enemies/camera/flip timer are retrace/frame-driven and slow;
- Mario movement does not slow proportionally;
- Mario therefore appears too fast and can outrun the camera.

Do not compensate by changing the Broadway timebase or game speed. Fix renderer CPU throughput and eventually decouple correct VI/retrace delivery from renderer stalls without repeating the unsafe mutable-guest-memory worker design.

## Flip black square remains narrow

The first logged Wii-A edge (`0x00000800`) is followed immediately by the expected source-backed raw GX half-scale texture-copy marker:

```text
src=608x480 dst=304x240 fmt=4 stride_tiles=76
```

The player still sees a black square. Therefore half-scale copy dimensions are confirmed active but are not sufficient to fix the effect. Keep the half-scale semantic. `GX_LO_OR` remains fidelity debt, but its startup/early warnings are not enough evidence to name it the flip root cause.

## New candidate after this proof

`renderer/aurora/apply_draw_hotpath_breakdown_patch.py` is now wired after the coarse profiler migration.

It does two things:

1. exact fast path: skip `guest_array_requirements()` when no INDEX8/INDEX16 attribute is backed by raw guest memory. Such draws necessarily have an all-zero guest requirement set, so semantics are unchanged;
2. replace the noisy 2,048-draw profiler with one 65,536-draw breakdown containing total/max draw CPU plus time/count for guest-index scan, vertex push, indexed-array staging, pipeline rebuild, texture/bind rebuild, uniform rebuild, and merged/unmerged counts.

Expected marker:

```text
M14 GX hotpath profile calls=... window=65536 total_us=... avg_us=... max_us=... scan_us=... scan_calls=... verts_us=... arrays_us=... pipe_us=... pipe_calls=... bind_us=... bind_calls=... uniform_us=... uniform_calls=... merged=... unmerged=...
```

This should identify whether the tens-of-ms outliers are primarily pipeline lookup/build, texture resolution/bind reconstruction, uniform construction, raw-array staging, or an unmeasured remainder. It also cuts profiler log volume by 32x.

## Next player test

Run `M14-UPDATE-RUN.cmd` without wiping `.openspm` or saves. Reach the same enemy-heavy scene and send the launch log. Visual expectations are deliberately unchanged: v19 graphics and rotating devices must stay correct; flip black square and door animation are still open. The next optimization should be selected from the new hotpath category totals rather than another speculative renderer-wide behavior change.
