# M14 LLVM v34 / dev44 — post-frame idle truth

Date: 2026-09-16
Branch: `m14-native-execution`

## Input log

Player log: `launch(20260916-035653).log`
Tested repo revision: `e6cbd75bfaaf3208feac4cc7af06150d84d7bb92`
Active tested module: `m14-dev43-llvm-vi-wait-queue-truth`

## dev43 result

dev43 successfully derived the real US2 `VIWaitForRetrace` sleep seam from the
player's own DOL:

- `VIWaitForRetrace = 0x802816F8`
- sleep call = `0x8028171C`
- derived `OSSleepThread = 0x802760E0`
- `retraceQueue = r13 + 0xFFFF8E68 = 0x8056D768` at the observed live r13
- `retraceCount = r13 + 0xFFFF8E8C = 0x8056D78C`

The first important stage-1 observation is conclusive:

- queue head/tail initially contain waiter `0x804E5A58`
- delivered retrace drains that waiter to zero
- `retraceCount` increments `0x111 -> 0x112`
- Nintendo `RunQueueBits` changes `0 -> 0x00800000`
- `RunQueueHint` changes `0 -> 1`
- the persistent current thread remains `0x80507718`

The same wake/count behavior repeats throughout the useful dev43 window. At
later samples the waiter re-enters the VI queue and the next retrace again drains
it while incrementing `retraceCount` and setting the run queue. Therefore:

1. host 60 Hz retrace delivery is functioning;
2. `__VIRetraceHandler` is mutating the real VI globals;
3. `OSWakeupThread(retraceQueue)` is effective for the observed waiter;
4. the `VIWaitForRetrace` do/while count is not stale;
5. the permanent stall is not explained by a missing VI wake or frozen
   `retraceCount`.

## Failure remains after dev43 log cap

The 64-line stage-1 VI truth window ends at tick 289 while game execution is
still active. After that:

- relF base/BSS placement still matches the runtime model;
- one real GX DrawDone completes;
- total display copies become 2;
- no second DrawDone occurs;
- the persistent CPU enters SelectThread idle `0x8027551C`;
- subsequent authoritative timing remains `seq=0 stage=1`;
- VI retraces continue at ~60 Hz;
- presentation safepoints grow without additional copies/DrawDone.

Thus the exact dead-state queue/runqueue condition occurs after dev43's bounded
log budget.

## dev44 implementation

New bridge:

`tools/native_bridge/legacy_c_adapter_v34.c.in`

Module version:

`m14-dev44-llvm-postframe-idle-truth`

CMake now selects v34 for LLVM.

No guest state or control flow is changed.

### 1. Separate post-frame idle capture

The wrapper watches top-level snapshots but does not spend this budget during
normal stage-1 execution. It arms only on the first transition satisfying:

- `seq == 0`
- `stage == 1`
- before PC is not `0x8027551C`
- after PC is `0x8027551C`

On that transition it logs a full before/after snapshot and then records only:

- delivered-retrace epoch changes;
- VI queue head/tail changes;
- `retraceCount` changes;
- RunQueueBits/RunQueueHint changes;
- current-thread changes.

The record also includes pre/post LR, CTR, SP and r3 around the run that entered
idle.

Expected markers:

`M14 LLVM post-frame idle truth probe active: ...`

`M14 LLVM post-frame idle capture ARMED ...`

`M14 LLVM post-frame idle ENTER #...`

`M14 LLVM post-frame idle STATE #...`

### 2. Bounded pre-idle OSSleepThread query ring

v34 wraps the already-existing native-region query hook read-only. During
`seq=0/stage=1`, if DolRecomp asks to enter either:

- derived `OSSleepThread` target; or
- the verified `VIWaitForRetrace` sleep callsite,

v34 stores the last eight observations in a ring:

- entry/range;
- r3 (candidate sleep queue);
- queue head/tail if r3 maps to guest RAM;
- current context/thread;
- LR and SP;
- retrace tick.

When the first final idle transition is observed, the ring is dumped. Nothing is
blocked or redirected. If direct native calls bypass the query hook, dev44 logs
that the ring was empty instead of perturbing execution to force a sample.

Expected marker:

`M14 LLVM pre-idle sleep ring ...`

or

`M14 LLVM pre-idle sleep ring empty ...`

## Next-run interpretation

### A. Dead idle VI queue contains `0x80507718`

The main thread is actually waiting on VI. Compare each retrace:

- if queue drains and RunQueueBits becomes nonzero but PC remains idle, target
  SelectThread/native memory visibility again, now with direct proof in the
  final dead state;
- if queue does not drain despite `retraceCount` incrementing, inspect
  `OSWakeupThread` queue identity / thread state;
- if queue drains but RunQueueBits remains zero, inspect the main OSThread state
  and suspension count around `SetRun`.

### B. Dead idle VI queue is empty or contains only `0x804E5A58`

The main thread is sleeping somewhere else. Prefer the pre-idle ring:

- if a record has `thread=0x80507718` and `queue_r3 != 0x8056D768`, that queue is
  the new primary target;
- identify its producer/wake source and compare generated-C behavior;
- likely categories include an SDK completion queue (GX/DVD/IPC/etc.), but do
  not assume which one until the queue/caller is proven.

### C. Pre-idle ring is empty

DolRecomp's native ABI likely direct-called the sleep function without a runtime
region query. Do not infer the sleep queue from absence. Next instrumentation
should be compiler-side or derive the current thread's `thread->queue` field from
verified `OSSleepThread` instructions, then snapshot it at the idle transition.

### D. RunQueueBits is nonzero throughout final idle

This becomes direct evidence that SelectThread is failing to consume visible
scheduler RAM in the final state despite the dev38 hard partition. Instrument
the exact `RunQueueBits` load/branch at `0x8027551C` and compare guest RAM with
the LLVM value used by the branch.

## Preserve

Do not regress these proven pieces while pursuing the final wait:

- dev38 hard compiler boundary at `0x8027551C`;
- dev40 `OSSaveContext` PC+4 partition `0x802754D0`;
- exact relF/BSS allocation model;
- host direct-call parity;
- fixed 60 Hz retrace phase lock;
- dev43 player-derived VI queue/count seams;
- dev41/dev42 scratch-transfer hypothesis remains retired.

## User action

Run `M14-LLVM-UPDATE-RUN.cmd` and send the new launch log. First verify module
version `m14-dev44-llvm-postframe-idle-truth`, then search the post-frame idle and
pre-idle sleep-ring markers above.
