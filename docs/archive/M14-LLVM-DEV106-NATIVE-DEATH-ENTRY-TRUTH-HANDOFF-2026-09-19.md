# M14 LLVM dev106 native death-entry truth handoff — 2026-09-19

## Why dev106 exists

Player log `launch(20260919-173529).log` tested dev104 at repo
`1622b99ace4c`.

The user clarified that successful Game Overs were observed when walking off an
edge, while the reproducible softlock occurs when jumping into the pit. The
failed jump path reaches the same sampled terminal player state seen in prior
successful deaths:

```text
hp=0
motion=24
y=-150
mario_flags=0x10000601
disp_flags=0x00000001
```

but `next_seq` remains 2 instead of becoming 4.

Because the once-per-spmarioMain sample is already after the decisive callback
work, the next useful question is not "is Mario dead?" but exactly which native
death functions ran between the prior live frame and the first HP-zero frame.

Do not force Game Over or write sequence state.

## dev105 retained performance experiment

dev104 proved the exact CPUState dispDraw site `0x8005B29C` is reachable but a
single nested `dolrecomp_call()` often stops at another generated chunk:

```text
attempts=2961408
handled=2961408
normal_returns=376344
abnormal_returns=2585064
```

dev105 therefore remains the performance A/B: walk the existing exact generated
dispatcher for up to 64 chunks / 32768 accumulated guest cycles until the exact
caller continuation, failing closed on fallback, exception, miss, no progress or
limits. dev103's proven entry-only host filter remains active.

## dev106 correctness instrumentation

Public US2 symbols identify:

```text
onHpEquals0 = 0x801615E4
seqSetSeq   = 0x8017B834
```

Host-call telemetry is insufficient because direct native-to-native calls can
bypass `CPUState::host_call`.

The LLVM block-entry emitter now makes a host-only counter call at exactly those
two guest PCs:

```text
openspm_m14_note_death_entry_native(pc)
```

It receives only the compile-time guest PC. It does not receive or materialize
CPUState, and it does not modify guest registers, memory, HP, motion or sequence
state.

The existing HP=0/MOT_24 trace now records:

```text
native_onHp0=<total>(+<delta since previous non-HP0 frame>)
native_seqSetSeq=<total>(+<delta since previous non-HP0 frame>)
host_onHp0=<total>
```

The first HP-zero line therefore distinguishes:

1. `native_onHp0 +0`: jump path never reached `onHpEquals0`;
2. `native_onHp0 +N`, `native_seqSetSeq +0`: HP-zero handler ran but never
   entered `seqSetSeq`;
3. both deltas nonzero but `next_seq=2`: inspect the exact seqSetSeq arguments /
   caller path next;
4. both deltas nonzero and `next_seq=4`: Game Over request occurred and any
   later failure is downstream.

The existing host-visible `onHpEquals0` counter remains only as a cross-check.

## Identities

Codegen:

```text
llvm-inline-wpar-indirect-budget-v17-dev106-native-death-entry-truth
```

Module:

```text
m14-dev106-native-death-entry-truth
```

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No manual cache wipe.

For correctness, test **both routes in the same dev106 build** if practical:

1. walk off the edge once;
2. jump into the pit once.

The within-build comparison removes the prior dev103/dev104 confound.

Important lines:

```text
M14 LLVM dev106 jump-death truth ...
M14 LLVM dev106 jump-death stats ...
M14 LLVM dev95 seqSetSeq request=...
M14 LLVM dev96 sequence truth ...
M14 LLVM dev105 dispDraw nested stats ...
```

For performance, also spend 15–20 seconds in the same NPC-heavy room. If dev105
multiblock makes normal_returns common but the scene remains ~30 FPS, stop
working on dispatcher mechanics and profile inside the surviving real disp/main
work. If multiblock remains mostly bounded exits or causes any correctness
regression, retire it and keep dev103 as the dispatch baseline.
