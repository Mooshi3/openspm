# M14 LLVM dev55 — indirect native-transfer budget guard

Date: 2026-09-16  
Branch: `m14-native-execution`

## Player evidence that changed the diagnosis

Latest player log: `launch(20260916-155515).log`  
Player repo revision: `b9c7a12f5a99`

The corrected dev54 build booted through the same opening/file-select path, but two important results changed the diagnosis:

1. The previous stationary target `0x80032B94` disappeared completely after the measured map entry was added.
2. Selecting the same file later produced a new stationary state:

```text
pc  = 0x800331AC
lr  = 0x80038964
ctr = 0x800331AC
exc = 0
program = 0
wg = 2622269
region_queries = 38
```

Those values remain unchanged for heartbeats 21 through 39. This is not a renderer crash, NAND failure or PPC exception.

The run also stayed visibly slow. Its normal progressing cadence was roughly `29-31 spmarioMain updates / 60-62 retraces` despite the dev54 chunk-size change from 128 to 64. Therefore chunk-64 is not treated as a proven SPM performance win.

## Why this is no longer treated as one missing map entry

Pinned DolRecomp's indexed global dispatcher accepts any aligned guest address inside a generated chunk. An exact semantic map entry is not generally required merely to dispatch an aligned PC.

More importantly, the LLVM backend has an asymmetric budget/yield contract:

- direct external/native transfers pass through `emitBudgetGuard(target)` in `branch_targets.cpp`;
- native-ABI `DOLIR_TERM_INDIRECT` transfers in `control_flow.cpp` previously went straight to `returnNative(target)` with no corresponding guard;
- loop-header guards only recognize statically visible backward CFG edges, so an indirect call/tail cycle need not be discovered as a loop header.

This gives computed `bctr/bctrl` chains a path that can stay inside native execution without reaching the normal soft-budget side exit that lets OpenSPM service VI, scheduler, GX-finish and other asynchronous semantics.

That architecture matches both file-select wedges: each persistent snapshot has `PC=CTR` at a computed target and all OpenSPM progress counters stop.

## dev55 compiler repair

`tools/native_bridge/apply_openspm_llvm_wpar_codegen_patch.py` now patches the pinned DolRecomp LLVM backend with:

```text
OPENSPM_M14_LLVM_INDIRECT_BUDGET_GUARD
```

The patch adds an overload:

```cpp
emitBudgetGuard(llvm::Value *pc)
```

and calls it before native-ABI indirect transfer forwarding.

If the soft budget is still available, execution continues exactly as before. If exhausted, the already-computed PPC target is materialized and the native body returns to OpenSPM. This mirrors the scheduling contract already used by direct native calls.

This is semantically safe for linked computed branches because DolIR computes the LR/CTR target and writes LR (`PC+4`) before the indirect terminator is emitted. A budget exit therefore resumes at the computed destination after the architectural branch effects have been established.

Toolchain manifest version is now:

```text
llvm-inline-wpar-indirect-budget-v3
```

`run_m14_llvm_candidate.py` requires that exact version, forcing the local updater to rebuild the pinned DolRecomp compiler instead of reusing the previous toolchain silently.

## Deliberately unchanged for this A/B

- `DOLRECOMP_LLVM_CHUNK_INSTRUCTIONS=64`
- `DOLRECOMP_DISPATCH_LOOKUP=indexed`
- dev54 measured `0x80032B94` map entry remains present
- runtime bridge remains dev53 (`legacy_c_adapter_v41.c.in`)
- GX/input/PE-finish semantics remain unchanged

Keeping the rest unchanged makes the dev55 run a clean test of the indirect budget guard against the exact dev54 player baseline.

## Performance observations from dev54

The renderer is not currently the obvious recurring limiter. The player log contains only the one startup `~257 ms` begin-frame/FIFO-submit stall and no repeating renderer-cost stalls. During most of the opening the native runner returns hundreds of times per second and charges about 79-80 million guest cycles per host second, yet `spmarioMain` remains near 30 updates/s.

There is also a roughly 2:1 relationship between scheduler-idle presentation safepoints and GX-finish completions in parts of the run. This may be normal scheduler geometry or may expose an extra synchronization cadence; do not change it until dev55 establishes whether computed-transfer starvation is contributing.

If dev55 removes the file-select wedge but cadence remains near 30/60, the next performance investigation should compare the GX-finish wake / VI-wait timeline against a known 60-Hz generated-C run before applying PGO or renderer micro-optimizations.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

Expected build evidence:

```text
OpenSPM LLVM codegen adaptation: ... indirect-budget-guard=applied
M14 OpenSPM codegen patch: llvm-inline-wpar-indirect-budget-v3
```

Then select the same save. Send the complete launch log whether it succeeds or wedges.

### Correctness success

- no persistent stationary `PC=CTR` computed target after file selection;
- seq/stage proceeds into the loaded save;
- NAND/GX/input remain healthy;
- no new exception/program fault.

### Performance observation

Record the normal `spmarioMain/retrace` cadence. The dev54 comparison is approximately `29-31 / 60-62`; chunk-64 alone did not solve the visible lag.
