# M14 LLVM dev58 playable-production repair handoff — 2026-09-16

Branch: `m14-native-execution`

Player command:

```text
M14-LLVM-UPDATE-RUN.cmd
```

Implementation commit before this handoff: `c7ee1e037172dfb6f54dc83964dd74f5baba2427`

## Why dev58 exists

Dev57 is the first LLVM build the player could actually continue from file select into normal gameplay. That is a major correctness milestone: the address-taken entry census cleared the repeated computed-target freezes.

The first long playable run (`launch(20260916-175000).log`, repo `004ee4c61708`) immediately proved that LLVM still has two production defects which predate the cold-entry work:

1. simulation collapses to roughly 20-30 `spmarioMain` updates per host second while VI/retraces remain about 60 Hz;
2. the old global blue/striped material corruption is back.

The same run also reports that many transient inputs appear eaten and enemies move slowly. Do not split those into independent gameplay bugs yet: when the game simulation is only advancing 20-30 times per second, both actor motion and short pressed-button windows are naturally starved.

## Performance breakthrough: region-interception work is catastrophically over-frequent

The dev57 teardown counters are not normal native-port overhead:

```text
region_queries ~= 2.022 billion
ordinary host-call classifications ~= 889 million
seam lookups ~= 4.323 billion
seam binary-search comparisons ~= 37.381 billion
host-target lookups ~= 2.910 billion
host-target binary-search comparisons ~= 16.142 billion
```

This is the current highest-value performance finding.

Pinned DolRecomp LLVM calls `ppc_native_region_available()` from every native function body entry while `CPUState::host_call` is installed. OpenSPM's dev50 production router made an individual classification cheap compared with the old bring-up probes, but the playable run proves that doing even logarithmic classification billions of times is still unacceptable.

### dev58 admission cache

`tools/native_bridge/legacy_c_adapter_v41.c.in` now layers a positive admission memo above dev53/dev50.

Policy:

- key is exact generated `[start,end)`;
- only an **allowed** result is cached;
- blocked results are never cached;
- display-list seams remain dynamic and uncached;
- frame-phase telemetry regions remain uncached but are **not blocked**;
- relF allocator seams remain uncached;
- HOME/GX/NAND/platform/direct-hostcall regions remain safe because any underlying blocked result cannot enter the positive cache;
- direct-mapped cache collisions cause a miss/reclassification, never a false allow.

The top bridge still receives the query callback itself. This candidate is therefore a safe first cut: it should delete the billions of repeated binary searches while leaving exact semantic interception intact. If host callback overhead itself remains material after this A/B, the next compiler-side step can use the measured cache hit rate to justify bypassing the callback for proven-static regions.

Expected teardown line:

```text
M14 LLVM dev58 stats queries=... cache_hits=... misses=... inserts=... collisions=... bypasses=...
```

A useful success is a very high `cache_hits / queries` ratio and a dramatic collapse in the older dev50 `region_queries`, `seam_lookup` and `host_lookup` totals.

### Ordinary host-call heavy-hitter sample

The same dev57 run reported about 889 million non-region calls through dev50. dev58 samples one of every 4096 ordinary callbacks into an 8-slot Misra-Gries sketch so the next playable log can identify a dominant PC without adding per-call logging.

Expected teardown line:

```text
M14 LLVM ordinary hostcall sample total=... rate=1/4096 heavy=0x........:... ...
```

Do not optimize those PCs until this line identifies them.

## Graphics root cause: this is the protected v19 regression

The player's recollection of the old immutable guest-array snapshot fix was directionally useful, but the dev57 log proves those renderer fixes are already active:

```text
M14 raw guest-array compact interval staging active
M14 raw guest-array pooled exact snapshots single-copy append active
M14 raw guest-array pointer-indexed exact lookup active
```

So the stripes are not caused by forgetting to replay the snapshot-pool/single-copy renderer patches.

The protected graphics milestone tells us exactly what fixed this symptom:

- branch `milestone/m14-v19-graphics-fixed-2026-09-13`;
- commit `a928d97de420c23600c5cdc72013c159670cca7f`;
- Nintendo's recompiled `GXBeginDisplayList` / `GXEndDisplayList` CPU-side code executed normally;
- OpenSPM switched only the physical bounded recorder at persistent guest continuation labels;
- this preserved the complete `GXData` dirty/save/restore context semantics across yields;
- the milestone explicitly records that flip no longer caused the prior global blue/striped corruption and that the Dark Prognosticus outline returned.

LLVM dev48 later superseded that contract because the real `GXBeginDisplayList` body stalled at `0x80291E20`. dev48/dev51 instead skip the retail body and manually approximate selected `GXData` fields around the PC recorder. The current blue/striped corruption is direct evidence that the approximation is not equivalent.

### dev58 v19 restoration

The complete proven v19 implementation never left the source tree:

- `legacy_c_adapter_v11.c.in` owns the persistent v19 begin/end state;
- `legacy_c_adapter_v15.c.in` already contains the LLVM one-shot seam design;
- the player-local seam map already partitions GX Begin/End and their caller continuations.

Dev58 now restores that exact ordering above dev48/dev51:

1. block an unarmed GX Begin/End or v19 caller-return partition;
2. at exact `GXBeginDisplayList`, call `openspm_m14_display_list_begin_before_native_v19()` and arm one entry permit;
3. let the real recompiled SDK body execute;
4. at its durable caller return, call `openspm_m14_display_list_begin_after_return_v19()` so only then does the neutral recorder become active;
5. mirror the sequence for `GXEndDisplayList`, including the durable recorded-size replacement in `r3`;
6. dev48/dev51 never see the public Begin/End entry in a successful dev58 path.

Important: frame-phase entries are **not** display-list blockers. They only bypass admission memoization and continue through the existing nonblocking dev50 telemetry. An initial dev58 draft incorrectly reused the broad historical v23 seam helper as a blocker; that was corrected before player testing.

Expected runtime proof:

```text
M14 v19 continuation-safe GXBeginDisplayList return hook active
M14 v19 continuation-safe GXEndDisplayList return hook active
```

At teardown, dev58's `begin=pre/post end=pre/post` counters should be paired. dev48/dev51 manual Begin/End shadow counters should fall to zero or otherwise prove they are no longer the active public-call implementation.

### Known risk / decisive failure signature

Dev48 originally existed because LLVM got stuck inside the real Wii `GXBeginDisplayList` body at:

```text
0x80291E20
```

Much of the LLVM/WPAR/hostcall infrastructure has changed since then, so restoring the real body is now worth testing. If the exact `0x80291E20` stall returns, do **not** restore the whole-function dev48 shortcut. The next repair should bypass only the Wii physical FIFO transport sub-operation while retaining the full real SDK CPU-shadow state transitions.

## Input policy for this candidate

Do not change input sampling yet.

The current frame-latched KPAD/WPAD route is active, and the same run proves simulation itself falls to 20 updates/s in heavy gameplay. Fix the CPU starvation first. If simulation returns near 60 updates/s and taps are still lost, then instrument the pressed/released lifetime separately.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

Load the same save and reach the same laggy gameplay area.

Watch for:

- module version `m14-dev58-llvm-admission-cache-v19-restore`;
- `M14 LLVM dev58 playable-production repair active`;
- v19 Begin and End durable-return hook messages;
- no stationary cold-entry regression;
- whether the global blue/striped corruption disappears;
- whether Dark Prognosticus outline is restored if replaying the opening;
- whether gameplay responsiveness / enemy speed changes noticeably.

Keep the game open long enough to get several cadence lines in the same heavy area, then close normally so teardown stats print.

### Performance success

Compare against dev57:

```text
heavy dev57: spmarioMain ~= 20 / host second, retraces ~= 60
```

The important measurements are:

- `spmarioMain` updates per host second;
- dev58 cache hit/miss totals;
- old dev50 region/seam/host lookup totals;
- ordinary hostcall heavy-hitter sample.

Even a large improvement short of 60 is useful because it tells us how much of the missing frame time was interception machinery versus translated SPM code itself.

### Graphics success

- no global blue/striped material corruption;
- flip does not poison later materials;
- normal textures/materials remain stable;
- v19 pre/post counters pair cleanly;
- no `0x80291E20` stall.

## Permanent constraints

- no emulator or general PPC interpreter fallback;
- player supplies proprietary WBFS/extracted content;
- public repo ships no Nintendo code/assets;
- preserve protected v19 graphics milestone;
- preserve exact guest-array snapshot coherency; never restore no-guest-index lookup;
- do not fake VI timing or remove `VIWaitForRetrace()`;
- high refresh eventually uses presentation interpolation, not faster simulation;
- ultrawide remains Hor+.
