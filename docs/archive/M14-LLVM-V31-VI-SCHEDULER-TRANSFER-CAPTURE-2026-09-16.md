# M14 LLVM v31 — VI scheduler-transfer capture

Date: 2026-09-16
Branch: `m14-native-execution`

## Player evidence

Latest player log: `launch(20260916-024528).log` from repo revision `df9b835f5116`.

The dev40 build activated successfully and derived the exact R8PE01 revision-2 scheduler context seam:

```text
SelectThread OSSaveContext call = 0x802754CC
saved context PC+4              = 0x802754D0
scheduler idle                  = 0x8027551C
```

Runtime region queries prove the generated LLVM partition really begins at `0x802754D0`:

```text
pc=0x802754D0 range=[0x802754D0,0x8027551C) exact_start=yes
```

The first host retrace also causes Nintendo scheduler state to move from the idle context to the game thread:

```text
OS_CURRENT_CONTEXT 0x80507B30 -> 0x80507718
OS_CURRENT_THREAD  0x00000000 -> 0x80507718
```

However the historical event-transfer telemetry still reports:

```text
transfer=0x00000000/0x00000000
```

and no `M14 native retrace scheduler transfer ...` line appears before `native retrace #1 delivered`.

Later startup remains unchanged from dev39/dev40:

- `seq=0 stage=1` is reached;
- VI repeatedly sets `RunQueueBits=0x00800000` and `RunQueueHint=1`;
- Nintendo scheduler code consumes those bits;
- relF base/prolog still match `0x80CE7220 / 0x80D2533C`;
- BSS still matches `0x80CE6340 / 0xEBC`;
- exactly one real DrawDone and two display copies occur;
- the persistent CPU eventually parks forever at `0x8027551C` while clean 60 Hz retraces/timebase continue.

So dev40 fixed/proved compiler geometry but did not restore sustained frame-thread liveness.

## Important correction: dev40 `bad_geometry` was diagnostic noise

The log alternated lines like:

```text
pc=0x802754D0 range=[0x802754D0,0x8027551C)
pc=0x802754D0 range=[0x8027551C,0x802755F8)
```

v30 counted the second shape as `bad_geometry`. That does **not** prove a DolRecomp dispatcher/cache-key bug.

Pinned DolRecomp commit:

```text
40637c4683bd2820ac5b23607ee344720beb26df
```

implements `ppc_native_region_available()` by temporarily placing the queried region only in:

```text
cpu->external_addr  = start
cpu->external_value = end
cpu->external_rid   = PPC_NATIVE_REGION_QUERY_PENDING
```

It deliberately does not update `CPUState.pc` before invoking `host_call`. Therefore `cpu->pc` may describe the previous/materialized architectural PC while `external_addr/external_value` describe the region currently being queried.

For native-region-query diagnostics, `[external_addr, external_value)` is authoritative. Do not diagnose dispatcher corruption from v30's `entry_pc != start` counter.

## DolRecomp RFI / cold-escape audit

The other broad hypothesis was that `OSLoadContext -> rfi` changed `CPUState.pc` but LLVM then returned normally through the native host call chain.

Pinned source does not support that diagnosis.

For non-ModernGekko LLVM output, `DOLIR_TERM_RFI`:

1. materializes architectural CPU state;
2. calls `ppc_rfi(ctx, cia)`;
3. immediately `returnFromBody()`.

Under the active native ABI with cold escapes, a return PC which does not equal the native caller's expected guest return PC is materialized and `_longjmp`s to the top-level generated wrapper. The wrapper's escaped path returns without overwriting `CPUState.pc`.

So DolRecomp has an explicit mechanism for nonlocal guest control flow. The remaining mismatch is narrower than “RFI cannot unwind LLVM calls.”

## The actual dev41 boundary

OpenSPM executes the guest `__VIRetraceHandler` on a scratch `CPUState` which shares RAM with the persistent CPU. The existing v7 event executor already contains an exact scheduler-transfer detector:

```text
current_context changed away from interrupted_context
AND current_context points to valid guest OSContext
AND OSContext.srr0 == event_cpu.pc
```

Only when all three are true does it copy the scratch CPU into `instance->cpu` and allow Nintendo's restored thread to become the persistent execution context.

Generated-C naturally exposed the restored saved PC to that event loop. With LLVM, dev40 made `0x802754D0` a legal native entry, but the scratch event is allowed to enter that region. v31 introduces an explicit event-side capture at that already-proven architectural handoff.

## v31 implementation

New player-local bridge:

```text
tools/native_bridge/legacy_c_adapter_v31.c.in
module version: m14-dev41-llvm-vi-scheduler-transfer-capture
```

v31 wraps v30 and changes no Nintendo scheduler data.

At the beginning of each top-level native run it records the current `OS_CURRENT_CONTEXT` **only if** the persistent CPU is parked at the proven scheduler idle PC `0x8027551C`.

During that underlying run, a native region is rejected only if all of these are true:

1. the query comes from a scratch CPU, not `&instance->cpu`;
2. the top-level run began at `0x8027551C` with a valid interrupted context;
3. the queried region starts exactly at `OPENSPM_LOCAL_SCHEDULER_CONTEXT_REENTRY` (`0x802754D0` for this player DOL);
4. shared `OS_CURRENT_CONTEXT` has changed away from the context observed at the beginning of the idle run;
5. the new context is a valid guest `OSContext`;
6. that new context's saved SRR0 is exactly `0x802754D0`.

Only then v31 sets the native-region query as handled+blocked. DolRecomp's existing interception path materializes the region entry and leaves the scratch native call chain. No register, queue or scheduler bit is modified by v31.

The existing v7 `bridge_commit_m14_retrace_context_transfer()` must still independently pass before any restored CPU state is committed to the persistent instance.

This means v31 does **not**:

- choose a Nintendo thread;
- set `RunQueueBits` or `RunQueueHint`;
- edit an `OSThreadQueue`;
- modify an `OSContext`;
- fake VI state;
- stub `VIWaitForRetrace`;
- force a saved PC.

Nintendo must already have selected a different context and saved exactly the derived PC before the interception can fire.

## Expected next-run markers

Activation:

```text
M14 LLVM VI scheduler-transfer capture active: restored SelectThread PC+4 on a scratch idle-retrace path is materialized back to the existing exact context-transfer detector; Nintendo scheduler state remains authoritative.
```

If the suspected mismatch is real, expect:

```text
M14 LLVM VI scheduler-transfer capture #1 scratch=yes interrupted=0x80507B30 current=0x80507718 saved_pc=0x802754D0 range=[0x802754D0,...); forcing exact event handoff.
M14 native retrace scheduler transfer context=0x80507718 pc=0x802754D0
```

The second line is especially important: it is emitted by the old exact transfer detector, not by v31.

## Decision tree

### Capture fires + historical scheduler-transfer line appears + boot advances

The missing semantic boundary was identified. Keep the player-derived `0x802754D0` partition and replace the diagnostic wrappers with a compact permanent “scratch scheduler transfer must return to persistent dispatcher” rule.

Success is measured by behavior, not just logs:

- DrawDone count rises beyond 1;
- display copies rise beyond 2;
- `spmarioMain` repeats;
- `seq_logo` advances beyond the permanent stage-1 stall.

### Capture fires + scheduler-transfer line appears but stage 1 still stalls

CPU ownership is now correct. Trace the next actual thread sleep/wake queue. Derive `OSSleepThread` fail-closed from the verified DOL and log queue pointers at sleep/wakeup rather than returning to generic scheduler hypotheses.

### Capture never fires while the game still reaches permanent idle

The active wake does not restore through the expected SelectThread saved-context continuation during an idle retrace. Instrument the scratch retrace event immediately after each `dolrecomp_call` with:

- event CPU PC;
- interrupted/current context;
- current context saved SRR0;
- LR/MSR/SRR0/SRR1;
- OS_CURRENT_THREAD.

Do not loosen the exact transfer predicate without that evidence.

### Build failure

Likely v31 wrapper/C ABI wiring. Inspect the exact MSVC diagnostic. The compiler partition itself is already player-runtime proven by dev40; do not remove it merely to get the build through.

## Performance direction

Unchanged. dev41 is a correctness ownership boundary around a host-driven Nintendo interrupt, not a gameplay fallback. Main DOL and relF game logic remain LLVM native. Once LLVM is playable, compare the same heavy scenes against generated-C and then train/use SPM-specific LLVM PGO before judging final throughput.
