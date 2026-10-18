# M14 LLVM dev105 multiblock dispDraw + jump-death handoff — 2026-09-19

## Player result that changed the diagnosis

Player log `launch(20260919-173529).log` tested repo
`1622b99ace4c` / dev104.

The user clarified an important distinction that prior handoffs had conflated:

- **walking off the edge** can reach Game Over;
- **jumping into the pit** can softlock.

The latest run deliberately reproduced the jump route. It reaches HP 0 with
motion 24 and remains in SEQ_GAME(2), stage 1, next_seq 2. No next_seq=4
transition appears. Therefore prior walking-off Game Over runs are not evidence
that the jump route was fixed.

Do not force Game Over. Treat the two bottomless-death routes separately.

## dev104 result

dev104 fixed dev102's CPUState downcount interpretation and proved the exact
`dispDraw` site `0x8005B29C` can enter an in-process nested dispatcher.

Shutdown:

```text
attempts=2961408
handled=2961408
normal_returns=376344
abnormal_returns=2585064
depth_fallbacks=0
lookup_misses=0
budget_yields=0
```

This is not a lookup or budget problem. Only about 12.7% of attempts reach the
caller continuation after one `dolrecomp_call()`. Most successful generated
dispatches stop at another generated/intermediate PC, after which dev104 escapes
back to the outer run loop.

The user still reports visible lag, and heavy windows remain capable of
crossing the VI deadline. Retain dev103's host filter, but supersede dev104's
one-block strategy.

## dev103 remains valuable

The latest dev104 run still has only about 3.5M ordinary production-router calls
over the session, while pre-dev103 runs had far higher host-router cadence.
Generic `openspm_dev57_addr_taken_*` rows should remain entry-only for host
prefilter purposes. Do not revert dev103 while testing dev105.

## dev105 performance design

Exact site remains:

```text
0x8005B29C
```

CPUState helper is now:

```text
openspm_m14_dev105_cpustate_nested_dispatch
```

Instead of making one nested `dolrecomp_call()`, dev105 walks the existing
generated dispatcher across intermediate generated PCs until the callback
reaches the exact continuation.

Bounds:

```text
max generated chunks: 64
accumulated CPUState charge quantum: 32768 guest cycles
```

It fails closed to the historical outer dispatcher on:

- semantic fallback request;
- guest exception;
- generated lookup miss;
- no PC progress;
- 64-step limit;
- 32768-cycle charge limit;
- recursion-depth refusal.

No callback is skipped, fabricated, reordered, or replaced.

New teardown fields:

```text
attempts
handled
normal_returns
abnormal_returns
depth_fallbacks
lookup_misses
budget_yields
nested_steps
step_limit_yields
no_progress_yields
```

The main A/B question is whether normal_returns becomes a large fraction of
attempts without increasing correctness failures.

## dev105 jump-death trace

Public US2 symbols identify `onHpEquals0` at `0x801615E4`. The existing
dev97 motion repair already keeps the HP-zero interval host-first, but direct
native calls can still bypass host-call telemetry.

dev105 therefore adds two observation-only views.

At every `spmarioMain` where:

```text
HP == 0
motionId == 24
```

it samples, with bounded logging:

- previous motion ID;
- subMotionId;
- poseTime;
- airTimer;
- XYZ position;
- seqWork;
- next_seq.

It also counts/logs host-visible entries to exact US2 `onHpEquals0`.

This should distinguish at least three cases on the jump repro:

1. MOT_24 substate/pose continues advancing but the HP-zero callback/sequence
   request never occurs;
2. MOT_24 itself freezes in one substate;
3. `onHpEquals0` is entered but returns without requesting seq 4.

Do not infer direct-native absence merely from a zero host-visible counter; use
the MOT_24 progression log as the authoritative companion observation.

## Identities

Codegen:

```text
llvm-inline-wpar-indirect-budget-v16-dev105-multiblock-dispdraw-pittrace
```

Module:

```text
m14-dev105-multiblock-dispdraw-pittrace
```

## Next player test

Run `M14-LLVM-UPDATE-RUN.cmd` with no manual cache wipe.

**First test must be jumping into the pit. Do not substitute walking off.**
Let the jump route either transition to Game Over or remain stuck long enough to
collect the bounded dev105 trace.

Then spend 15–20 seconds in the same NPC-heavy room and close normally.

Expected startup markers:

```text
M14 LLVM dev105 CPUState dispDraw multiblock nesting active:
M14 LLVM dev105 jump-death trace active:
```

Important runtime/teardown lines:

```text
M14 LLVM dev105 jump-death truth ...
M14 LLVM dev105 host-visible onHpEquals0 ...
M14 LLVM dev105 dispDraw nested stats ...
M14 LLVM dev105 jump-death stats ...
M14 LLVM dev96 sequence/bbox truth stats ...
```

If the jump route is still stuck, use submotion/pose/air progression to choose
the exact next correctness surface. If multiblock normal_returns becomes common
but FPS remains poor, stop optimizing dispatcher mechanics and profile inside
the surviving `seq_gameMain` / callback work instead.
