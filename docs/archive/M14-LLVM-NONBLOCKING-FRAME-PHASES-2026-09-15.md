# M14 LLVM nonblocking frame-phase frontier — 2026-09-15

## Observed run

Tested repo revision: `6707894e8f112386a4162c0327f4b01ab9b37621`.

The v22 direct-BL rescue is active and the previous `0x801A7ACC` frontier is gone. Runtime reaches `seq=0 stage=1`, then repeatedly materializes:

- `PC=LR=0x801A7B58`
- queried native region `[0x801A7AAC,0x801A7CAC)`
- no guest exception
- no unsupported MMIO
- 60 Hz retraces continue

Exact verified R8PE01 DOL instructions around the new frontier:

```text
801A7B50  bl 0x80236950
801A7B54  bl 0x8027DA78
801A7B58  cmpwi r3,-1
801A7B5C  bne ...
```

So `0x801A7B58` is the ordinary continuation after the call at `0x801A7B54`, not a new hardware/service boundary.

## Root cause

This is self-inflicted by `legacy_c_adapter_v15.c.in`'s LLVM diagnostic phase seam.

`OPENSPM_M14_LLVM_PHASE_MAIN` is `0x801A7AAC`, which lies inside the queried native range `[0x801A7AAC,0x801A7CAC)`. v15 treats phase PCs as hard region-blocking seams. Its one-shot permit allows one entry into the range, but after any normal guest call returns into that same range the permit has already been consumed and v15 blocks it again.

That explains the progression:

- HOME completion explicitly armed one permit, allowing the first re-entry.
- v22's direct-BL rescue could advance across later continuations only when the stranded continuation itself happened to be another direct `bl`.
- `0x801A7B58` is `cmpwi`, so v22 cannot and should not emulate it; the phase-region gate becomes visible again.

This is not evidence of a DolRecomp continuation bug at this frontier.

## Fix

Added `tools/native_bridge/legacy_c_adapter_v23.c.in` and activated it for LLVM builds.

v23 inspects native-region queries before delegating to v22. If the queried range contains one of the four diagnostic frame-phase PCs, it arms v15's existing one-shot permit for that phase before delegation. Therefore v15 ignores exactly the diagnostic phase seam for that query.

Important preservation properties:

- v19 GX begin/end seams remain fail-closed.
- v19 durable GX return seams remain fail-closed.
- lower-layer region blocks remain authoritative.
- generated-C path is untouched.
- no LLVM object regeneration is required.
- input, NAND, DSP, HOME policy, renderer and direct-BL rescue are unchanged.

The new runtime marker is:

```text
M14 LLVM frame-phase telemetry is nonblocking: diagnostic spmario/frame phase PCs no longer gate native-region re-entry; v19 GX seams remain fail-closed.
```

## Acceptance criteria for next run

Run unchanged:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

Expected:

1. source-only contracts pass;
2. LLVM object cache is reused;
3. v23 marker appears;
4. `0x801A7B58` is no longer the sustained frontier;
5. `seq/stage` is allowed to advance according to real guest logic.

If a new frontier appears, classify it from the exact DOL instruction and caller before adding any new shim. Do not reintroduce hard frame-phase region blocking; phase instrumentation is diagnostic and must not gate guest execution.
