# M14 LLVM stale-stage recovery — 2026-09-14

## Observed failure

After the first LLVM runtime attempt reached guest execution and faulted at the Broadway Program exception vector, the next `M14-LLVM-UPDATE-RUN.cmd` invocation stopped before LLVM preflight/build with:

```text
error: staged player DLL does not match the normal C build output.
Refusing to use it as an LLVM rollback source; run M14-UPDATE-RUN.cmd once first.
```

## Diagnosis

The transactional runner has two distinct local artifacts:

- `.openspm/m14/local-module-build/module/openspm_local_native.dll` — independently built normal-C module and rollback authority.
- `.openspm/m14/windows-candidate/User-OpenSPM/native/openspm_local_native.dll` — launch staging slot temporarily overwritten by the LLVM candidate.

A sufficiently hard console/process interruption can prevent the Python runner from regaining control and executing its inner `finally` restore. The staging slot can therefore be stale even though the independent normal-C artifact is still intact.

The old preflight treated any staged/known-good hash mismatch as fatal and required a full `M14-UPDATE-RUN.cmd` merely to recopy that already-available known-good artifact.

## Fix

`tools/native_bridge/run_m14_llvm_candidate.py` now:

1. Requires the independent normal-C artifact and its generated identity header.
2. Verifies that identity against the current player-owned `main.dol` and the existing relF/v19 seam fingerprint.
3. Only after that verification, compares the candidate staging DLL with the independent normal-C artifact.
4. If the staging DLL is missing or differs, copies the known-good C DLL into the staging slot and verifies both size and SHA-256.
5. Continues the LLVM A/B normally.

This recovery does **not** trust the stale staged DLL, does not bless an LLVM artifact as rollback state, and does not weaken the player-code identity checks.

## Expected next-run marker

A recovered run may print:

```text
[OpenSPM] Repaired stale/missing staged player DLL from the independently built known-good C module; no normal rebuild was required.
```

It should then continue to `[LLVM 2/7]` and eventually the instrumented LLVM launch.

## Current runtime target

The next meaningful result remains the first-fault diagnostic added after the first LLVM boot attempt. Look for:

```text
M14 LLVM first guest exception:
```

and capture `srr0`, `srr1`, `program`, decoded Program-cause flags, `raw`, LR/CTR/CR/XER, and GPRs. That identifies the actual PPC instruction/control-flow origin of the early 0x700 exception.
