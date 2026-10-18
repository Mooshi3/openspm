# M14 LLVM v28 scheduler-idle re-entry — 2026-09-15

## Result of the dev37 run

The dev37 NAND completion probe falsified the active NAND hypothesis.

The run loaded dev37 correctly and generic hosted-call seams were live, but no
NAND-specific region-query, redirect, host-service or callback line appeared
before the permanent stall. Therefore `NANDCheckAsync` is not the event currently
holding seq_logo at stage 1.

The relF work remained healthy:

- relF base `0x80CE7220` exact
- relF BSS `0x80CE6340` / `0xEBC` exact
- relF prolog executed
- one post-relF GXDrawDone completed
- total display copies reached 2

The run then remained indefinitely at Revolution OS SelectThread idle:

```
pc=0x8027551C
lr=0x8027551C
ctr=0x80274E0C
seq=0 stage=1
```

VI retraces continued normally, but DrawDone remained exactly 1 after the
post-relF frame.

## Key generated-C comparison

A known-playable generated-C M14 launch also samples SelectThread idle during
seq_logo stage 1. The important difference is not the idle PC itself.

Generated-C continues executing the game frame loop while stage 1 is active:
DrawDone rises essentially once per retrace (63 by safepoint 64) and seq_logo
advances to stage 2 around frame 88.

LLVM produces one post-relF DrawDone and then never executes another game frame.
Thus the missing liveness is the frame/main-thread scheduler resume, not a NAND
completion.

## LLVM-specific re-entry mismatch

M14's fixed-rate retrace bridge intentionally treats `0x8027551C` as a host
safepoint:

1. the persistent guest reaches SelectThread's no-runnable-thread idle
   continuation;
2. OpenSPM parks execution there until the next 60 Hz deadline;
3. OpenSPM executes `__VIRetraceHandler` in a scratch CPU against the same guest
   RAM, allowing Nintendo OS scheduler queues to be mutated normally;
4. the persistent CPU resumes at exactly `0x8027551C`, where SelectThread should
   observe the now-runnable VI waiter and schedule it.

That architecture predates the LLVM backend and works in generated-C, whose
runtime can enter arbitrary generated continuation labels.

The failing LLVM heartbeat exposed a different geometry:

```
pc=0x8027551C
region=[0x802753D0,0x802755D0)
```

So the host-driven resume PC was an *interior continuation* of a larger LLVM
native region, not a compiler entry/partition point. The host was repeatedly
asking LLVM to resume at a mid-region scheduler continuation after an external
callback changed scheduler RAM.

## v28 fix

`derive_v19_llvm_seams.py` now treats host-driven mid-function re-entry as an
architecture seam, just like semantic host calls and relF allocation seams.

The player-local LLVM map always contains:

```
8027551C 0 openspm_scheduler_idle_reentry
```

The scanner fail-closes unless `0x8027551C` is inside verified player executable
text. The generated header exposes:

```
OPENSPM_LOCAL_SCHEDULER_IDLE_REENTRY
```

and the source-only seam self-test requires the map row.

This is a compiler partition only. No run-queue bit, OS thread state, sequence
stage, timer, or guest register is fabricated.

`legacy_c_adapter_v28.c.in` / module version
`m14-dev38-llvm-scheduler-idle-reentry` wraps dev37 only to verify runtime
geometry. It does not change scheduler behavior.

Activation marker:

```
M14 LLVM scheduler-idle reentry partition active: 0x8027551C is a hard compiler boundary; Nintendo run-queue state remains authoritative.
```

Expected query after the first scheduler idle resume:

```
M14 LLVM scheduler reentry query ... pc=0x8027551C range=[0x8027551C,... ) exact_start=yes ... bad_geometry=0
```

The previous broken shape was:

```
range=[0x802753D0,0x802755D0)
```

If the scheduler resumes a runnable thread, dev38 also logs:

```
M14 LLVM scheduler reentry escaped idle ... before=0x8027551C after=0x........
```

## Next-run decision tree

Run `M14-LLVM-UPDATE-RUN.cmd` normally.

### Best case

`exact_start=yes`, `bad_geometry=0`, idle escapes, DrawDone begins incrementing
again, and seq_logo proceeds to stage 2. Continue boot and classify the next
frontier normally.

### Partition correct but still no wake

If `exact_start=yes` but every run remains at `0x8027551C`, the continuation-entry
hypothesis is falsified. The next target is Nintendo scheduler shared state:
trace the run queue / VI wait queue immediately before and after the scratch
`__VIRetraceHandler` event and compare it with generated-C. Do not revisit NAND.

### Partition did not materialize

If dev38 sees `pc=0x8027551C` with `exact_start=no` or `bad_geometry>0`, do not
patch scheduler state. The DolRecomp map/resume cache did not regenerate the
native range as expected; fix generation/cache invalidation until the native
region starts at `0x8027551C`.

### New frontier

If frame execution resumes and a new detail-12/native frontier appears, treat it
as progress. Preserve this scheduler partition and classify the new boundary.

## Performance relevance

This seam is one address and only changes where LLVM may split a native region.
It does not move ordinary game code back to generated-C or add a per-instruction
host round trip. It is therefore compatible with the reason for the LLVM
migration: keep normal SPM simulation/render preparation in native LLVM objects,
then perform an SPM-specific PGO build once gameplay is restored.
