# M14 LLVM FPU exception-resume cold-entry fix — 2026-09-14

## Symptom

The LLVM-native candidate still reached the existing cache-stable RVL OSInterrupt recovery at `0x80271E48`, but then became permanently stationary at:

- `pc=0x802F09B4`
- `lr=0x8017B664`
- `msr=0x0000A032`
- `exc=0`
- `program=0`
- native range `[0x802F09A8,0x802F0A1C)`
- `last_hostcall=0x802F09B4`

The previous linked-call-return and post-`MTMSR` continuation admission rules changed the generated entry sets, but did not remove this frontier. That proved `0x802F09B4` belonged to a different resume class.

## Exact player-code inspection

A previously saved player-owned checkpoint contains the same verified R8PE01 `main.dol` used by M14. It was inspected locally only; no Nintendo bytes or instructions are added to the repository.

The exact control flow establishes:

- the game calls the function beginning at `0x802F09A8` from `0x8017B660`;
- therefore the architectural LR is `0x8017B664`, exactly matching the runtime heartbeat;
- `0x802F09B4` is an interior `lfs` instruction and the first floating-point instruction in that callee.

So the frontier is not a hidden function boundary, a linked-call continuation, or a post-`MTMSR` PC.

## Architectural cause

Pinned DolRecomp inserts `DOLIR_HELPER_FP_AVAILABLE` before every decoder-recognized FPU / paired-single instruction (`ppc_op_uses_fpu`). The helper reads MSR[FP]. If FP is disabled, `ppc_fp_available()` raises the FP-unavailable exception at the faulting CIA.

The pinned CPU core then stores that same CIA in SRR0. Wii exception handling enables FP and returns with `rfi`, which retries the original FPU instruction.

That explains the apparently contradictory stationary heartbeat cleanly:

1. native execution reaches `lfs` at `0x802F09B4` while FP is unavailable;
2. FP-unavailable exception saves `SRR0=0x802F09B4` and transfers to the handler;
3. the Wii handler enables FP and returns;
4. by the time OpenSPM samples the resumed state, MSR is `0x0000A032` (FP enabled) and the exception flag is clear;
5. the public dispatcher must now cold-enter `0x802F09B4` itself;
6. without that CIA in the LLVM entry set, the wrapper's entry-miss path cannot resume the translated instruction and the run becomes stationary there.

The important contract is therefore not address-specific: **every translated FPU/paired-single instruction is a possible architectural exception-restart entry.**

## Compiler-side fix

OpenSPM's private DolRecomp pipeline now adds every executable instruction satisfying:

```c
ppc_op_uses_fpu(inst.op)
```

to the LLVM cold-entry set at its own guest address.

This is layered on the already-proven entry rules for:

- direct branch targets;
- linked `B` / `BC` return continuations (`PC+4`);
- post-`MTMSR` continuations (`PC+4`).

The existing two-entry-per-instruction allocation bound remains sufficient: an instruction can contribute at most the linked branch target plus linked continuation, while `MTMSR` and FPU op classes contribute one entry each and do not overlap those branch instructions.

The retained WPAR/MSVC/progress/cold-entry patch implementation is preserved byte-for-byte in `_apply_openspm_llvm_codegen_patch_base.py`; the public `apply_openspm_llvm_wpar_codegen_patch.py` front-end verifies the historical source-contract markers, delegates the retained transform, then idempotently upgrades the generated `pipeline.c` with the FPU exception-restart rule.

## Cache behavior

The global 128-instruction chunk/range layout is unchanged. `pipeline.c` remains app-level compiler tooling outside the global guest-codegen source digest; however, the per-object entry-point set is part of each object's identity. Therefore chunks whose effective entry set gains FPU CIAs may re-emit, while unrelated chunks remain eligible for exact cache reuse.

Because SPM uses floating point heavily, this run can legitimately rebuild more objects than the earlier MTMSR-only change. That is expected and is not evidence of a full-cache invalidation.

## Next validation

Run the unchanged transactional path:

`M14-LLVM-UPDATE-RUN.cmd`

Primary acceptance criterion: the stationary `0x802F09B4` / `last_hostcall=0x802F09B4` frontier must disappear.

If a new stationary frontier appears, decode that exact R8PE01 instruction and classify the architectural resume source before broadening the entry set again. Do not add individual address exceptions unless the ISA/runtime contract requires one.
