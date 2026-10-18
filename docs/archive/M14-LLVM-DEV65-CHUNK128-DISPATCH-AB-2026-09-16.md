# M14 LLVM dev65: 128-instruction dispatch-pressure A/B

Date: 2026-09-16

## Why dev64 was not the lag fix

Latest tested player run:

```text
launch(20260916-214826).log
repo 5d6575cbe082
```

User result:

- input edges are no longer eaten;
- a small amount of input latency remains;
- gameplay lag is essentially unchanged;
- HP reaching zero did not complete the normal game-over/respawn transition.

The log confirms that the remaining lag is simulation starvation rather than
presentation/GPU pacing. Representative late gameplay windows are still only
28-29 `spmarioMain` updates against 60-62 retraces per second.

Dev64's exact `strcmp` specialization executed successfully but did not cure the
architecture-level dispatch pressure:

```text
strcmp calls       = 51,251,628
strcmp completed   = 44,964,954
strcmp declined    =  6,286,674
ordinary callbacks = 351,442,649
production ordinary= 351,428,529
seam lookup        = 351,389,331 calls / 3,019,036,559 comparisons
host lookup        = 351,120,829 calls / 2,036,176,425 comparisons
```

The 1/4096 heavy-hitter sample no longer points principally at the old `strcmp`
64-instruction boundary. Its surviving large sample is instead ordinary native
PC `0x8006F80C` (`386` sampled hits). That strongly suggests the dev64 `strcmp`
frontier was one manifestation of a broader chunk/side-exit problem rather than
the unique bottleneck.

Renderer timing is not large enough to explain the missing half of simulation
cadence. Host present calls are roughly hundreds of microseconds, and measured
GX producer work is low-single-millisecond scale in the bad path.

## Architectural observation

Pinned DolRecomp's public dispatcher checks the OpenSPM host callback before
falling through to the native original lookup. Therefore every LLVM side exit
that returns to public dispatch can pay OpenSPM's semantic-router work even when
the destination is merely another ordinary native PC.

OpenSPM had explicitly forced:

```text
DOLRECOMP_LLVM_CHUNK_INSTRUCTIONS=64
```

That was based on a generic upstream register-allocation campaign. It is not a
measurement of OpenSPM's custom runtime, where public-dispatch crossings are
very expensive. Pinned DolRecomp's own default chunk size is 128 instructions.

At 64 instructions, dev64 already proved one real retail function (`strcmp`)
was split directly through its hot loop. Completing that one function in C only
moved the sampled heat elsewhere while hundreds of millions of ordinary exits
remained.

## dev65 experiment

Dev65 changes one architectural variable:

```text
64 PPC instructions per LLVM range -> 128
```

Indexed dispatch remains enabled.

The dev64 host-side `strcmp` interception is removed. `strcmp` is ordinary
native generated code again; with the larger region it should no longer hit the
old artificial boundary at `0x8025CAC8`.

The dev63 generated inline positive-admission cache is retained exactly. This
is mandatory: dev63 already eliminated the previous billion-scale native-region
query callback storm.

The runtime bridge still inherits the full dev58/v19 production semantics:

- protected v19 GX CPU shadow/save/restore behavior;
- display-list semantic seams;
- dev53 HOME admission correction;
- dev57 address-taken native entries;
- dev55 indirect soft-budget guard;
- deterministic relF/BSS placement;
- real GX finish completion;
- frame-latched KPAD/WPAD;
- dev27 pressed-edge-until-consumed behavior;
- fixed-phase 60 Hz VI/retrace.

No semantic host target was removed and no Nintendo state is fabricated.

## Cache safety

`DOLRECOMP_LLVM_RESUME=1` remains enabled, but old 64-instruction objects cannot
be incorrectly blessed as 128-instruction objects. Pinned DolRecomp's LLVM job
hash includes the generated job's function address/count, partition seed,
function ranges, entry points and individual PPC instruction address/raw data.
Changing the partition geometry therefore changes the stamp for affected jobs.

## Expected next-run markers

Generation should report:

```text
chunk=128 dispatch=indexed
```

Runtime should report:

```text
M14 LLVM dev65 chunk128 dispatch-pressure A/B active
```

There should be **no** dev64 `strcmp stats` line at teardown.

## Success criteria

The important metric is not a tiny local speedup. Compare the same gameplay
path against dev64:

```text
dev64 baseline:
  simulation ~= 28-30 Hz
  retrace    ~= 60-62 Hz
  ordinary callbacks = 351M over the run
```

A useful dev65 result should materially collapse ordinary callbacks/router
lookups and push `spmarioMain` cadence upward. Reaching near 60 updates/s is the
goal.

If chunk128 produces a large callback collapse and a major cadence gain, retain
it and then inspect the remaining high-frequency exits.

If chunk128 barely changes ordinary callback density or simulation cadence, the
next architectural fix is not another libc helper. Patch the public dispatch
path so proven ordinary native destinations try native lookup before OpenSPM's
host semantic router, while retaining a generated exact priority set for the
383 hosted callsites + semantic targets/dynamic seams.

## Game-over defect

The HP=0 report is recorded as a separate correctness frontier, but lag remains
priority. The latest log does not show a native exception or total scheduler
freeze around the late run; seq/stage remain live and simulation continues at
~30 Hz. Do not synthesize a game-over transition.

After the dispatch-pressure fix, retest death. If it still fails, instrument the
actual player death/game-over transition and callback/sequence state rather than
forcing HP or scene state from the host.
