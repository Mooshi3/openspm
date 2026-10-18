# M14 runtime 050525: WPAR negative result, DrawDone substage frontier

Authoritative player log: `launch(20260914-050525).log`

Tested repo revision: `e956895fd06ac64fc95830c86ff9200cfdc01642`

Player result: **still visibly choppy**.

## Candidate was definitely active

The runtime reports:

`M14 staged DolRecomp CPU WPAR direct fast path active; exact 32-byte GX gather/order preserved.`

Therefore this is a real player A/B of the staged-CPU shortcut, not a stale binary.
The shortcut bypasses DolRecomp's generic `resolve_addr()` + indirect
`external_write` path only for the exact Broadway WPAR window while preserving
dev16's existing 32-byte gather bytes/order/flushes.

No new correctness regression was reported with the player result. Keep the
shortcut provisionally as a narrow migration optimization, but **retire it as
the primary explanation for scene-dependent choppiness**.

## Visible pacing remains bad before Present()

The swapchain call itself remains cheap (~0.2 ms average), but successful host
present intervals still acquire a 30-35 ms tail in gameplay:

- 2048 presents: p95 ~32.5 ms, p99 ~34.5 ms;
- 4096 presents: p95 ~31.5 ms, p99 ~33.5 ms;
- shutdown aggregate at 7001 presents: p95 ~35 ms, p99 ~36.5 ms.

Frame/staging slot acquisition remains effectively zero. This is producer-side
lateness, not a blocked swapchain or exhausted host frame ring.

## DrawDone still scales with scene complexity

Representative synchronous producer windows:

- opening: ~0.14-0.17 ms average;
- normal gameplay: commonly ~2.5-2.9 ms average;
- heavy region around total DrawDone 5376: ~4.04 ms average, p95 ~5.5 ms;
- heavy region around total DrawDone 5632: ~4.41 ms average, p95 ~5.75 ms,
  max ~7.97 ms.

At the same heavy boundary the scheduler-idle presentation safepoint is only
~8 us average. Aurora frame/staging acquisition is effectively zero.

Therefore `raw::drain()`/command processing is a real scene-scaling CPU cost,
but 4-8 ms alone cannot explain every 30-35 ms visible interval. Keep a second
frontier open for generated-game CPU/dispatch work outside DrawDone.

## Prior evidence that matters

The rejected 16d2b30 hotpath experiment bundled two things:

1. a useful draw substage profiler;
2. a semantic no-guest-index shortcut that caused rotating-device Mario
   corruption and door softlocks.

Its measurements showed indexed-array staging dominated synchronous draw CPU.
The semantic shortcut was rolled back and must never return.

Later pooled exact snapshots were player-proven to cut 2048-draw median CPU from
~9.745 ms to ~3.932 ms and p95 from ~46.745 ms to ~9.686 ms while preserving
exact memcmp coherency. Single-copy append improved the tail further. Thus the
old array-staging conclusion must now be remeasured against the current pooled
architecture rather than assumed unchanged.

## Current diagnostic candidate

Added `renderer/aurora/apply_drawdone_substage_profile_patch.py` and wired it
through the existing configure companion chain after the unsafe render-worker
probe is verified retired.

This is **profiling only**. It does not restore the rejected scan shortcut and
does not alter guest-array cache/snapshot semantics, FIFO order, draw merging,
pipeline validity, worker callbacks, parser scheduling, or presentation.

It measures, on the synchronous producer/Drain thread:

- total `handle_draw -> draw_prim` CPU;
- mandatory `guest_array_requirements()` scan time (the call is never skipped);
- vertex push time;
- the entire indexed-array staging/cache loop;
- pipeline rebuild time/count;
- texture/bind rebuild time/count;
- uniform rebuild time/count;
- merged/unmerged draw counts and total vertices.

One aggregate line is emitted every 256 real GXDrawDone boundaries so it can be
compared directly with the renderer's existing 256-boundary producer-sync
windows.

Expected marker:

`M14 GX DrawDone substage profiler active; producer-thread timing only, rendering semantics unchanged`

Expected aggregate prefix:

`M14 GX DrawDone substage profile total_frames=...`

The patch is fail-closed and applies its source edits only after all exact
anchors validate. Pre-frontier fresh Aurora source is skipped rather than
silently guessed.

## Interpretation of the next log

- If `arrays_us` still owns most of `draw_us`, optimize the current exact pooled
  snapshot/memcmp/upload path without weakening coherency.
- If `draw_us` is close to the existing DrawDone producer-sync cost but arrays
  are small, choose the largest measured pipeline/bind/scan/vertex bucket.
- If `draw_us` is much smaller than DrawDone cost, focus on non-draw GX command
  processing/state churn.
- Even if DrawDone becomes clear, retain the separate ~20+ ms residual-frame
  question: instrument generated-code run/dispatch work next rather than
  pretending a ~4 ms renderer bucket explains a ~33 ms frame.

## Permanent warnings

Do not restore:

- the no-guest-index guest-array shortcut;
- async raw FIFO worker over mutable guest memory;
- frequent 32 KiB synchronous parser-progress drains;
- render-worker callback pacing wrapper.

Retain v19 display-list semantics, exact pooled snapshots, compact intervals,
pointer-index lookup, single-copy append, phase-locked 60 Hz VI grid, renderer
dev7 shutdown/event repairs, and the normal Windows debugger heap policy.
