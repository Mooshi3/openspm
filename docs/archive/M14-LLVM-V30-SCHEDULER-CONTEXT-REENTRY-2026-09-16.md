# M14 LLVM v30 — SelectThread saved-context re-entry frontier

Date: 2026-09-16
Branch: `m14-native-execution`

## Player evidence that changed the diagnosis

Latest player log: `launch(20260916-020733).log` from repo revision `d6f7084b9edf`.

The dev39 scheduler probe falsifies the remaining broad "VI is not waking the game" hypothesis.
During `seq=0 stage=1`, the real recompiled `__VIRetraceHandler` repeatedly changes the proven
R8PE01 revision-2 scheduler globals from:

```text
RunQueueBits  0x00000000 -> 0x00800000
RunQueueHint  0x00000000 -> 0x00000001
```

Nintendo code also consumes the bit back to zero during the same startup interval. Therefore:

- host retraces are being delivered;
- `OSWakeupThread`/`SetRun` scheduler RAM mutations are real;
- LLVM can execute scheduler code and consume runnable bits;
- this is not simply a missing VI interrupt or a permanently broken `SelectThread`.

relF and BSS allocation still match the proven live player layout exactly:

```text
relF base   0x80CE7220
relF prolog 0x80D2533C
BSS base    0x80CE6340
BSS size    0x00000EBC
```

After that, one real DrawDone/two display copies complete. Then the persistent CPU eventually
returns to scheduler idle at `0x8027551C`, the interrupt context becomes the idle context
`0x80507B30`, DrawDone remains permanently at 1, and fixed-rate retraces/timebase continue.

The known-playable generated-C build does not behave this way: it repeatedly leaves scheduler
idle, runs `spmarioMain`/`DEMODoneRender`, and produces a DrawDone roughly every frame.

## Narrower LLVM control-flow mismatch

Revolution OS thread switching contains another non-local guest entry in addition to the dev38
scheduler-idle entry.

`SelectThread` saves the running thread with:

```text
OSSaveContext(&currentThread->context)
```

A later `OSLoadContext` restores that context and ends in `rfi`. The saved SRR0/PC is the
instruction immediately after the linked `OSSaveContext` call. In other words, the awakened
thread resumes at a **PC+4 interior continuation inside SelectThread**, not at a normal function
entry.

Generated-C can dispatch arbitrary PPC labels directly. LLVM native objects require that a PC
used as an external/non-local re-entry be a materialized compiler partition. dev38 proved the
same requirement for the `0x8027551C` scheduler-idle continuation.

The already-proven R8PE01 revision-2 OS coordinates are:

```text
SelectThread          0x802753D0
SelectThread idle     0x8027551C
OSSetCurrentContext   0x8026E57C
OSSaveContext         0x8026E5E4
OSLoadContext         0x8026E664
OSLoadContext rfi     0x8026E738
OSWakeupThread        0x802761CC
VIWaitForRetrace      0x802816F8
__VIRetraceHandler    0x80280600
```

`OSSaveContext=0x8026E5E4` is also fail-closed verified at player build time: the seam scanner
requires exactly one direct BL to that target inside the verified `SelectThread` body.

## v30 implementation

### `derive_v19_llvm_seams.py`

The mature player-local LLVM seam scanner now additionally:

1. verifies the scheduler/context function coordinates are executable in the player's DOL;
2. scans `[0x802753D0, 0x802755F8)` for direct linked calls to `OSSaveContext`;
3. requires exactly one match;
4. derives its `PC+4` saved-context resume address;
5. emits both PCs into the LLVM partition map;
6. emits:

```c
OPENSPM_LOCAL_SCHEDULER_CONTEXT_SAVE_CALL
OPENSPM_LOCAL_SCHEDULER_CONTEXT_REENTRY
```

into `openspm_local_v19_llvm_seams.h`.

The source-only seam self-test now synthesizes this BL and verifies both generated partitions.
All existing GX, relF/BSS, hosted-call and `0x8027551C` idle-reentry seams remain unchanged.

### `legacy_c_adapter_v30.c.in`

Player-local module version:

```text
m14-dev40-llvm-scheduler-context-reentry
```

v30 includes the full v29 chain and changes **no guest scheduler state**. It wraps only
`PPC_HOST_CALL_NATIVE_REGION_QUERY` to prove the new saved-context resume partition has correct
geometry at runtime.

Expected activation marker:

```text
M14 LLVM scheduler context-resume partition active: SelectThread OSSaveContext call=0x........ saved PC+4=0x........ is a hard compiler boundary; Nintendo context state remains authoritative.
```

Expected runtime proof when a saved thread is restored through that continuation:

```text
M14 LLVM scheduler context reentry query #... pc=0x........ range=[0x........,0x........) save_call=0x........ exact_start=yes ... entry_hits=... bad_geometry=0.
```

`exact_start=yes` and `bad_geometry=0` are required. The runtime does not select a thread,
fabricate `RunQueueBits`, stub `VIWaitForRetrace`, or alter OS contexts.

## Next player run

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

Interpret the result in this order:

### 1. Build-time scanner rejects the scheduler seam

Do not weaken the check. Inspect the player's actual `SelectThread` call form/target. The current
`OSSaveContext` coordinate or direct-BL assumption is wrong for the verified DOL.

### 2. Runtime reports context-reentry hits with exact geometry and boot advances

Keep both scheduler partitions permanently. Success evidence is more than the marker itself:
DrawDone/display-copy count must rise beyond 1/2, `spmarioMain` must repeat, and seq_logo should
advance beyond the permanent stage-1 stall.

### 3. Runtime reports context-reentry hits, `exact_start=yes`, but still stalls

The compiler-entry geometry is fixed. Next inspect the restored `OSContext`/`OSSaveContext`
semantics themselves: saved SRR0, LR/CR/MSR and the exact persistent CPU state after rfi. Do not
return to VI/MMIO guesses.

### 4. No context-reentry hit occurs before permanent idle

Then the frame thread is not being restored through this continuation. The next trace should
instrument the actual VI `retraceQueue` head/tail and the first `VIWaitForRetrace -> OSSleepThread`
transition to determine whether the thread is ever enqueued, without fabricating queue state.

## Performance intent remains unchanged

This is a correctness partition, not a performance workaround. Ordinary game code and relF code
remain native LLVM; only an architecturally valid non-local entry is materialized. Once LLVM is
playable, do a same-scene generated-C vs LLVM A/B and then add SPM-specific PGO before judging the
backend's final heavy-scene throughput.
