# M14 LLVM dev62 — bounded display-list caller continuations

Date: 2026-09-16
Branch: `m14-native-execution`

## Player evidence

Latest run:

```text
launch(20260916-192506).log
repo e40819223da4
```

Dev61 succeeded at its intended scope. The restored v19 path now executes both durable hooks and repeatedly completes display lists:

```text
GX display-list begin count=1 ... lr=0x80080624
v19 continuation-safe GXBeginDisplayList return hook active
GX display-list end count=1 ... lr=0x8008064C
v19 continuation-safe GXEndDisplayList return hook active
...
GX display-list begin count=64 ...
GX display-list end count=64 ...
```

The prior stationary frontiers `0x80291E20`, `0x80080630`, and `0x80291E9C` are all cleared. After 64 matched Begin/End pairs the persistent CPU becomes stationary at:

```text
seq=3 stage=6
pc=0x800806CC
lr=0x800806CC
ctr=0x00000000
exc=0
program=0
wg=2163844
region_queries=2
```

Retraces remain ~60 Hz while PC/LR/WG stop advancing.

## Interpretation

Do not widen the GXBegin/GXEnd SDK continuation range again. Dev61 proved those bodies are resumable and semantically completing. `0x800806CC` is caller-side, in the same display-list-builder function whose verified calls return at `0x80080624` (GXBegin) and `0x8008064C` (GXEnd).

The repeated `PC == LR` shape remains consistent with a linked call returning to a PC+4 which is not a usable native LLVM entry in OpenSPM's no-interpreter runtime.

## dev62 repair

`tools/native_bridge/augment_llvm_bl_continuations.py` now derives and partitions linked-call continuations in exactly one additional player function: the display-list-builder caller.

The player-local generator must verify:

- `0x80080620` is a direct linked branch to `GXBeginDisplayList=0x80291DC8`;
- `0x80080648` is a direct linked branch to `GXEndDisplayList=0x80291E78`;
- `0x800806C8` is a linked transfer whose architectural return is the measured `0x800806CC`;
- the caller's start can be derived from the nearest preceding `stwu r1,-N(r1)` EABI stack-frame prologue;
- its exclusive end can be bounded at the next stack-frame prologue after at least one real non-linking BCLR/BLR return.

Only linked-call PC+4 entries inside that derived `[caller_start,caller_end)` are materialized. GXBegin/GXEnd dev61 entries are retained. The whole-game direct-BL census remains audit-only.

Expected generation line now includes:

```text
dev62_caller_entries=<N> caller=[0x........,0x........)
```

Any shape mismatch fails generation and reports the offending local opcode/target; do not force a partition if that happens.

## Performance

This run remains ~30-31 `spmarioMain` updates for ~60-62 retraces before the display-list phase, so dev62 is still a correctness repair, not a performance claim. Once this caller resumes into gameplay, immediately benchmark a heavy scene against the protected generated-C runtime before more LLVM polishing.

## Preserve

- no general PPC interpreter/emulator fallback;
- protected v19 display-list CPU-shadow/GXData semantics;
- dev61 bounded GX continuations;
- dev58 positive region-admission memo;
- exact guest-array snapshot coherency;
- fixed 60 Hz VI/retrace semantics.
