# M14 LLVM dev78 — retire superseded wedding state spills

Date: 2026-09-17  
Branch: `m14-native-execution`

## Player acceptance of dev77

Player run:

`launch(20260917-231158).log`

Repo revision:

`57f864c68eaa9b0f0e7511266397ae1283e6e287`

The run passed the complete wedding cutscene and continued into normal gameplay.
The old `0x80065128` stationary frontier is gone:

```text
hang_80065128_max=0
```

This confirms dev77's structural partition entry is the required wedding repair.
The generated-C control already passed the same sequence; LLVM now does too
without mutating guest logic, timers, effect state or scheduler behavior.

## Remaining performance defect

Light scenes are healthy:

```text
spmarioMain=58 retraces=61
spmarioMain=61 retraces=61
```

Later gameplay is still quantized near half rate:

```text
spmarioMain=31 retraces=62
spmarioMain=30 retraces=60
spmarioMain=30 retraces=60
spmarioMain=31 retraces=62
```

The renderer is not large enough to explain the missing frame:

```text
DrawDone avg ~= 3.1 ms
present call avg ~= 0.205 ms
```

The long run again accumulated enormous public-dispatch counts, but dev66 already
proved that collapsing ~99.5% of ordinary semantic-router passes did not restore
the missing 2x. Do not return to router-table micro-optimization.

Notable remaining native-boundary counts from this run:

```text
direct-BL cold-entry rescues = 11,211,778
RVL post-MTMSR tails        =  1,577,698
```

Those remain a later structural profiling target if dev78 does not materially
move the heavy-scene cadence.

## Why dev78

The active compiler still carried two wedding-debug experiments after dev77 had
made them unnecessary.

dev72 changed legacy x86 native-ABI `syncDirtyState()` so a cold escape writes
**every state slot used by the range**, rather than the normal dirty subset.

dev76 then added:

```cpp
if (nativeTarget && native_abi_ && cold_escapes_ && intrinsic_escapes_)
    syncDirtyState();
```

before every external native call.

Neither dev72 nor dev76 fixed the wedding. dev77 fixed it by making the externally
resumable `0x80065128` a real compiler entry.

The combined dev72/dev76 behavior can therefore impose broad architectural-state
stores at every native call boundary for no proven remaining correctness benefit.
That tax is particularly suspicious in scenes where CPU work scales with many
actors/effects.

## dev78 change

Active compiler base:

`tools/native_bridge/_apply_openspm_llvm_codegen_patch_base.py`

now delegates to the preserved pre-wedding-experiment dev71 base.

Retained:

- unrestricted native ABI;
- legacy x86 cold escapes;
- inline LLVM WPAR gather;
- linked BL/BCL continuation entries;
- post-MTMSR continuation entries;
- FPU restart entries from the composing patch;
- indirect-transfer budget guards;
- generated positive native-region admission cache;
- chunk128/indexed generation;
- dev77 wedding partition entry.

Retired from active codegen:

- `OPENSPM_M14_LLVM_COLD_ESCAPE_FULL_STATE` (dev72);
- `OPENSPM_M14_LLVM_CALLER_STATE_CHECKPOINT` (dev76).

The historical files remain in source control for archaeology.

New active marker:

```text
OPENSPM_M14_LLVM_RETIRE_FULL_STATE_CALLER_CHECKPOINT
```

Patch transform reports:

```text
dev78-state-spills=retired
```

Codegen provenance is bumped from:

```text
llvm-inline-wpar-indirect-budget-v3
```

to:

```text
llvm-inline-wpar-indirect-budget-v4-dev78
```

Both preparer and transactional runner require the new identity. The source-only
pipeline contract was updated too.

## New graphics report

During the successful post-wedding gameplay run, the player reported that a
spinning device left Mario invisible, probably after Mario entered it while
offscreen.

Treat this as a separate renderer/GX correctness frontier. The run remained live
afterward and did not show a guest/native exception, so do not synthesize Mario
visibility state in the bridge. Candidate areas for a later targeted trace are
cull/clip state, PE bounding-box behavior and draw-state restoration across the
device transition. No speculative graphics mutation is included in dev78.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

Do not wipe caches.

The preparer should print the normal retained patch stack plus:

```text
dev78-state-spills=retired
M14 OpenSPM codegen patch: llvm-inline-wpar-indirect-budget-v4-dev78
```

Test gates:

1. wedding still passes — this proves dev77 is sufficient without dev72/dev76;
2. compare the same heavy gameplay area against the previous ~30-31 updates/s;
3. note whether input/graphics regress before attributing any performance change.

If cadence improves materially, keep dev78 and then profile the remaining
native-boundary exits.

If cadence is unchanged, the next high-value target is not router lookup:
classify/eliminate the multi-million direct-BL rescue/public-dispatch crossings
or capture a Windows host CPU sample profile in the same heavy scene.

If the wedding regresses, restore only the minimum state contract proven by the
new log; do not immediately restore both broad spill experiments.

## Preserve

No emulator/general PPC interpreter fallback. Player-owned game code/assets only.
Keep dev68 runnable-idle wake, dev69 immutable filters, dev63 inline positive
admission cache, chunk128/indexed generation, dev77 restart partition, v19 GX
CPU-shadow/display-list semantics, deterministic relF/BSS placement, exact GX
finish completion, input edge retention and fixed-phase 60 Hz VI.
