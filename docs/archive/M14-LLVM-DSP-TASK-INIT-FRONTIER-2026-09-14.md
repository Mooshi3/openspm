# M14 LLVM DSP task-init frontier — 2026-09-14

## Result from `launch(20260915-012316).log`

The prior CPU->DSP mailbox acceptance fix worked. The LLVM candidate no longer remains in the `DSPCheckMailToDSP` loop at `0x8028986C/0x8028A154`.

The candidate now:

- initializes Aurora/GX,
- arms the native Revolution scheduler-idle retrace source,
- continues delivering the fixed 60 Hz guest retrace,
- raises no guest program/illegal-instruction exception,
- keeps returning from native slices normally (`query_stationary=0`),
- and advances to a new stable frontier at `0x802871E4`.

The repeated frontier is:

```text
pc=0x802871E4
lr=0x802871E4
region=[0x8028711C,0x8028720C)
query_stationary=0
```

Region-query count rises by millions per second while retrace remains at 60 Hz. This is therefore an intentional guest polling loop, not a host/native region that failed to return.

## Exact retail R8PE01 code

The verified player `main.dol` contains:

```text
802871D8  lis   r3,0x8051
802871DC  addi  r3,r3,0x1700       ; DSPTaskInfo @ 0x80511700
802871E0  bl    0x8028997C          ; DSPAddTask
802871E4  lwz   r0,-0x7070(r13)
802871E8  cmpwi r0,0
802871EC  beq   0x802871E4
```

The callback installed in that DSP task is:

```text
802870A8  li    r0,1
802870AC  stw   r0,-0x7070(r13)
802870B0  blr
```

`0x8028997C` matches the Revolution/Dolphin SDK `DSPAddTask` flow: insert the task, set state INIT / flags ATTACHED, and boot it if it is the first DSP task.

The first task descriptor at `0x80511700` contains the observed bootstrap shape including:

- state = INIT,
- priority = 0,
- flags = ATTACHED,
- DRAM DSP address = `0xCD2`,
- `init_cb = 0x802870A8`.

On real hardware the DSP task eventually emits mailbox command `0xDCD10000`. The SDK `__DSPHandler` handles that command by setting the current DSP task state to RUN and invoking `init_cb(task)`. OpenSPM intentionally does not execute Nintendo DSP microcode, so there is no real DSP core to originate that mailbox/interrupt event.

## LLVM-only semantic completion

`tools/native_bridge/legacy_c_adapter_v20.c.in` adds a one-shot LLVM migration semantic at the exact wait continuation.

It does not recursively enter DolRecomp from `host_call`. Instead it reproduces the exact two architectural effects of SDK `__DSPHandler` case `0xDCD10000` for this proven first task:

1. `task->state = RUN`
2. execute the verified init callback's only side effect: set the `r13-0x7070` completion flag to 1

The bridge refuses to do anything unless all of these live guest signatures match:

- wait-loop instruction words,
- task address and task state,
- priority and ATTACHED flag,
- task DRAM DSP address `0xCD2`,
- task `init_cb` pointer,
- callback instruction words,
- completion flag still zero,
- all touched addresses resolve to bounded guest RAM.

On success the expected marker is:

```text
M14 LLVM DSP task-init semantic completion active: synthesized the first 0xDCD10000 SDK effect (task RUN + verified init_cb flag); Nintendo DSP microcode is not executed.
```

On a source/shape mismatch it fails closed and logs:

```text
M14 LLVM DSP task-init completion refused: R8PE01 wait/task/callback signature changed; leaving guest behavior untouched.
```

This bridge-only change should reuse the existing exact-stamped 14,414 LLVM object cache and require only the local module relink.

## Historical host-target misclassification discovered during analysis

`0x8028987C` is currently classified in `state/m14-host-targets.json`, `m14_host_targets.inc`, and native backend v8 as a GX/Broadway write-gather setup helper.

That classification is wrong for the verified retail R8PE01 DOL. The actual code is:

```text
8028987C  lis   r3,0xCC00
80289880  lhz   r0,0x5004(r3)
80289884  rlwinm r3,r0,17,31,31
80289888  blr
```

This is `DSPCheckMailFromDSP()`: it reads the DSP->CPU mailbox-full bit at `0xCC005004`.

The bad classification entered in commit `eca4a65e0b3d271d5812d6605b9a6ee66fed41e3` (`M14: host GX write-gather setup target`). The generated-C path is currently the known-good rollback authority and may accidentally rely on the resulting no-op while OpenSPM has no DSP microcode implementation, so this iteration intentionally does **not** remove the shared target.

After LLVM reaches the existing title/gameplay frontier, perform a controlled generated-C A/B before retiring this misclassified seam. The actual WPAR/GX gather transport is independently implemented and should not conceptually depend on `0x8028987C`.

## Next test

Run:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

Acceptance criteria:

1. source-only pipeline contracts pass,
2. the exact-stamped LLVM object cache is reused,
3. module reports `m14-dev29-llvm-dsp-task-init`,
4. the DSP task-init semantic marker appears once,
5. `0x802871E4` disappears as the sustained frontier,
6. the next frontier or visible boot progress becomes authoritative.

If the semantic completion refuses, the refusal is useful evidence: do not broaden the bypass. Re-decode whichever live task/signature differs first.
