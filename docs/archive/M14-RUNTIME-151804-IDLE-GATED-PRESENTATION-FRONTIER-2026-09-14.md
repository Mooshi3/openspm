# M14 runtime 151804 — idle-gated presentation frontier

Date: 2026-09-14

Player log: `launch(20260914-151804).log`

Tested revision: `3fdf7d67890405a19106363f9e5d177d1783cf79`

Player report: still feels laggy at times. Priority is to finish the frame-delivery problem quickly so development can move to high-refresh presentation and mods.

## A/B result: inline MEM1/MEM2 + PSQ0 is rejected

The staged generated-chunk inline RAM/PSQ experiment is a performance regression relative to the prior LTCG-only build.

Current 151804 run:

- 1024 presents: p95 19.5 ms, p99 31 ms
- 2048 presents: p50 18.5 ms, p95 34 ms, p99 36 ms
- 4096 presents: p50 19.5 ms, p95 34 ms, p99 36 ms
- shutdown after 7097 presents: p50 30.5 ms, p95 36 ms, p99 45.5 ms
- actual swapchain Present remains cheap (~0.17–0.21 ms average)

Prior LTCG-only 145508 run:

- 2048 presents: p95 19 ms, p99 20 ms
- 4096 presents: p95 19 ms, p99 20 ms
- 8192 presents: p95 30.5 ms, p99 33 ms

Therefore the inline RAM/PSQ path moved the throughput cliff much earlier. It is retired. Restore the LTCG + exact WPAR staged runtime baseline.

## Renderer cost still does not explain visible cadence

At 2048 presents in 151804:

- DrawDone sync ~2.87 ms average, p95 ~3.75 ms
- DrawDone draw CPU ~2.62 ms/frame
- actual host Present call ~0.195 ms average

At 4096:

- DrawDone sync ~2.99 ms average, p95 ~4 ms
- draw CPU ~2.61 ms/frame
- Present call ~0.200 ms average

Even in later heavier windows DrawDone rises to ~5–6 ms, which is real but still does not explain a recurring ~30–36 ms host-frame cadence by itself.

## Architectural discovery: presentation is guest-idle gated

`tools/native_bridge/legacy_c_adapter_v8.c.in` currently presents only when all of these are true before a native run:

- the persistent CPU PC is exactly Revolution OS `SelectThread` idle (`0x8027551C`);
- the retrace tick differs from the last presented tick.

The comment says presentation occurs only when Nintendo's scheduler says there is no runnable guest work.

This is too strong a condition for host display delivery. A busy gameplay scene may continuously have runnable Wii threads even after its GPU command stream has already produced a complete `GXCopyDisp`. The VI retrace source can therefore remain at 60 Hz while the PC window waits tens of milliseconds for the guest scheduler to become idle.

This matches the measured signature better than the scalar-RAM theory:

- authoritative retraces remain ~60/s;
- DrawDone is only a few ms;
- present safepoint work itself is tiny;
- swapchain Present itself is tiny;
- nevertheless completed host frames arrive at ~30–36 ms tails under load.

A real Wii VI scanout is not gated on "all runnable Broadway threads are idle."

## New candidate: completed-copy presentation at DrawDone

Renderer wrapper `renderer/aurora/openspm_renderer_aurora_v6.cpp` now keeps the proven dev4 DrawDone drain, then performs one additional check:

1. run the exact existing batched DrawDone sync/parser drain;
2. call `aurora::gx::raw::take_display_copy()` **without another drain**;
3. if no original-Wii display copy is latched, do nothing;
4. if a display copy is latched, end/present that completed host frame immediately;
5. retain the old scheduler-idle PRESENT_SAFEPOINT path unchanged as fallback.

This is deliberately not the rejected old behavior of presenting every GXDrawDone. Intermediate DrawDone boundaries with no `GXCopyDisp` remain invisible.

Expected marker:

`M14 completed Wii display-copy presentation active: parsed GXCopyDisp frames present at DrawDone; scheduler-idle remains fallback.`

Sparse stats:

`M14 completed-copy presentation stats drawdone_presents=... drawdone_no_copy=... scheduler_safepoints=... total_display_copies=...`

## Interpretation

Best case:

- player-visible gameplay becomes consistently smooth;
- host-present p95/p99 stays near 16–20 ms in the busy room;
- DrawDone may rise slightly because completed-copy presentation is now included in its measured boundary;
- scheduler-idle safepoints mostly become no-op fallback checks.

If visuals regress with intermediate construction/flicker, rollback only this wrapper commit. Do not change protected v19 display-list semantics, guest-array lifetimes, raw FIFO ordering or the fixed 60 Hz simulation clock.

If cadence remains ~30–36 ms despite drawdone_presents rising every frame, the missing delay is upstream of the parsed display copy itself and the next step is direct per-simulation-frame CPU wall profiling / semantic GX HLE, not more presentation work.

## High-refresh target

The final target remains 60 Hz authoritative simulation plus decoupled/interpolated presentation at the user's display rate (e.g. ~200 Hz), not running Wii gameplay logic at 200 Hz. Stable prompt delivery of each authoritative 60 Hz frame is the prerequisite for interpolation and mod work such as character-independent flip, moonjump and superspeed.
