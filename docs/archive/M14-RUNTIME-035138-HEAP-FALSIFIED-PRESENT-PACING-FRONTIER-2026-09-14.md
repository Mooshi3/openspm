# M14 runtime 035138 — normal heap is active; presentation pacing is now the missing measurement

## Tested player

- Log: `launch(20260914-035138).log`
- Repo tested: `034f2a0267fa0c52c3cb26339a30928edca3c82a`
- Backend: `openspm.native 14.0-dev26`
- Renderer: `openspm.aurora-gx 14.0-dev7`
- D3D12 adapter: RTX 5060 Ti
- FIFO correctness mode: synchronous Drain
- protected v19 display-list bridge active
- native retrace phase-lock active
- frame-boundary host-event deferral active

Player result: **game still feels choppy**.

## Astra debug-heap candidate: launch-policy repair is valid, but not the main choppiness cause

Astra's `M14-PERFORMANCE-DEBUG-HEAP-AUDIT-2026-09-14.md` correctly identified that the normal M14 launcher always creates the game under `DEBUG_ONLY_THIS_PROCESS`, and the old wrapper did not explicitly request the normal Windows heap policy. Commit `034f2a...` sets child-only `_NO_DEBUG_HEAP=1` and retains crash capture.

035138 proves the new policy is actually active:

```text
[OpenSPMWinDbg] ... heap_policy=normal _NO_DEBUG_HEAP=1
```

The player still reports essentially unchanged choppiness. Therefore keep the launch-policy fix as correct hygiene, but **retire debug-heap/LFH as the primary performance diagnosis unless a controlled same-binary `--debug-heap` A/B later proves a material difference**. Do not stack further allocator guesses on top of it.

## VI/retrace throughput is no longer a proxy for perceived smoothness

The phase-locked scheduler now delivers long gameplay stretches at exactly 60 retraces per ~1000 ms, including the reported choppy area. Examples in seq=2/stage=1 repeatedly show 60 or 61 retraces while the player still sees poor motion.

That means the remaining complaint cannot be diagnosed from one-second Wii retrace counts alone. A runtime can execute 60 simulation/retrace epochs and still look choppy if desktop presents are uneven, duplicated, bunched, or delayed.

## Previously suspected waits remain cleared

Cumulative Aurora acquisition statistics are still negligible. Around 2048 acquired frames in 035138:

- frame-slot: ~12 us total, max 1 us
- staging-slot: ~136 us total, max 136 us
- staging-map: 0 us

These cannot explain persistent visible choppiness.

PE bbox readback is also not showing the old large cost through the long heavy-scene segment. Typical sampled submit+map costs are sub-2 ms and the logged samples are `slow=0`. Bbox remains a possible GPU shader-contention concern because the fragment shader uses atomics, but CPU-visible bbox fencing is not currently the obvious steady stall.

The special `GXSetZCompLoc` early-depth dual-pass marker does not appear in 035138, and the old 7 MiB guest-array high-water trigger also does not appear. Do not optimize either path merely because it was listed as a speculative Astra follow-up.

## Separate late host-event hitch cluster remains real

Near the end of 035138, Aurora `window::poll_events()` stalls repeatedly for roughly 120–332 ms. Those stalls line up with isolated one-second retrace drops into the 39–53 range. They are genuine severe hitches and remain a separate event/Win32 frontier.

However, they occur late in the run and cannot explain the player's persistent choppiness during the many earlier seconds that report 60/60. Do not conflate this late cluster with the steady presentation problem.

## New diagnostic: measure the thing the player actually sees

The missing measurement is the cadence of successful host swapchain presentations.

Aurora's public `aurora_end_frame()` does **not** itself perform `g_surface.Present()`. It queues a render-worker job through `gfx::end_frame`; the render worker later acquires the surface texture, submits the redraw command buffer, and calls `g_surface.Present()`.

Therefore timing Broadway's presentation sync or the outer `aurora_end_frame()` call would still not prove desktop frame pacing.

New commits:

- `62df10f91fa3c4a1d16ce1306a46ac6fa60dfb1f` — add exact/idempotent render-worker swapchain-present pacing adaptation
- `1358c999a9ce2b6f0a9fcb9f01f0a32a2bafd9ee` — wire it into the normal cached-Aurora configure migration chain

`apply_present_pacing_diag_patch.py` instruments only **successful `g_surface.Present()` calls**. It records:

- successful present count;
- interval average;
- 0.5 ms histogram with p50/p95/p99;
- maximum interval;
- counts over 18 ms, 25 ms and 33 ms;
- average/max duration of the `Present()` call itself.

It emits only at 256/512/1024/... successful presents and once after render-worker synchronization during shutdown. Cost is two `steady_clock` reads per actually presented host frame, not per GX draw/FIFO operation.

Expected marker:

```text
M14 host present pacing reason=periodic presents=... intervals=... avg_us=... p50_us=... p95_us=... p99_us=... max_us=... over18=... over25=... over33=... present_call_avg_us=... present_call_max_us=...
```

No GX semantics, parser boundaries, draw merging, shaders, guest-array coherence, Wii timebase, retrace delivery, event ordering, or presentation ordering are changed.

## How to interpret the next player run

Use the same heavy scene and ordinary `M14-UPDATE-RUN.cmd`.

If VI stays around 60 but host-present p95/p99 are materially above ~16.7 ms, or many intervals exceed 25/33 ms, the remaining subjective choppiness has finally been localized to host presentation pacing. Compare `present_call_*` against interval tails:

- long intervals + long `Present()` calls => swapchain/vsync/compositor/backend blocking is implicated;
- long intervals + cheap `Present()` calls => frames are reaching the render worker unevenly, so inspect render-worker queue/GPU command completion and producer cadence;
- clean ~16.7 ms present intervals while player still reports choppy motion => host presentation cadence is innocent; move to motion-state interpolation/update cadence or content-level frame duplication rather than continuing renderer stall guesses.

If actual presentation cadence is clean, follow Astra's fallback recommendation and obtain measured host draw/merge counts plus GPU timings before changing bbox atomics, early-Z, or descriptor/index code.

## Do not regress

- Do not restore the failed 32 KiB synchronous parser-progress experiment.
- Do not restore async FIFO Thread mode.
- Do not restore the no-guest-index fast path.
- Do not undo v19 display-list semantics.
- Do not alter the 60.75 MHz Wii timebase to hide presentation problems.
- Do not infer smoothness from VI/retrace count alone.
