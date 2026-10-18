# M14 LLVM seq_logo stage-1 scheduler probe — 2026-09-16

## Runtime that motivated this probe

Player runtime `launch(20260916-014931).log` ran revision `441b23d2dd4162578d1f16958c36850734ad48fc` with dev38.

### dev38 result: the LLVM re-entry partition fix is real

`0x8027551C` is now a hard LLVM partition and the runtime proves the native region begins there:

```text
M14 LLVM scheduler reentry query ...
pc=0x8027551C range=[0x8027551C,0x802755F8)
exact_start=yes bad_geometry=0
```

The first retrace also genuinely escaped idle:

```text
M14 LLVM scheduler reentry escaped idle #1 run=10
before=0x8027551C after=0x80271D98
```

Therefore the old interior-entry geometry `[0x802753D0,0x802755D0)` was a real LLVM bug. Keep the `0x8027551C` compiler partition permanently.

### But the post-relF stall remains

The same run still reaches the proven live relF/BSS layout:

```text
relF base  = 0x80CE7220
relF prolog= 0x80D2533C
BSS base   = 0x80CE6340
BSS size   = 0x00000EBC
```

Then `seq_logo` reaches stage 1, produces exactly one post-relF `GXDrawDone` / second display copy, and falls back to permanent scheduler idle:

```text
pc=0x8027551C lr=0x8027551C ctr=0x80274E0C
seq=0 stage=1
draw_done=1
copies=2
```

That state persists for thousands of retraces. NAND dev37 remains silent, so `NANDCheckAsync` is not reached before this stall.

## Generated-C control comparison

Known-playable generated-C runtime `launch(20260914-165002).log` proves the intended behavior at the same sequence point:

```text
seq=0 stage=1
safepoints=32  copies=32  draw_done=31
...
safepoints=64  copies=64  draw_done=63
...
seq=0 stage=2
```

`spmarioMain` continues at roughly one update per retrace. The healthy C log does **not** print `M14 native retrace scheduler transfer`, so the special scratch-event OSLoadContext/rfi commit is not the ordinary per-frame mechanism. The normal path is consistent with retrace mutating Nintendo scheduler RAM and persistent `SelectThread` observing that shared state.

## Existing revision-2 scheduler proof reused

Do not import absolute SDK addresses from other R8PE01 revisions. Earlier US2 generated-code analysis already proved the coordinates needed here:

```text
SelectThread             0x802753D0
SelectThread idle        0x8027551C
OSWakeupThread           0x802761CC
OS_CURRENT_CONTEXT       0x800000D4
OS_CURRENT_THREAD        0x800000E4
RunQueueBits             r13 - 29552
RunQueueHint             r13 - 29556
```

`OSWakeupThread` drains sleepers, marks them ready, sets the relevant `RunQueueBits` bit via `SetRun()`, sets `RunQueueHint`, and requests rescheduling. These remain guest-authoritative; the host must not fabricate them.

## dev39 / bridge v29

Active LLVM bridge:

```text
legacy_c_adapter_v29.c.in
module=m14-dev39-llvm-stage1-scheduler-probe
```

No guest state is changed.

### Scheduler snapshot

For delivered retraces, v29 snapshots before/after:

- live r13;
- `RunQueueBits = r13 - 29552`;
- `RunQueueHint = r13 - 29556`;
- `OS_CURRENT_CONTEXT`;
- `OS_CURRENT_THREAD`;
- sequence/stage;
- persistent CPU PC;
- retrace counter;
- any recognized scheduler-transfer context/PC.

Baseline logging is bounded to four observations; `seq=0 stage=1` logging is bounded to sixteen observations.

Expected line:

```text
M14 LLVM scheduler wake probe stage1-#...
  runq@...=before->after
  hint@...=before->after
  context=before->after
  thread=before->after
  transfer=context/pc
  pc=before->after
```

Interpretation:

1. `runq 0 -> nonzero`, persistent CPU remains idle: Nintendo queued a runnable thread but LLVM failed to consume it; inspect `SelectThread` execution/load semantics next.
2. `runq 0 -> 0`, no transfer, no frame: VI did not leave a runnable thread. Trace the actual async wake source rather than patching scheduler state.
3. nonzero/transfer plus frame progress: scheduler edge is alive; follow the next liveness frontier.

Because the snapshot surrounds the whole underlying native run, a very short-lived queue bit can theoretically be consumed before the after-snapshot. The companion async-service probe below provides a second independent signal.

### Stage-1 async service probe

v29 wraps the already-authoritative host-service dispatcher and logs, without modifying, any `seq=0 stage=1` service that returns a nonzero guest callback event. It also always observes the two GameFS player-disc read seams:

```text
0x80279BF4 async GameFS read
0x80279CDC sync GameFS read
```

Expected line:

```text
M14 LLVM stage1 async-service probe ...
target=... result=... event=... argc=... args=[...]
```

This is intended to answer whether the generated-C path is being woken by a DVD/file completion or another hosted async event that LLVM never reaches/completes.

## Next player command

```text
M14-LLVM-UPDATE-RUN.cmd
```

Do not change the normal generated-C path. The transactional LLVM runner must restore the independent known-good C DLL after the test.

## Next-session decision tree

- If build fails: fix v29 source/CMake only; do not bypass validation.
- If `RunQueueBits` becomes nonzero after stage-1 retrace but PC remains idle: instrument the exact `SelectThread` load/branch around `0x8027551C` and compare LLVM vs generated-C memory semantics.
- If `RunQueueBits` remains zero and no stage-1 async event appears: identify the sleeping thread/queue from the active-thread list and trace the producer that should wake it.
- If a stage-1 async service returns a callback but no frame resumes: trace that callback through the v7 scratch executor and scheduler mutation.
- If stage 2 / repeated DrawDone resumes: retain dev38 partition and remove/retire diagnostic-only v27/v29 layers before performance A/B.
