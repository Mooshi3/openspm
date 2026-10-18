# M14 runtime 044537 — producer localization and direct WPAR CPU fastpath candidate

Date: 2026-09-14

## Known-good correctness baseline

Runtime `launch(20260914-044537).log` tested repo `2135c0e488ca...` after the unsafe render-worker pacing probe had been removed. The player had already confirmed rotating devices and the door were correct again on the preceding clean rollback build. This runtime reached the heavy gameplay areas without the old spinner/door regression.

KEEP the protected v19 graphics/display-list work, exact guest-array snapshots, fixed-phase VI scheduler, synchronous Aurora FIFO Drain mode, frame-boundary host-event handling, and the real swapchain-present pacing probe.

Do NOT restore the no-guest-index array hotpath, async FIFO worker, 32 KiB parser-progress experiment, or render-worker callback timing wrapper.

## What 044537 proved

The low-overhead producer sync wrapper finally measured costs that the old >16 ms slow-only logger hid.

### DrawDone parser drain scales with the scenes that visibly stutter

Opening / light work:

- total 256: avg 0.119 ms, p95 0.250 ms
- 512: avg 0.104 ms
- 1024: avg 0.117 ms
- 2048: avg 0.143 ms

Gameplay ramp:

- 3328: avg 1.385 ms, p95 3.250 ms
- 3584: avg 2.695 ms, p95 3.500 ms
- 3840: avg 2.862 ms, p95 3.750 ms
- 4096: avg 2.836 ms, p95 3.750 ms

Heavy stretch:

- 7680: avg 3.984 ms, p95 5.000 ms, p99 6.500 ms
- 7936: avg 4.834 ms, p95 6.000 ms, p99 7.250 ms
- 8192: avg 4.493 ms, p95 6.000 ms
- 8448: avg 4.202 ms, p95 5.250 ms
- 8960: avg 4.193 ms, p95 5.250 ms
- 9216: avg 2.898 ms, p95 4.250 ms

The scheduler-idle presentation sync itself remains effectively free in the same heavy region (roughly 7–9 us average). Therefore synchronous GX parsing at DrawDone is a real contributor, but not the entire 30–35 ms visible frame.

### Real desktop presentation follows the same load curve

- 2048 presents: p95 19.0 ms, p99 20.5 ms
- 4096: p95 19.5 ms, p99 30.5 ms
- 8192: p95 33.5 ms, p99 35.5 ms; 1206 frames >25 ms, 490 >33 ms

The actual WebGPU/D3D12 `Present()` call remains only about 0.2 ms average. The late frames are created upstream before swapchain presentation.

### Bbox is not the primary bottleneck

By 8189 bbox reads / 2048 real maps:

- submit: 153 us
- map: 72 us
- slow=0

Retire bbox as the primary explanation for the persistent scene-dependent slowdown.

### Broadway write-gather traffic explodes in gameplay

`wg_writes` counts original Broadway stores entering the 32-byte write-gather stream, not 32-byte FIFO lines. In the smooth opening the counter advances only around ~69k stores/s. In `seq=2 stage=1`, one-second heartbeat deltas are commonly about 2–4 million stores/s, with cumulative traffic reaching hundreds of millions in this run.

The existing dev16 optimization already gathers those stores into exact 32-byte chunks before crossing the module/backend GX FIFO service. However stock DolRecomp `mem_write8/16/32/64` still does generic `resolve_addr()` and an indirect `CPUState::external_write` call for every WPAR store before the dev16 gather code runs.

That is a remaining migration-bridge tax proportional to scene draw traffic and has the right shape to explain part of the gap before DrawDone.

## Candidate: direct staged-CPU WPAR hook

The candidate intentionally changes no GX byte stream or renderer semantics.

Files:

- `tools/native_bridge/stage_cpu_wg_fastpath.py`
- `tools/native_bridge/refresh_staged_cpu_runtime.py`
- `tools/native_bridge/build_local_module.py`
- `tools/native_bridge/legacy_c_adapter_v12.c.in`
- `M14-UPDATE-RUN.cmd`
- `tools/native_bridge/test_cpu_wg_fastpath.py`

### Runtime behavior

The player-local build now compiles a private staged copy of DolRecomp `cpu/` + `common/`. The original local compiler checkout is never modified.

Only `mem_write8/16/32/64` in the staged `cpu.c` gain one exact precheck for `0xCC008000..0xCC00801F`. A matching store directly calls `openspm_m14_try_wg_write()` before generic `resolve_addr()`.

`openspm_m14_try_wg_write()` lives in the active v12 bridge translation unit and reuses dev16's exact existing static state:

- `g_m14_v8_wg_bytes`
- `g_m14_v8_wg_size`
- `g_m14_v8_wg_pending_stores`
- `bridge_flush_wg_v8()`
- same GX FIFO service
- same failure/fallback behavior

The helper appends 1/2/4/8-byte stores in the same PPC big-endian order and flushes at the same 32-byte boundary. Partial-line flushes before external access, ordering operations, hosted semantics, presentation, and teardown remain owned by the proven dev16 code.

If the bulk GX FIFO service is unavailable, the helper returns unhandled and DolRecomp follows its original generic path.

Expected runtime marker:

`M14 staged DolRecomp CPU WPAR direct fast path active; exact 32-byte GX gather/order preserved.`

### Incremental-cache migration

Normal `M14-UPDATE-RUN.cmd` does not recreate the local module from scratch. It now:

1. reads the existing `OPENSPM_CPU_RUNTIME_DIR` from the local module CMake cache,
2. remembers the pristine source runtime path in `stage/cpu_runtime_origin.txt`,
3. refreshes a private `stage/cpu_runtime` copy,
4. applies the exact WPAR transform there,
5. reconfigures the existing local module tree to compile against the staged copy,
6. incrementally rebuilds as usual.

Fresh `build_local_module.py` builds also use the private stage and create the same origin marker.

The source transform fails closed if the selected DolRecomp runtime does not contain the expected four write-helper anchors.

## A/B expectations

This candidate deliberately does NOT change Aurora parsing, so DrawDone timings should remain roughly comparable to 044537. That is useful.

If generic per-store CPU overhead is material:

- spinner/door correctness remains unchanged,
- marker above is present,
- host-present p95/p99 improves in the gameplay/heavy regions,
- phase-lock expiration may decrease,
- DrawDone parser cost stays similar because FIFO bytes are identical.

If host presentation does not materially improve, the next highest-value frontier is the synchronous Aurora parser itself. 044537 already proves it consumes ~4–5 ms/frame average in the heaviest region, so optimizing parser/draw construction without changing boundaries/lifetimes would then be justified.

## Rollback trigger

If Mario becomes invisible/smeared on rotating devices, a door softlocks, or any new geometry/texture corruption appears, immediately rollback only this WPAR direct-CPU candidate. Keep v19 graphics, phase-lock, dev7 event/shutdown fixes, and the safe producer sync/present probes.
