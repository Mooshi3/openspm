# M14 LLVM v33 — VIWaitForRetrace queue/count truth frontier

Date: 2026-09-16  
Branch: `m14-native-execution`

## Player evidence from dev42

Latest player log: `launch(20260916-030539).log`, revision `d01ee4d5e168`.

The runtime still reaches the established correctness frontier:

- real DOL startup and hosted seams execute;
- the fixed scheduler-idle LLVM partition at `0x8027551C` is active;
- the saved-context LLVM partition at `0x802754D0` is active;
- host VI retraces continue at roughly 60 Hz;
- relF allocates exactly at `0x80CE7220`, with prolog `0x80D2533C`;
- relF BSS allocates exactly at `0x80CE6340`, size `0xEBC`;
- one real GX DrawDone completes and total display-copy count reaches 2.

The failure remains unchanged after that first frame: `seq=0 stage=1`, DrawDone remains 1,
display copies remain 2, and the persistent CPU parks forever at SelectThread idle
`0x8027551C` while retraces continue.

## dev41/dev42 timeline hypothesis is retired

dev41/dev42 attempted to stop a hypothetical scratch VI event from entering Nintendo's restored
SelectThread continuation `0x802754D0` before the existing event-context transfer detector could
capture it.

The dev42 log finally made the ordering mistake explicit. It reports:

```text
M14 native retrace #1 delivered handler=0x80280600 context=0x80507B30
M14 LLVM VI transfer candidate #1 rejected=persistent-cpu ... range=[0x802754D0,0x8027551C)
...
context=0x80507B30->0x80507718 thread=0x00000000->0x80507718
```

Inspection of `bridge_m14_service_retrace()` shows that `M14 native retrace #N delivered` is logged
**after** `bridge_run_m14_event()` has already returned. Therefore every subsequent
`0x802754D0` region query in that timeline belongs to the persistent CPU's normal Nintendo
SelectThread work, not the scratch `event_cpu`.

dev42's `persistent-cpu` rejection is correct. There is no evidence that the scratch event ever
crosses into the restored thread. Do not weaken dev41/dev42 to intercept those persistent
entries.

This also matches the known-playable generated-C architecture: the scratch VI handler mutates
shared scheduler RAM and returns; the persistent scheduler then observes the runnable thread and
performs Nintendo's ordinary context switch. The special event-context transfer path is not the
normal frame wake mechanism.

## New target: the first real VIWaitForRetrace sleep

The remaining divergence happens after the first real frame. The most valuable question is now
what Nintendo's main thread does at `VIWaitForRetrace`.

Public RVL SDK source gives the relevant semantics:

```c
count = retraceCount;
do {
    OSSleepThread(&retraceQueue);
} while (count == retraceCount);
```

This gives two independent pieces of state to observe:

1. `retraceQueue` head/tail — is the main thread actually waiting there, and does the VI handler
   drain/wake it?
2. `retraceCount` — does the out-of-band LLVM VI handler advance the SDK count which makes
   `VIWaitForRetrace` leave its do/while loop after wake?

A wake can be structurally correct yet still look like permanent scheduler idle if `retraceCount`
never advances: the restored thread would immediately execute the loop condition and sleep again.
Conversely, an empty VI queue while the CPU is idle proves the main thread is blocked on something
else and we should stop blaming VI.

## Player-local derivation — no guessed OSSleepThread or queue address

`tools/native_bridge/derive_v19_llvm_seams.py` now extends the existing fail-closed R8PE01 scanner.

The scanner starts from already-proven revision-2 coordinates:

```text
VIWaitForRetrace     0x802816F8
OSDisableInterrupts  0x80271E28
OSRestoreInterrupts  0x80271E50
```

Inside the player's actual `VIWaitForRetrace` body it requires:

1. a BL to `OSDisableInterrupts`;
2. exactly one other linked call between disable and restore;
3. a BL to `OSRestoreInterrupts`;
4. a terminating BLR within the bounded function scan.

The sole middle call is treated as the **player-proven OSSleepThread target**. The scanner then
adds hard LLVM partitions at:

- `VIWaitForRetrace` entry;
- the VI sleep call;
- its PC+4 return;
- the derived `OSSleepThread` target.

It emits:

```c
OPENSPM_LOCAL_VI_WAIT_FOR_RETRACE
OPENSPM_LOCAL_OS_SLEEP_THREAD
OPENSPM_LOCAL_VI_SLEEP_CALL
OPENSPM_LOCAL_VI_SLEEP_RETURN
```

No OSSleepThread address from EU0, another title, or another SDK build is trusted at runtime.

### Queue/count expression derivation

A deliberately tiny PPC constant-expression pass follows only address-building forms needed by
SDK code (`addi`, `addis/lis`, `ori/oris`, `mr`, and effective addresses of `lwz`) while respecting
EABI volatile GPRs across linked calls.

At the derived sleep call it records the expression currently in `r3`; that is the actual
`retraceQueue` passed to `OSSleepThread`.

If exactly one `lwz` effective address appears both before and after the sleep inside the interrupt
bracket, the scanner records it as `retraceCount`. Address expressions can be:

- absolute;
- r13-relative;
- r2-relative.

Queue derivation is required. `retraceCount` derivation is optional and is explicitly marked
unavailable if the retail compiler shape is ambiguous. The build fails closed if the VI wait or
queue shape cannot be proved.

The generated header now also contains:

```c
OPENSPM_LOCAL_VI_SLEEP_QUEUE_MODE
OPENSPM_LOCAL_VI_SLEEP_QUEUE_VALUE
OPENSPM_LOCAL_VI_RETRACE_COUNT_MODE
OPENSPM_LOCAL_VI_RETRACE_COUNT_VALUE
```

The synthetic scanner self-test proves an absolute queue/count example and the generated map
partitions.

## v33 runtime probe

Active player-local module:

```text
m14-dev43-llvm-vi-wait-queue-truth
```

`legacy_c_adapter_v33.c.in` changes **no guest state** and deliberately creates no diagnostic
fallback/exception/side exit.

Around each ordinary top-level native run it snapshots:

- derived VI queue address/head/tail;
- derived `retraceCount` when available;
- proven `RunQueueBits` and `RunQueueHint`;
- `OS_CURRENT_CONTEXT`;
- `OS_CURRENT_THREAD`;
- seq/stage;
- persistent PC;
- host retrace tick.

Expected activation line:

```text
M14 LLVM VI wait-queue truth probe active: VIWait=0x........ sleep_call=0x........ OSSleepThread=0x........ queue_expr=... resolved_now=... retrace_count_expr=...; dev41/dev42 capture hooks bypassed=yes; guest state unchanged.
```

Relevant transitions are logged as:

```text
M14 LLVM VI wait truth stage1-#... tick=... queue@0x........ head=...->... tail=...->... count@0x........=...->... runq=...->... context=...->... thread=...->... pc=...->...
```

For this run v33 bypasses only the obsolete dev41/dev42 host-call interception wrappers. dev40's
read-only scheduler-region telemetry and every older correctness/host-service seam remain active.
The nested wrappers are restored before destroy.

## Next player run

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

Interpret the result in this order.

### 1. Build-time VIWait scanner rejects the retail shape

This is useful evidence, not a reason to loosen the scanner. Read the exact error and extend the
small instruction/address decoder to the player's real compiler pattern. Keep fail-closed
revision verification.

### 2. Queue is nonempty before retrace, drains after retrace, and retraceCount increments

The VI wake path itself is fully healthy. If the game still reaches permanent idle, trace the
woken thread from `VIWaitForRetrace`'s PC+4/loop condition through the next `OSSleepThread` or
scheduler transition. The blocker is later than the VI event.

### 3. Queue drains, but retraceCount does not increment

This is a strong root-cause lead. Nintendo wakes the waiter, but the SDK loop condition still sees
the old count and can immediately sleep again. Trace the real `__VIRetraceHandler` store to the
player-derived count word and compare generated-C vs LLVM scratch execution.

### 4. Queue drains and immediately becomes nonempty again while count increments

Trace the restored thread's VIWait loop/return semantics and saved register/context state. The
waiter is being woken but re-enqueues unexpectedly despite a changed count.

### 5. Queue remains nonempty across retraces

Compare the player's `__VIRetraceHandler -> OSWakeupThread` queue expression against the derived
VIWait queue. If they differ, derive the handler-side queue pointer from the verified DOL and find
why LLVM scratch execution targets the wrong queue or skips the wake.

### 6. Queue is empty when the post-DrawDone CPU becomes permanently idle

Retire VI as the frame blocker. The main thread is sleeping on a different OS queue. Next trace the
player-derived `OSSleepThread` target generically and identify the queue used immediately before
the final idle transition (DVD/message/mutex/etc.).

### 7. DrawDone starts repeating / stage advances

v33 is read-only, so that would indicate timing-sensitive incidental progress rather than a
semantic fix. Preserve the log and reproduce before claiming success.

## Performance goal unchanged

This work is only to make the LLVM backend semantically playable. Once it reaches the same
playable frontier as generated-C, the performance sequence remains:

1. same-scene LLVM vs generated-C A/B;
2. record an SPM-specific profile in NPC-heavy/Bowser scenes;
3. PGO rebuild;
4. evaluate whether LLVM delivers the throughput gain needed for stable 60+ FPS.
