# M14 LLVM v32 — VI scratch-context scheduler transfer fix

Date: 2026-09-16
Branch: `m14-native-execution`

## Player evidence

Player log: `launch(20260916-030001).log`
Repo revision: `9607c30c05f3`
Module: `m14-dev41-llvm-vi-scheduler-transfer-capture`

The run reproduces the same eventual correctness failure:

- relF base/prolog still match the proven deterministic layout;
- relF BSS still matches the proven deterministic model;
- seq_logo reaches stage 1;
- exactly one DrawDone and two display copies complete;
- the persistent CPU then remains indefinitely at SelectThread idle `0x8027551C`;
- fixed ~60 Hz host retraces and authoritative timebase continue;
- after the stall `OS_CURRENT_CONTEXT` is the idle context `0x80507B30`;
- DrawDone remains 1 and total display copies remain 2.

The important new evidence occurs at the first delivered retrace. Nintendo changes
`OS_CURRENT_CONTEXT` from idle context `0x80507B30` to game context `0x80507718`, and LLVM then
queries the exact derived saved-context continuation region:

```text
M14 native retrace #1 delivered handler=0x80280600 context=0x80507B30
M14 LLVM scheduler context reentry query ... pc=0x802754D0 range=[0x802754D0,0x8027551C) ... exact_start=yes ...
M14 LLVM scheduler wake probe ... context=0x80507B30->0x80507718 ... transfer=0x00000000/0x00000000 ...
```

However, dev41 never emits its own `M14 LLVM VI scheduler-transfer capture` marker. Therefore the
semantic hypothesis was not tested: the capture hook saw the same native-region query chain but
rejected it before reaching the exact restored-context predicate.

## dev41 bug

`legacy_c_adapter_v31.c.in` tried to identify the scratch event CPU with:

```c
instance = bridge_from_cpu(cpu);
if (instance == NULL || instance != g_m14_v41_instance ||
    cpu == &instance->cpu || ...)
    delegate;
```

That ownership test is invalid for retrace events. `bridge_run_m14_event_from_cpu()` creates:

```c
CPUState event_cpu;
event_cpu = *base_cpu;
```

on the stack. It shares Nintendo RAM/MEM2 and callbacks with the persistent CPU but is not embedded
inside `openspm_bridge_instance`. `bridge_from_cpu(event_cpu)` therefore cannot prove ownership and
silently delegates to dev40. This exactly explains why the player log shows the dev40
`0x802754D0` query immediately after retrace #1 but no dev41 capture line.

## v32 implementation

New bridge:

```text
tools/native_bridge/legacy_c_adapter_v32.c.in
m14-dev42-llvm-vi-scratch-transfer-fix
```

v32 includes the complete v31 chain. It leaves v31's `run()` wrapper in place because that wrapper
already records the narrow top-level condition:

- persistent run started at scheduler idle `0x8027551C`;
- `g_m14_v41_idle_run_active=1`;
- `g_m14_v41_interrupted_context` records the context interrupted by the host VI event.

v32 replaces only `CPUState::host_call`. For an exact
`OPENSPM_LOCAL_SCHEDULER_CONTEXT_REENTRY` native-region query while that idle-run window is active,
it proves the event CPU by:

1. using the already-known `g_m14_v41_instance` rather than `bridge_from_cpu(cpu)`;
2. requiring `cpu != &instance->cpu`;
3. requiring the scratch CPU to share exactly the persistent CPU's MEM1/MEM2 backing pointers and
   sizes;
4. requiring Nintendo's shared `OS_CURRENT_CONTEXT` to have changed away from the interrupted
   context;
5. requiring the selected context address to be valid;
6. requiring its saved SRR0 to equal the derived `0x802754D0` continuation exactly.

Only if every condition is true does v32 mark the native-region query handled+blocked. DolRecomp
then materializes `0x802754D0` and returns from the scratch native call chain. The existing v7
`bridge_commit_m14_retrace_context_transfer()` remains authoritative and independently validates
`OS_CURRENT_CONTEXT`, saved SRR0 and materialized PC before copying the scratch CPU into the
persistent CPU.

v32 does not pick a Nintendo thread, edit RunQueueBits, edit an OSContext, fake a VI wake, or alter
queue membership.

## Diagnostic improvement

If the transfer still does not capture, the next log now reports the exact rejected predicate:

```text
M14 LLVM VI transfer candidate #... rejected=persistent-cpu ...
M14 LLVM VI transfer candidate #... rejected=foreign-memory ...
M14 LLVM VI transfer candidate #... rejected=context-not-switched ...
M14 LLVM VI transfer candidate #... rejected=selected-context-unmapped ...
M14 LLVM VI transfer candidate #... rejected=saved-pc-mismatch ...
```

Expected success path:

```text
M14 LLVM VI scheduler-transfer capture v42 #1 scratch=yes interrupted=0x80507B30 current=0x80507718 saved_pc=0x802754D0 ...
M14 native retrace scheduler transfer context=0x80507718 pc=0x802754D0
```

The behavioral success test remains stricter than the marker itself:

- DrawDone must exceed 1;
- display copies must exceed 2;
- `spmarioMain`/frame activity must repeat;
- seq_logo must escape the permanent stage-1 idle loop.

## Next player run

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

Interpret in this order:

1. **Build fails** — fix the v32 bridge composition/C syntax only; do not change the scheduler theory.
2. **v42 capture + v7 scheduler-transfer marker appear and boot advances** — keep the exact capture
   semantics and continue to the next frontier.
3. **v42 candidate rejection appears** — use its explicit reason; do not guess.
4. **No v42 candidate line at all but dev40 still logs exact `0x802754D0` after idle retrace** — the
   remaining issue is the v31 idle-window state lifetime; instrument that wrapper directly.
5. **Capture succeeds but boot still returns to permanent idle** — the restored CPU state is now
   correctly committed; inspect the *next* thread sleep/wake boundary rather than VI delivery or
   compiler partition geometry.

The LLVM performance plan remains unchanged: reach playable correctness first, then perform a
same-scene generated-C vs unprofiled LLVM A/B, record SPM-heavy-scene PGO, rebuild, and repeat the
A/B before judging the backend throughput win.
