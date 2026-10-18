# M14 LLVM linked-call continuation cold-entry fix — 2026-09-14

## Evidence from the second black-window run

The narrow RVL OSInterrupt recovery did execute successfully:

- `M14 LLVM RVL cold-resume recovery active ... 0x80271E48 ...`

Execution then advanced to a new stationary frontier:

- `pc=0x802F09B4`
- `lr=0x8017B664`
- `ctr=0x8017B644`
- `msr=0x0000A032`
- `exc=0`, `program=0`, `wg=0`
- `region_queries=137089` and then permanently unchanged
- last region `[0x802F09A8,0x802F0A1C)`
- `last_hostcall=0x802F09B4`

This proves the previous `0x80271E48` hang was not a one-off Wii register problem. The same failure mode can recur at another interior PC of another LLVM range.

## Compiler-side gap

DolRecomp's LLVM backend already knows how to resume a linked call internally at `BL/BCL + 4`. `branch_targets.cpp` computes that continuation and branches to the corresponding local block after a native callee returns.

However, `collect_llvm_entry_points()` in `src/app/pipeline.c` only promoted branch *targets* to legal top-level LLVM entry points. It did not promote the return continuation of linked branches.

A native callee can cold-escape through OpenSPM (host interception, protected seam, runtime boundary, etc.). Once control crosses the public dispatcher, the caller continuation may later be presented to the containing LLVM object as a fresh entry PC. If that PC is not a region leader, RecompCore-mode `emitColdEntry()` sends it to `ppc_fallback_instruction()`. OpenSPM intentionally supplies only the narrow M14 migration fallback, not a complete PPC interpreter. An ordinary continuation instruction can therefore leave PC unchanged and spin forever inside the fallback retry path.

## Fix

Commit `b0002a6a97e4fca6d1be86a14440c93737dee01a` extends the existing private DolRecomp app-level patch in `apply_openspm_llvm_wpar_codegen_patch.py`.

`collect_llvm_entry_points()` now adds `address + 4` for every linked `PPC_OP_B` / `PPC_OP_BC` whose continuation remains in executable code. The collector's capacity is expanded accordingly and its normal sort/dedup pass remains authoritative.

This does **not** alter the global 128-instruction function-range / partition layout. It only makes linked-call continuations legal region leaders inside their existing objects.

## Cache behavior

The patch is intentionally in `src/app/pipeline.c`, which OpenSPM already treats as outside the LLVM codegen-source digest. Therefore it does not globally invalidate every cached object merely because the compiler executable changed.

DolRecomp's per-job hash already includes only entry points that fall inside that job. Objects whose local entry-point set gains one or more linked-call continuations will re-emit; unaffected objects remain reusable.

This is preferable to adding all continuation PCs to the symbol map, which would modify the global function-range table and poison every job key.

## Temporary RVL shim

`legacy_c_adapter_v19.c.in` remains active for now. It is narrow and already proved that escaping the first stationary cold-entry frontier advances execution. Once general cold-entry parity is proven, remove that migration shim rather than accumulating address-specific recoveries.

## Next acceptance test

Run `M14-LLVM-UPDATE-RUN.cmd` normally.

Expected build behavior:

- Step 4 rebuilds the private DolRecomp executable because `pipeline.c` changed.
- Step 5 reuses most cached objects and re-emits only jobs whose local linked-call continuation entry set changed.

Expected runtime behavior:

- the old `0x80271E48` shim marker may still appear;
- the candidate must not become stationary again at `0x802F09B4` / `[0x802F09A8,0x802F0A1C)`.

If another stationary interior PC appears after this change, inspect whether it is a continuation of a non-call runtime boundary. Do not add another address-specific shim; extend the entry-point collector by semantic class instead.
