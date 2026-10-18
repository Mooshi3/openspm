# M14 LLVM dev47 — GX finish semantic completion

Date: 2026-09-16

## Frontier

The LLVM startup path is still correct through `seq=0/stage=1`, first real display copy, and first DrawDone. The permanent-idle defect is now localized to a missing GPU-finish completion event rather than VI/retrace or generic scheduler behavior.

## dev45 correction: VI is healthy

The dev45 wake-tail trace proved the apparent dev44 VI failure was only an outer-sampling illusion. During permanent idle the scratch `__VIRetraceHandler`:

1. reaches the real `OSWakeupThread(0x8056D768)`;
2. removes the VI waiter;
3. sets the real `RunQueueBits=0x00800000`;
4. reaches the ordinary handler tail.

Before the next outer `run()` snapshot the VI waiter consumes that wake and sleeps again in `VIWaitForRetrace`, so dev44's later snapshot naturally sees the queue populated and RunQueueBits cleared again.

Do not return to a missing-VI-wake diagnosis.

## dev46 decisive result

Latest tested revision: `2b507d697f9e761589d47fa09881b09235a0ce5b`.

The main thread `0x80507718` transitions cleanly:

- BEFORE: `state=2/RUNNING`, suspend=0, priority/base=16, exit=`0xFFFFFFFF`, no wait queue;
- AFTER: `state=4/WAITING`, same suspend/priority/exit values;
- its `OSThread::queue` pointer at `+0x2DC` becomes `0x8056D990`;
- queue object `0x8056D990` contains head/tail `0x80507718/0x80507718`.

SeekyCt's public Wii `OSThread` header confirms `+0x2DC` is `OSThreadQueue *queue`. Earlier dev46 diagnostic labels after this field were based on an older layout and should not be used as field names; the queue pointer itself is exact.

With runtime r13=`0x80574900`, the blocking queue is exactly `r13-0x6F70`.

The transition occurs immediately after OpenSPM parses the first real GX DrawDone command, synchronously fences Aurora, and reports `drawdone_presents=1`, `total_display_copies=2`.

## Root-cause model

Public Dolphin/Revolution GX implementations use the same semantic shape:

- `GXDrawDone()` / equivalent emits BP finish (`0x45=2`), clears the finished flag and sleeps on `FinishQueue`;
- the PE-finish interrupt handler sets the finished flag, invokes the optional DrawDone callback, and wakes/broadcasts `FinishQueue`;
- Revolution `OS_INTR_PI_PE_FINISH` is interrupt ID 19.

OpenSPM already recognizes BP `0x45=2` and performs a synchronous host renderer fence. The missing half was guest-visible PE-finish completion. Therefore Nintendo correctly sleeps the main thread on FinishQueue and waits forever.

## dev47 implementation

Active module:

`m14-dev47-llvm-gx-finish-semantic-completion`

Bridge:

`tools/native_bridge/legacy_c_adapter_v37.c.in`

The correction is deliberately semantic rather than scheduler fabrication.

### Runtime player verification

Before delivering any completion, dev47 scans player-owned main-DOL text `[0x80004000,0x80350000)` and requires exactly one direct call to US2 `OSWakeupThread=0x802761CC` immediately preceded by:

`addi r3,r13,-0x6F70`

That exact queue expression comes from the dev46 live TCB result. The scanner then finds the nearest ordinary non-leaf PPC prologue within 0x200 bytes and treats that function as the retail GX finish handler.

If the signature is absent, ambiguous, or lacks a prologue, dev47 logs `REFUSED` and makes no guest-state correction.

### Delivery guard

After each ordinary run slice, dev47 only acts when:

- persistent PC is Nintendo scheduler idle `0x8027551C`;
- the verified `r13-0x6F70` queue has a waiter;
- that waiter's real `OSThread.state` is WAITING;
- its real `OSThread::queue` pointer points back to the exact FinishQueue.

This is the state observed immediately after the already-completed Aurora DrawDone fence.

### Completion semantics

On that exact condition dev47 uses the existing v7 scratch guest-event machinery to run the derived **real Nintendo GX finish handler** with:

- argument 0: `OS_INTR_PI_PE_FINISH = 19`;
- argument 1: live `OS_CURRENT_CONTEXT`.

No TCB state, RunQueueBits, queue links, or DrawDone flag are written by OpenSPM. Nintendo's own handler owns its finished flag, callback and `OSWakeupThread` behavior.

## Expected next-run markers

First, proof/activation:

`M14 LLVM GX finish semantic completion probe/fix active...`

Then one of:

### Success path

`M14 LLVM GX finish semantic completion ARMED: ... wake_call=... derived_handler=...`

followed after first DrawDone by:

`M14 LLVM GX finish semantic completion #1 ... after_head=0x00000000 runq=...`

If the root cause is correct, the main thread should resume, DrawDone/copy counts should continue beyond 1/2, and seq/stage should make new progress. Repeated frames should produce repeated completion counters.

### Signature mismatch

`M14 LLVM GX finish completion REFUSED: ... candidates=...`

Do not weaken the guard blindly. Use the reported candidate count/call/handler plus player PPC to generalize only the exact retail compiler shape.

### Guest handler execution failure

`M14 LLVM GX finish semantic completion FAILED ...`

Inspect the existing `M14 event failure` line. Most likely next boundary would be a PE MMIO access or an indirect callback/native-entry issue; fix that exact boundary while keeping the real handler as semantic owner.

### Handler runs but queue remains populated

Then inspect the derived handler start and wake call. Do not manually force the TCB READY. The derivation likely selected an enclosing function/prologue too early or the event arguments/context need correction.

## Preserved proven facts

Keep permanently unless contradicted by new evidence:

- dev38 scheduler idle boundary `0x8027551C`;
- dev40 SelectThread save/reentry partition `0x802754CC/0x802754D0`;
- VIWait `0x802816F8`, sleep call `0x8028171C`, OSSleepThread `0x802760E0`;
- OSWakeupThread `0x802761CC`;
- VI queue `0x8056D768`, retraceCount `0x8056D78C`;
- VI handler `0x80280600` is healthy through wake and tail;
- main thread `0x80507718` is intentionally WAITING, not dead/corrupt;
- exact main wait queue is `0x8056D990 = r13-0x6F70`;
- the wait begins directly after first host-completed GXDrawDone.

## Next test

Run `M14-LLVM-UPDATE-RUN.cmd` and inspect the dev47 ARMED/completion marker first. If it completes and the game advances, preserve this semantic path and move immediately to the next real frontier rather than continuing scheduler probes.
