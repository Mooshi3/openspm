# M14 dev92 — configure-chain repair + guest-array timing retirement

Date: 2026-09-18

## Trigger

First player attempt at repo revision `70dad13ab5d5d75925aeb1ac75a1cb34d0424f80`
passed the LLVM source contracts and then stopped during the explicit CMake
reconfigure:

```text
OpenSPM M14 Aurora FIFO reentrancy adaptation failed (7):
Aurora worker retirement / producer profiler / resize repair configure failed:
OpenSPM DrawDone substage profiler adaptation failed:
quiet draw handler profiler: expected source fragment exactly once, found 0
```

This was not a renderer compile failure and did not reach the dev89/dev91 build.

## Root cause

The cached Aurora checkout already had dev88's intentional marker:

```text
M14 DrawDone per-draw profiler retired for normal play
```

but the older configure compatibility chain still flowed through
`apply_render_worker_pacing_diag_patch.py`, whose historical 050525 behavior
explicitly re-launched `apply_drawdone_substage_profile_patch.py`.

That old profiler expected the pre-dev88 quiet `handle_draw` block. dev88's
retired handler deliberately has a successor comment/source shape, so the old
installer failed its exact anchor.

In other words: an old compatibility hook was trying to resurrect diagnostics
that the current frontier intentionally retired.

## Repair

Two defenses are now active:

1. `apply_render_worker_pacing_diag_patch.py` no longer invokes the temporary
   DrawDone profiler at all. It only maintains the still-valid raw-Wii resize
   repair.
2. `apply_drawdone_substage_profile_patch.py` recognizes the dev88 retirement
   marker and returns `already-retired` without touching source.

The LLVM source contract now constructs a synthetic dev88-retired
`command_processor.cpp` and proves that the old profiler transform no-ops
idempotently.

## Additional performance cleanup: dev92

Tracing the configure chain exposed a second retired measurement that remained
in the active guest-array path:

`apply_guest_array_timing_probe_patch.py`

That historical probe inserts a `steady_clock` start for every
`m14_stage_guest_array` request and aggregate timing/accounting on the cache
path. Those measurements already localized the guest-array cost and directly
led to dev89/dev91.

dev92 removes only:

- `sM14GuestArrayStageNanos`;
- `sM14GuestArrayStageCalls`;
- `sM14GuestArrayProfileFrames`;
- sampled `M14 raw guest-array stage profile ...` logging;
- `profileStart` / `profileFinish` timing work.

It does not change:

- guest-index scanning;
- compact interval selection;
- exact pointer/stride matching;
- exact `memcmp` validation;
- `gfx::push_storage` bytes or ranges;
- FIFO/draw ordering;
- shader addressing;
- guest mutation visibility.

Expected source/runtime policy marker:

```text
M14 raw guest-array stage timing probe retired for normal play
```

The predecessor timing-probe installer is successor-aware and cannot reinstall
it on later configure passes.

## Exact test candidate proof

`run_m14_llvm_candidate.py` explicitly reconfigures the existing Windows
candidate and then reads its real `OPENSPM_AURORA_SOURCE_DIR` from
`CMakeCache.txt`.

It refuses to compile/launch unless that source proves all of:

- dev89 fixed bucket + scan plan;
- dev91 staging-backed exact snapshots;
- dev92 guest-array timing retirement;
- no old `unordered_multimap` stage lookup;
- no CPU snapshot byte pool;
- no guest-array stage timing counters/profile marker/`profileFinish`.

Expected pre-build line:

```text
[OpenSPM] Renderer performance candidate proof PASS: dev89 fixed-bucket/scan-plan + dev91 staging-backed exact snapshots + dev92 timing retirement are present in configured Aurora.
```

## Player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No cache wipe or HOME reset.

The first useful gates are:

```text
PASS: OpenSPM LLVM native pipeline contracts
Aurora DrawDone substage profiler remains retired for normal play
Aurora guest-array stage-head + scan-plan optimization applied
Aurora staging-backed exact guest-array snapshots applied
Aurora guest-array timing probe retired for normal play
Renderer performance candidate proof PASS
```

Then test the same heavy enemy/NPC area and provide the resulting
`launch(...).log`.

The main comparison remains completed `M14 main-loop phases frames/host_ms`
and DrawDone producer-sync timing versus the dev88 baseline.
