# M14 LLVM v27 NAND completion probe — 2026-09-15

## dev36 result

The first `m14-dev36-llvm-hostcall-direct-parity` launch did **not** clear the
post-relF black-screen hang.

What it did prove:

- the player-local LLVM seam table is live (`383` verified hosted callsites);
- at least one direct hosted seam was actually observed by dev36, because the
  activation marker is emitted only after the non-query seam path is entered;
- deterministic relF file placement remains exact at `0x80CE7220`;
- deterministic relF BSS placement remains exact at `0x80CE6340`;
- native VI retraces, timebase, input/safepoints and host event pumping remain
  alive;
- there is no new PowerPC exception or missing-native-function frontier.

The guest still reaches:

```
seq=0 stage=1
pc=0x8027551C
lr=0x8027551C
ctr=0x80274E0C
```

and stays there while retraces continue. `0x8027551C` is Revolution OS
`SelectThread`'s no-runnable-thread idle path, so the game is waiting for an
event/completion rather than burning guest CPU in a bad loop.

## Why NAND is still the leading early-boot candidate

SPM's `nand_check()` sets `NAND_FLAG_Waiting`, submits `NANDCheckAsync`, and
returns while the bit remains set. The guest `checkCallback` is what clears the
waiting bit and records the result. The logo sequence owns a NAND-check event
entry, so losing this async completion is sufficient to leave early boot with
no runnable game work.

Do not equate OpenSPM's `seq=0 stage=1` telemetry with `NandWork.stage`; they are
different state machines. The NAND hypothesis must be tested by observing the
actual hosted call and callback route.

## LLVM partition semantics verified

The pinned DolRecomp source was audited before changing interception policy.

`collect_llvm_partition_points()` inserts every executable symbol-map address
into the LLVM partition-point set. `build_llvm_ranges()` then cuts a generated
range at the next partition point. Therefore the call/return entries emitted by
`derive_v19_llvm_seams.py` are real LLVM function/range boundaries, not labels
used only for naming.

This matters because DolRecomp's native interception API exits at a blocked
range's **start**. Broadly rejecting any range that merely contains a later seam
would be incorrect. dev36's exact-entry interception remains the intended
shape: the map must make the semantic callsite the range start.

## dev37 design

`legacy_c_adapter_v27.c.in` selects module version:

```
m14-dev37-llvm-nand-completion-probe
```

It includes dev36 unchanged and adds bounded, read-only telemetry around the
existing path. It does not synthesize NAND success, clear guest flags, advance
the logo sequence, or replace the guest callback executor.

It observes four boundaries:

1. **NAND native-region query**
   - query PC
   - generated range `[start,end)`
   - NAND seam PC
   - whether seam is the exact range entry
   - whether dev36 blocked the range

2. **dev36 direct-call redirect**
   - call PC / target / PC+4 return
   - resulting PC/LR
   - detail-12 fallback state

3. **existing OpenSPM M14 hostcall service**
   - `NANDCheckAsync` arguments on entry
   - service return code / r3
   - returned `event_target`, argc and args

4. **post-service v7 run return**
   - v7 executes `state.event_target` synchronously before its `run()` returns;
     therefore a successful underlying return after a nonzero event tells us the
     established callback-execution path was traversed rather than bypassed.

The M14 hostcall service function pointer is wrapped and restored; the actual
service implementation and v7 event bridge remain authoritative.

## Expected log markers

Activation:

```
M14 LLVM NAND completion probe active: ...
```

Then, if the NAND path is used:

```
M14 LLVM NAND region query #...
M14 LLVM NAND seam redirect #...
M14 LLVM NAND host service enter #...
M14 LLVM NAND host service exit result=... event=...
M14 LLVM NAND post-service run result=...
```

There are also up to eight generic:

```
M14 LLVM hosted seam observed #...
```

rows so an earlier hosted boundary can be identified if NAND itself is never
reached.

## Next-run decision tree

Run `M14-LLVM-UPDATE-RUN.cmd` normally and preserve the launch log.

- **No NAND region query:** the actual boot path never reaches a direct
  `NANDCheckAsync` seam before sleeping. Use the generic hosted-seam trace and
  identify the real outstanding event instead of patching NAND.
- **NAND query with `exact_entry=no`:** generated objects do not reflect the
  intended map partition even though source derivation found the seam. Audit
  cache/provenance and the emitted function range before changing runtime logic.
- **`exact_entry=yes` but `blocked=no`:** dev36's wrapper composition/predicate
  is wrong at the live callsite.
- **blocked query but no NAND redirect:** native execution exits correctly, but
  the outer dispatch does not invoke the host callback at the materialized PC.
- **redirect but no service entry:** the v7 detail-12 classifier/service route is
  not recognizing the NAND target.
- **service entry/exit with `event=0`:** the backend NAND shim is not returning
  the guest completion callback; fix the service implementation.
- **nonzero event + successful post-service return but still stage 1:** callback
  traversal occurred, so inspect callback target/arguments and guest memory
  state (especially the authoritative NAND waiting/result state).
- **stage advances / copies resume:** the completion path is working; remove
  diagnostic-only v27 wrapping once the permanent difference from generated-C
  is isolated, then continue to the next native frontier.

## Performance note

This probe is temporary and bounded. dev36's semantic exits are only for
host-owned SDK/service boundaries; ordinary SPM main-DOL and relF code remains
LLVM native. The performance objective remains unchanged: reach playable LLVM,
then train and A/B an SPM-specific LLVM PGO build against generated-C M14.
