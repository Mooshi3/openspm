# M14 runtime frontier — 2026-09-14 16:50:02

## Result

Player log: `launch(20260914-165002).log`

Tested repo: `b9ae9697a6d0`

Player report after the 8x native-slice A/B: **no subjective improvement**. The same three gameplay symptoms remain authoritative:

- Mario still looks jittery / flicks slightly backward and forward while moving;
- enemies and shells move too slowly (Mario can outrun a Koopa shell);
- the large spiky boulders roll slowly but appear to spawn at a roughly normal rate, so they bunch together.

This run finally measures the real SPM top-level game loop directly and proves that the heavy-scene slowdown is not merely presentation pacing.

## Real simulation cadence is collapsing

The player-local R8PE01 revision-2 generated source now counts the durable entry label for `spmarioMain` at `0x801A7AAC`. Seeky's public SPM decomp shows the real top-level loop as:

```c
DEMOBeforeRender();
spmarioDisp();
spmarioMain();
DEMODoneRender();
```

The old `fps=60` and frame-latched-input diagnostics were retrace-adjacent proxies, not a measurement of this loop.

### Light / early gameplay

The real loop repeatedly reaches ~60 updates/s with ~60 VI retraces/s.

Representative 165002 windows:

- `spmarioMain=60 retraces=60`
- `spmarioMain=61 retraces=61`

### Heavy gameplay

The real loop falls to approximately half rate while VI remains near real time:

- `spmarioMain=30 retraces=60`
- `spmarioMain=30 retraces=60`
- `spmarioMain=31 retraces=60`
- `spmarioMain=28 retraces=57`

This directly explains the player's symptoms. Movement/physics/NPC advancement that occurs once per real game update can run near 30 Hz while retrace/time-driven activity continues near 60 Hz. Slow boulders can therefore accumulate if their production/timing path is not throttled by the same top-level update cadence.

**Do not return to a presentation-only diagnosis.** Presentation still has pacing issues under load, but the game itself demonstrably stops completing 60 simulation frames per real second.

## The 2,000,000-cycle slice experiment is rejected

The tested build raised the backend's native-run soft budget from the bring-up baseline 250,000 guest cycles to 2,000,000.

This produced no subjective improvement and did not prevent the real loop from dropping to ~30 Hz. Therefore the original 250k cap is not the primary cause of this slowdown.

After 165002:

- restore `OPENSPM_NATIVE_SLICE_CYCLES` to 250,000;
- keep the low-rate slice telemetry for diagnosis;
- do not carry the 2M behavior experiment into subsequent A/Bs.

## Slice telemetry reveals work-per-frame growth, not merely boundary count

The backend currently labels every `OPENSPM_NATIVE_EXIT_SAFEPOINT` together, so the count includes both execution-budget/coarse safepoints and scheduler-idle returns. The raw count alone must not be interpreted as DLL/service cost.

However, the ratio is informative:

- light gameplay: roughly 512 native returns/s and ~60 real updates/s, or ~8.5 returns/update;
- bad ~30 Hz gameplay: roughly 256 returns/s and ~30 real updates/s, still ~8.5 returns/update.

The number of recurring boundaries per real game update is therefore broadly stable while the **translated guest work represented by each produced frame grows dramatically**.

Representative light gameplay slice samples are only a few million guest cycles per real update. In bad windows, aggregate guest-cycle charge implies roughly 10–15+ million translated cycles per produced update.

This makes the next question phase-local rather than scheduler-global: what part of SPM's top-level frame is consuming the extra work?

## Strong correlation with draw/display workload

The same bad windows show a huge increase in GX work.

Example around `total_frames=6912`:

- `draws=1223624` across a 256-frame profiling window — about **4,780 parsed draw primitive commands per game frame**;
- DrawDone producer-boundary average ~5.2 ms;
- p95 ~6.5 ms;
- p99 ~7.5 ms.

Earlier light gameplay around `total_frames=2304` is only ~76 parsed draws/frame, with DrawDone boundary ~0.15 ms average, while `spmarioMain` remains ~60 Hz.

The renderer-side DrawDone parser work is significant in the bad room but still does not by itself explain a ~33 ms real frame. The stronger candidate is **upstream translated Wii CPU work constructing and emitting the scene**.

## Natural narrowing: `spmarioDisp -> camDraw -> dispDraw`

Seeky's public source shows:

```c
void spmarioDisp()
{
    camDraw();
    g_bFirstSmartAlloc = false;
}
```

The public headers describe `camDraw()` as calling `dispDraw` for each camera, and `dispDraw(cameraId)` as running every display entry's callback.

Therefore, if the display phase dominates the next profile, the search space collapses to:

`spmarioDisp -> camDraw -> dispDraw -> scene/display callbacks -> GX emission`

Do not profile the entire game or start rewriting NPC logic first if that phase accounts for the missing frame time.

## Current diagnostic candidate: real top-level phase timing

The active generated-source transform now marks exact R8PE01 rev2 durable entries:

- `DEMOBeforeRender = 0x801A8A9C`
- `spmarioDisp = 0x801A7D1C`
- `spmarioMain = 0x801A7AAC`
- `DEMODoneRender = 0x801A8B64`

A thin `legacy_c_adapter_v13.c.in` wrapper sits above the unchanged v20 stack and performs four monotonic clock reads per produced game frame. It measures wall time between these real guest entry labels, so yields/resumes across native slices remain part of the correct phase duration.

Expected runtime line:

```text
M14 main-loop phases host_ms=... frames=... before_avg_us=... before_max_us=... disp_avg_us=... disp_max_us=... main_avg_us=... main_max_us=... done_avg_us=... done_max_us=... retraces=...
```

This probe does **not**:

- wrap Aurora render-worker callbacks;
- add GX drains/syncs;
- snapshot guest memory;
- change scheduler/retrace decisions;
- alter game state;
- change public module/backend/renderer ABI versions.

## Interpretation for the next run

### If `disp_avg_us` grows toward the missing ~16–30 ms

Treat render construction as the primary root cause. Go directly into `camDraw`/`dispDraw` and display-entry classes. Measure which callbacks or GX emission patterns explode with scene complexity. The end goal should be removing a structurally wrong native translation/service cost, not shaving microseconds from shaders.

### If `main_avg_us` dominates

The cost is actual SPM update logic. Subdivide `spmarioMain` into its major drivers (NPC/event/map/animation/etc.) using the same durable-entry timing strategy.

### If `done_avg_us` dominates

Investigate `DEMODoneRender` / Wii SDK frame-finalization / DrawDone / copy / wait semantics. Existing DrawDone profiler remains useful context, but do not assume its measured 3–7 ms is the whole phase.

### If `before_avg_us` dominates

Investigate `DEMOBeforeRender` and any VI/GX/pre-frame setup performed there.

### If all four measured phases are small but full frame is ~33 ms

The missing time is outside the four ordinary phase bodies, most likely scheduler-idle/retrace waiting or a phase-order gap. Then split the idle/retrace return reasons rather than changing game or renderer code.

## Current commits after 165002

- `b338b0b40fb2814af908d44fca907beb653ca3b0` — broaden staged instrumentation to all four real loop entries while retaining `spmarioMain` cadence.
- `9f2e8fec797b3fec048da224112e65046f336024` — add thin v13 main-loop phase profiler above unchanged v20.
- `df14be375252f49e14a1a177ef2a9b79538f0af6` — activate v13 wrapper in player-local CMake.
- `96289cd40abf5f53fc5753ebea998dce19c9b598` — retire 2M slice experiment and restore 250k baseline.

## Safety / rollback

If the phase diagnostic causes any spinner/door/rendering regression, switch `OPENSPM_ACTIVE_BRIDGE` back from `legacy_c_adapter_v13.c.in` to `legacy_c_adapter_v12.c.in` and keep the generated-source hook code dormant. Do not touch protected v19 display-list semantics.

Never restore:

- render-worker callback wrapping;
- parser-progress synchronous drains;
- async FIFO Thread mode;
- no-guest-index guest-array shortcut;
- 2M slice budget as a presumed performance fix.

## Next player test

Run normal `M14-UPDATE-RUN.cmd` with no cache/save wipe and no resize during the performance path. Traverse the same light first area, large bad area and later area. Upload the log.

Subjective observations remain useful, but the decisive lines are now `M14 main-loop phases ...` plus `M14 simulation cadence ...`.
