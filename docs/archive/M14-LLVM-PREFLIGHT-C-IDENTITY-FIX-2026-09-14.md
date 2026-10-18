# M14 LLVM preflight: known-good C identity anchor — 2026-09-14

## Failure observed

The first player-side `M14-LLVM-UPDATE-RUN.cmd` attempt stopped before any LLVM
compiler work with:

```text
[LLVM 1/7] Verifying player-owned DOL/retail-relF revision and v19 seam shape...
error: transactional LLVM candidate failed: clean main.dol identity mismatch:
expected <missing state>, got 316fe3a88654cf55f164a493fdc7d872fb7362298613e335a2240b4921bfe7a0
```

This was an OpenSPM preflight bug, not evidence of a wrong game image and not an
LLVM/toolchain failure.

`run_m14_llvm_candidate.py` assumed
`state/m14-runtime-frontier.json -> game.main_dol_sha256`. Current frontier
schema 26 has no `game` object containing that field, so `expected` was always
empty and every valid DOL failed closed.

## Correct identity authority

The transactional LLVM experiment already requires an independently built normal
C module at:

```text
.openspm/m14/local-module-build/module/openspm_local_native.dll
```

The normal local-module builder creates that DLL together with:

```text
.openspm/m14/local-module-build/stage/identity/openspm_local_identity.h
```

That header is generated directly from the player-owned `sys/main.dol` and
contains:

```c
OPENSPM_LOCAL_SOURCE_SHA256[32]
```

This is a stronger and more relevant authority than a duplicated repo-state
field. The LLVM candidate is supposed to A/B against the known-good C DLL, so its
compiler input must be byte-identical to the DOL which produced that C build.

## Fix

`run_m14_llvm_candidate.py` now:

1. reads the 32-byte SHA-256 from the known-good C module identity header;
2. rejects missing/malformed identity state with an instruction to run normal
   `M14-UPDATE-RUN.cmd` once;
3. hashes the current extracted `.openspm/m14/game/sys/main.dol`;
4. requires the two hashes to match exactly before relF or LLVM work;
5. then retains all existing retail-relF structural and v19 seam checks.

The failure text now explicitly says:

```text
clean main.dol identity mismatch against known-good C module
```

so future failures identify the real source of the expected hash.

The parser was sanity-checked against the player's observed DOL SHA-256:

```text
316fe3a88654cf55f164a493fdc7d872fb7362298613e335a2240b4921bfe7a0
```

and reconstructs exactly 32 bytes / the same digest.

## Safety impact

This does not weaken preflight. It removes a stale schema dependency and tightens
the A/B invariant:

```text
LLVM compiler main.dol
        ==
main.dol identity embedded while building known-good C rollback module
```

No Nintendo code/assets are stored in the repository; the SHA-256 is identity
metadata only.

Normal `M14-UPDATE-RUN.cmd` remains unchanged.

## Next action

Pull current `m14-native-execution` and rerun:

```text
M14-LLVM-UPDATE-RUN.cmd
```

If step 1 passes, the first LLVM experiment has finally advanced beyond the
preflight-only failure. Triage the next earliest failing stage rather than
bypassing it.
