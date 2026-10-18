# M14 runtime 002303: hotpath rollback proven, snapshot pool next

Player log: `launch(20260914-002303).log`

Repo tested: `bed1dcdec007aa9d9e1551b5164e0e6322220e35`

## Correctness A/B result

The rollback of the 16d2b30 65,536-draw hotpath/no-guest-index experiment is player-proven successful:

- Chapter 1-2 spinning/rotating devices render Mario normally again; the smear regression is gone.
- Doors complete their transition again; the door/event softlock regression is gone.
- Therefore both regressions belong to the retired hotpath experiment. Do not restore its no-guest-index scan fast path.
- The older missing visible door open/close animation remains a separate pre-existing frontier.

The large lag spikes in this log are legitimate workload evidence: the player deliberately entered the large enemy-filled room whose door had softlocked in the rejected build.

## Enemy-room performance baseline

The graphics-correct synchronous runtime still becomes extremely slow in the enemy room.

Observed coarse-profiler/runtime extremes include:

- 2,048-draw windows reaching about 100 ms CPU;
- individual draw-preparation outliers around 49-60 ms;
- synchronous `draw_done_drain` reaching about 269 ms;
- delivered retraces falling to roughly 12-13/s while the 60.75 MHz Broadway timebase continues tracking real wall time.

This is the intended performance baseline for the next optimization. Do not reinterpret these spikes as evidence that the rollback failed.

## Retained evidence from rejected 233530 profiler

Although the 16d2b30 build was rejected for correctness, its diagnostic breakdown remains useful because the timing instrumentation itself showed a strong concentration of cost in indexed-array staging:

- ~387 ms total / ~310 ms array staging;
- ~385 ms total / ~308 ms array staging;
- ~360 ms total / ~280 ms array staging;
- ~324 ms total / ~255 ms array staging;
- lighter windows still spent roughly 137-155 ms of ~195-210 ms in array staging.

Guest-index scanning was much smaller. The failed optimization targeted the wrong part of this path.

## New narrow candidate: pooled exact snapshots

Added `renderer/aurora/apply_guest_array_snapshot_pool_patch.py` and wired it through the existing cached-Aurora compatibility entry.

This candidate does **not** skip draws, attributes, scans, cache checks, or memcmp validation. It does not change uploaded guest bytes or `gfx::push_storage` ranges.

Before the candidate, every guest-array stage-cache miss owns a separate `std::vector<u8>` immutable snapshot, causing a heap allocation/copy per entry and mass frees at frame reset. The candidate replaces those per-entry vectors with one frame-local byte pool. Entries store snapshot offset+size; reuse still performs the same exact byte-for-byte `memcmp`. Same-address guest mutation therefore remains observable and cache semantics remain exact.

The pool retains capacity across frames (8 MiB common-case reserve) but clears logical contents each Aurora frame. Offsets remain valid even if the pool grows/reallocates.

Expected runtime marker:

`M14 raw guest-array pooled exact snapshots active; cache semantics remain byte-exact`

Status: **runtime-unproven performance candidate**.

Acceptance requirements:

1. spinner remains correct;
2. doors still transition and do not softlock;
3. v19 systemic graphics remain clean;
4. enemy-room retrace/draw CPU profile improves materially versus the 002303 baseline;
5. if performance is unchanged, keep correctness and retire/demote allocation churn as the dominant part of array staging rather than weakening coherency.

No `.openspm` or save wipe is required.
