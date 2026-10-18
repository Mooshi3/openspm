# M14 LLVM v35 — VI wake-tail truth frontier (2026-09-16)

## Player log analyzed

`launch(20260916-040549).log`, repo revision `187656f54809...`, module `m14-dev44-llvm-postframe-idle-truth`.

## What dev44 proved

This run finally isolates the post-frame dead state to the VI handler's wake tail rather than the generic Revolution OS scheduler.

Before the dead state, the player-derived `VIWaitForRetrace` machinery is healthy:

- `retraceQueue = 0x8056D768`
- `retraceCount = 0x8056D78C`
- waiter thread/context = `0x804E5A58`
- `OSSleepThread = 0x802760E0`
- waiter LR on entry to sleep = `0x80281720` (`VIWaitForRetrace` call return)

The bounded pre-idle ring shows the same waiter repeatedly entering `OSSleepThread(0x8056D768)` at ticks 282, 283, 284, 285, 286, 287, 292 and 303. The queue is empty at each sleep entry because the previous retrace woke/dequeued it.

At tick 303 the game completes its first real DrawDone / second display copy and enters permanent scheduler idle:

- persistent PC: `0x80304724 -> 0x8027551C`
- `OS_CURRENT_CONTEXT`: `0x80507718 -> 0x80507B30` (idle context)
- `OS_CURRENT_THREAD`: `0x80507718 -> 0`
- VI queue already contains waiter `0x804E5A58`
- `RunQueueBits = 0`
- `RunQueueHint = 1`
- `retraceCount = 0x12F`

Most importantly, every later host retrace advances `retraceCount` (`0x12F -> 0x130 -> 0x131 ...`) while:

- queue head/tail remain `0x804E5A58`
- `RunQueueBits` remain zero
- `OS_CURRENT_THREAD` remains zero
- persistent PC remains `0x8027551C`

That is impossible for a normally completed `OSWakeupThread(&retraceQueue)` on a non-empty queue. During healthy stage-1 retraces the exact same VI handler drains this exact queue and sets `RunQueueBits=0x00800000`.

Therefore these hypotheses are now retired for this frontier:

- missing host retrace delivery;
- stale `retraceCount`;
- wrong VI queue identity;
- `VIWaitForRetrace` not sleeping;
- generic SelectThread inability to see a healthy run queue;
- dev41/dev42 scratch restored-context capture theory.

The remaining split is much narrower:

1. dead-state `__VIRetraceHandler` updates `retraceCount` but skips/escapes before `OSWakeupThread`; or
2. it enters `OSWakeupThread`, but that function fails to dequeue/set-ready for waiter `0x804E5A58`.

## dev45 implementation

Active source:

`tools/native_bridge/legacy_c_adapter_v35.c.in`

Module:

`m14-dev45-llvm-vi-wake-tail-truth`

v35 wraps the native-region query callback from dev44 and passively counts/logs regions containing three already-proven US2 SDK coordinates:

- `__VIRetraceHandler = 0x80280600`
- `OSWakeupThread = 0x802761CC`
- ordinary VI handler return = `0x80280E0C`

No region is blocked. No guest register/RAM/scheduler state is changed.

The probe keeps bounded PRE and DEAD logs, using dev44's `g_m14_v44_idle_active` to distinguish the post-DrawDone permanent-idle period. At wake entry it records live LR, which exposes the real caller return PC without guessing a VI-handler callsite.

Expected log prefix:

`M14 LLVM VI wake-tail PRE|DEAD HANDLER|WAKE|TAIL ...`

At destruction, when available:

`M14 LLVM VI wake-tail truth stats handler_pre=... handler_dead=... wake_pre=... wake_dead=... tail_pre=... tail_dead=...`

## Next-run decision tree

### A. DEAD HANDLER and DEAD TAIL appear, but DEAD WAKE does not

This is the strongest result. The VI handler is completing an ordinary return while skipping the wake call despite incrementing `retraceCount`.

Next step: derive the exact player-local `__VIRetraceHandler -> OSWakeupThread` BL from the verified DOL and inspect/partition the controlling branch immediately before it. Compare the live condition registers/state between healthy PRE and DEAD handler executions. Do not modify scheduler state.

### B. DEAD HANDLER and DEAD WAKE appear; queue remains populated / runq zero

The call is reached. Move inside `OSWakeupThread`.

Trace the waiter `0x804E5A58` OSThread state and the dequeue/SetRun path before and after wake entry. Derive offsets from verified generated code or SDK structure rather than guessing. The likely target is thread state / queue-link corruption or an LLVM continuation partition inside `OSWakeupThread`.

### C. DEAD HANDLER appears, but neither DEAD WAKE nor DEAD TAIL appears

The scratch handler escapes/nonlocally exits between count update and its normal tail. Trace the last native region/PC reached in the event CPU and add a hard partition at that continuation if needed. Do not blame VI or scheduler RAM.

### D. No DEAD HANDLER query at all

The native-region query is not a reliable observation point for the handler entry in this direct-call shape. Do not infer that the handler did not run because `retraceCount` proves it did. Instrument the scratch event loop itself around `dolrecomp_call()` or add fail-closed player-local partitions for handler/wake/tail.

## Performance work remains deferred

Do not start LLVM vs generated-C performance A/B yet. First reach fully playable LLVM. Then profile/PGO and compare heavy scenes.
