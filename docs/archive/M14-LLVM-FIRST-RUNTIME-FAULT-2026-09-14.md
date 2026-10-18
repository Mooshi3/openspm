# M14 LLVM first native runtime fault — 2026-09-14

## First full Windows LLVM build result

The first complete player-local LLVM generation/link succeeded far enough to launch the strict-native OpenSPM candidate. Aurora, the native backend, NAND transport, standalone MEM1/MEM2 setup and the authoritative Wii timebase all initialized normally. The guest then stopped after 211,882 cycles at the Broadway Program exception vector (`0x00000700`).

Observed runtime boundary:

```text
Native guest exception boundary reason=1 detail=0x00000001 source=0x00000700 cycles=211882
M14 LLVM seam stats queries=893 blocked=0 permits=0 seam_hits=0 permit_pending=0 permit_pc=0x00000000.
```

The later Windows process status `0xC000013A` was accompanied by `^C`, so it is the console/control-C termination status and not evidence of a host-native access violation. The architectural failure to investigate is the guest Program exception.

The zero seam-hit count is important: the failure occurs before the protected v19 GX display-list seams participate, so renderer/display-list interception is not the first suspect.

## Diagnostic added

`legacy_c_adapter_v17.c.in` now wraps the LLVM candidate's `run()` entry only to record the first unhandled guest exception after the existing bridge has already classified it. It does not resume, suppress or mutate the exception.

The diagnostic records:

- `CPUState.exception`
- vector PC
- `SRR0` (original guest fault PC)
- `SRR1`
- `program_exception` cause bits with FP / illegal / privileged / trap flags
- raw big-endian PPC instruction at `SRR0` when that address is valid MEM1/MEM2
- MSR, LR, CTR, CR, XER
- GPR0..GPR31
- guest cycle coordinate

Expected marker:

```text
M14 LLVM first guest exception: ... srr0=0x........ srr1=0x........ program=0x........ ... raw=0x........ ...
```

This should distinguish a special-instruction lowering omission from native-ABI/register/control-flow corruption without changing the failing behavior.

## Fast iteration change

`generate_local_llvm.py` no longer deletes the entire LLVM generation work tree on every A/B. It sets `DOLRECOMP_LLVM_RESUME=1` and preserves the tree. DolRecomp's existing per-object stamp includes the instruction payload, ABI policy, target, semantics, codegen fingerprint, native range data and OpenSPM codegen source hash, so only exact-valid objects are reused in place.

This is particularly valuable for the current diagnostic because only the bridge source changed; no guest LLVM object should require regeneration.

## Next action

Run `M14-LLVM-UPDATE-RUN.cmd` again and inspect the first `M14 LLVM first guest exception:` marker.

Interpretation priority:

1. `illegal=1` with a valid raw instruction: inspect that opcode against the generated-C instruction-fallback/special-instruction path and LLVM lowering.
2. `privileged=1`: inspect the raw privileged instruction plus MSR state and its LLVM lowering.
3. `trap=1`: decode the trap operands from the raw instruction and captured GPR state; an unexpected true condition points toward state/native-ABI propagation.
4. `fp=1`: inspect FP availability/MSR handling.
5. invalid or implausible `SRR0`: prioritize native ABI/control-flow state corruption rather than individual instruction semantics.

Do not change the renderer, v19 seam policy, or normal generated-C baseline based on this first fault. Keep the LLVM path opt-in and transactional until correctness parity is established.
