# M14 LLVM v36 / dev46 — main-thread transition truth

Date: 2026-09-16

## Tested input

Player log: `launch(20260916-042252).log`

Tested repo revision: `d2cbbf9ab6e2bd545492b25b55a1c121a25c2c55`

Tested module: `m14-dev45-llvm-vi-wake-tail-truth`

## Major correction: the VI wake path is healthy even in the dead state

dev44 had shown that after the first real DrawDone the outer `run()` snapshots
looked like this forever:

- `retraceCount` increments every host retrace;
- the VI waiter `0x804E5A58` appears linked on `retraceQueue=0x8056D768`;
- `RunQueueBits` appears zero;
- `OS_CURRENT_THREAD` appears zero;
- persistent CPU remains at SelectThread idle `0x8027551C`.

That coarse observation suggested that `__VIRetraceHandler` might update the VI
count but fail to complete `OSWakeupThread`.

dev45 disproved that theory directly.

### Healthy stage-1 wake

At tick 282 the scratch handler entered `OSWakeupThread` with:

- `r3 = 0x8056D768` (real VI retrace queue);
- head/tail = `0x804E5A58`;
- retrace count already incremented to `0x11B`.

Inside the real Nintendo `OSWakeupThread`:

- queue head/tail become zero;
- `RunQueueBits` becomes `0x00800000`;
- handler reaches its normal tail.

### Dead-state wake

At tick 311 the exact same semantic sequence still occurs:

1. scratch `__VIRetraceHandler` entry;
2. `OSWakeupThread(0x8056D768)` sees waiter `0x804E5A58`;
3. queue head/tail become zero;
4. `RunQueueBits` becomes `0x00800000`;
5. handler reaches its normal tail at the known `0x80280E00..0x80280E10` region.

This repeats at ticks 312, 313, 314, etc.

Therefore **VI delivery, retraceCount, OSWakeupThread, run-queue insertion, and
handler return are all working after the first DrawDone**.

The reason dev44's next outer snapshot again saw waiter-on-queue + runq=0 is now
clear: between the scratch event and the next outer `run()` boundary, Nintendo's
persistent scheduler selects `0x804E5A58`, that VI waiter runs, observes the new
retrace count, and immediately goes back through `VIWaitForRetrace ->
OSSleepThread(retraceQueue)`. It consumes its own run-queue bit before dev44 can
sample again.

Do not revisit the VI wake/count theory unless later evidence contradicts this
exact before/inside/after proof.

## Actual remaining failure

The independent persistent main thread disappears.

At the first post-frame idle transition:

- current thread before: `0x80507718`;
- current context before: `0x80507718`;
- persistent PC before: `0x80304724`;
- LR before: `0x803046B0`;
- CTR before: `0x80304294`;
- SP before: `0x80584828`;
- r3 before: `0x0000007F`;
- after: current thread `0`, context `0x80507B30`, PC `0x8027551C`.

This transition is what strands stage 1. The VI waiter continues waking and
re-sleeping normally afterward, but it is not the game/main thread.

dev44's eight-entry pre-idle `OSSleepThread` query ring contains only the VI
waiter `0x804E5A58` sleeping on `0x8056D768`, including ticks immediately before
the transition. It does not show `0x80507718` entering `OSSleepThread` through a
query-visible path.

Important caveat: absence from that query ring does not prove the main thread did
not execute `OSSleepThread`; LLVM can linked-call guest functions without
consulting the native-region query hook. dev46 therefore inspects the OSThread
object itself instead of drawing conclusions from query visibility.

## dev46 implementation

Active source:

`tools/native_bridge/legacy_c_adapter_v36.c.in`

Module version:

`m14-dev46-llvm-main-thread-transition-truth`

This layer is observation-only and includes the full dev45 -> dev44 -> ... chain.

On the first `seq=0/stage=1` transition from a real current thread into
`0x8027551C` with `OS_CURRENT_THREAD == 0`, dev46 captures the disappearing
thread's TCB before and after the underlying run.

It uses the standard Dolphin/Revolution OSThread layout, which is independently
consistent with the already proven M14 OSContext SRR0 offset `+0x198`:

- OSContext: `+0x000 .. +0x2C7`
- state/attributes: `+0x2C8`
- suspend count: `+0x2CC`
- effective priority: `+0x2D0`
- base priority: `+0x2D4`
- exit value: `+0x2D8`
- queue links: `+0x2DC/+0x2E0`
- generic links: `+0x2E4/+0x2E8`
- active links: `+0x2EC/+0x2F0`
- stack bounds: `+0x2F4/+0x2F8`
- saved r1/r3/LR/CTR/SRR0/SRR1 from the embedded OSContext.

Expected state values:

- `1 = READY`
- `2 = RUNNING`
- `4 = WAITING`
- `8 = MORIBUND`

It also dumps three 8-word raw PPC rows around each of the transition's last
persistent PC, LR, and CTR. These bytes are read from the player's own loaded DOL
memory at runtime and are diagnostic only.

If the after-state is WAITING, dev46 performs one bounded read-only scan of
`0x80400000..0x80600000` for adjacent words referencing the disappearing thread,
reporting up to 16 queue-reference candidates. This is intended to identify a
single-waiter `OSThreadQueue` or a containing message/mutex object without
fabricating scheduler state.

## Expected markers

Activation:

`M14 LLVM main-thread transition truth probe active:`

Capture:

`M14 LLVM main-thread transition captured ...`

TCB:

`M14 LLVM main-thread truth BEFORE ...`

`M14 LLVM main-thread truth AFTER ...`

Player instructions:

`M14 LLVM main-thread PPC PC ...`

`M14 LLVM main-thread PPC LR ...`

`M14 LLVM main-thread PPC CTR ...`

Possible queue scan:

`M14 LLVM main-thread queue-ref candidate ...`

`M14 LLVM main-thread queue-ref scan complete ...`

## Next-run decision tree

### AFTER state = WAITING

The main thread intentionally entered a blocking path. Use the queue-ref
candidates plus raw PPC window to identify the primitive/object. If the raw code
contains a direct BL to `OSSleepThread`, derive and partition that callsite from
the verified player DOL. If it is a higher-level primitive (message queue, mutex,
join, alarm, DVD/async wait), trace the producer/wakeup side of that exact object.

Do not patch RunQueueBits or force the thread runnable.

### AFTER state = MORIBUND

The main thread exited/was cancelled. Determine whether this is architecturally
normal (another game thread should take over) or an LLVM-only premature thread
return. Decode the PC/LR window and trace the thread entry/OSExitThread path.

### AFTER state = READY

A READY thread vanished from the run queues. That would be a scheduler data
integrity bug. Compare TCB queue links/priority with RunQueueBits and SelectThread
before considering any semantic host workaround.

### AFTER state remains RUNNING

The scheduler globals and TCB disagree. Trace the exact context/thread-global
writes between `0x80304724` and `0x8027551C`; do not infer a normal sleep.

### TCB invalid/corrupt

Validate the thread object/layout against the healthy VI waiter and the known
OSContext fields before changing scheduler behavior.

## Preserved conclusions

- dev38 scheduler idle compiler partition remains valid.
- dev40 saved-context partition remains valid.
- dev41/dev42 scratch-transfer interception theory is retired.
- dev43 proves real VI queue/count derivation.
- dev44 proves the outer dead-state shape but its apparent no-wake interpretation
  is superseded by dev45.
- dev45 proves the real dead-state VI wake executes completely.
- No fake VI wake, fabricated runqueue bit, forced thread selection, or emulator
  fallback is justified.
