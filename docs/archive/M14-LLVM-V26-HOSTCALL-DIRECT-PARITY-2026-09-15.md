# M14 LLVM v26 hosted direct-call parity — 2026-09-15

## Why this exists

The first LLVM run after rebasing relF to its proven live layout crossed the old
`0x80D2533C` missing-native-function frontier successfully. It then remained
alive for thousands of VI retraces but stopped producing game frames:

- `seq=0 stage=1`
- scheduler idle PC `0x8027551C`
- LR `0x8027551C`
- CTR `0x80274E0C`
- only two display copies while retraces continued past 4096
- no new `reason=3/detail=12` missing-native-function boundary

`0x8027551C` is Revolution OS `SelectThread`'s no-runnable-thread idle path. VI
retrace delivery was healthy, so this was not the old missing-retrace problem.
Every guest thread was legitimately asleep waiting for some other completion.

A known-playable generated-C launch leaves `seq=0 stage=1` after roughly a
second and proceeds to stage 2. SPM's early logo sequence runs the NAND capacity
check. `nand_check()` calls `NANDCheckAsync(...)`; its guest `checkCallback`
clears `NAND_FLAG_Waiting`, allowing `nandCheckMain()` and the logo sequence to
advance.

OpenSPM already hosts `NANDCheckAsync` at `0x8029A500` and returns the guest
completion callback. The backend service was therefore not missing.

## Root architectural mismatch

The generated-C pipeline already normalizes direct PPC `bl` instructions whose
target is in `state/m14-host-targets.json` back into semantic side exits. This
prevents DolRecomp from optimizing a direct call to a host-owned Wii/IOS service
into an ordinary native call to Nintendo's implementation.

The LLVM pipeline did not have the equivalent general callsite partitioning.
Its map covered GX seams, frame telemetry and the relF allocation proof seams,
but not arbitrary direct calls to hosted services. Once relF itself became
native, LLVM could therefore execute a direct `bl NANDCheckAsync` as native game
code and bypass OpenSPM's host service/callback completion entirely.

This failure class is broader than NAND and must be fixed generically.

## v26 design

### Player-local seam derivation

`tools/native_bridge/derive_hostcall_llvm_seams.py` scans:

1. every executable text range in the verified clean player `main.dol`; and
2. the proven-live relocated relF executable section.

For every linked PPC branch whose target is in the checked-in M14 host-target
manifest it records:

- call PC
- semantic host target
- PC+4 return

The normal `derive_v19_llvm_seams.py` flow now incorporates these rows into the
same player-local LLVM map/header used by codegen. Both call and return PCs are
hard compiler partition boundaries. No proprietary game bytes are committed.

For this boot frontier the derivation fails closed unless the verified player
code contains at least one direct call to `0x8029A500` (`NANDCheckAsync`).

Generation now reports:

```
M14 LLVM seams derived: ... hosted_calls=N nand_check_calls=M
```

where `M` must be nonzero for this revision.

### Runtime bridge

`legacy_c_adapter_v26.c.in`, module version
`m14-dev36-llvm-hostcall-direct-parity`, intercepts the materialized LLVM
callsite immediately before the guest BL can execute.

It performs only the BL's architectural state transition:

```
LR = call_pc + 4
PC = semantic_host_target
```

and requests the existing detail-12 migration exit for that target.

It deliberately does **not** implement a second hostcall/callback mechanism.
The existing v7 run/event bridge receives the ordinary detail-12 exit, invokes
`openspm.m14.hostcall.r8pe01`, applies the returned CPU state, and executes any
async/nested guest completion callback through the already-proven event path.
This is the same semantic route used by generated-C.

Indirect/top-level entries remain protected by v7's existing hosted-target
native-region classifier.

Runtime activation marker:

```
M14 LLVM hosted direct-call parity active: N verified callsites are materialized before BL and converted to the proven detail-12 OpenSPM hostcall path.
```

Shutdown statistics:

```
M14 LLVM hosted direct-call stats redirects=... nand_check=... seams=...
```

## What the next run should prove

Run `M14-LLVM-UPDATE-RUN.cmd` normally.

Primary success condition: the run must no longer remain indefinitely at
`seq=0 stage=1 / PC=0x8027551C`. The expected progression is the same as the
known-playable generated-C path: NAND check completion arrives, the logo
sequence advances to stage 2, additional display copies are produced, and boot
continues toward title/file select.

If generation reports `nand_check_calls=0` or fails the required boot-target
check, do not weaken the check. That disproves the direct-BL assumption for the
actual player image and the call form must be reclassified.

If runtime reports `nand_check=0` despite a nonzero generated count, inspect the
LLVM callsite partition/query behavior rather than changing NAND semantics.

If `nand_check>0` but stage 1 still does not advance, inspect the returned guest
callback target and `NAND_FLAG_Waiting` transition. The backend service is then
being reached and the remaining defect is callback execution/state propagation.

If stage 2 is reached and a new migration frontier appears, classify that new
frontier normally; do not revert the hostcall parity layer.

## Performance intent

This layer restores correctness only at semantic host boundaries. Ordinary
main-DOL and relF game code remains in LLVM native objects. That is important:
we are not solving liveness by falling back to generated-C execution.

The pinned DolRecomp source (`40637c4683bd2820ac5b23607ee344720beb26df`)
is newer than the merged upstream LLVM performance series that introduced
register-allocator-sized chunks, indexed dispatch and LLVM PGO support. Upstream
measurements show title-dependent but large gains from those mechanisms; the
project still requires a same-scene OpenSPM generated-C vs LLVM A/B before
claiming an SPM-specific FPS win.

Generated-C v13 remains the unchanged rollback path.
