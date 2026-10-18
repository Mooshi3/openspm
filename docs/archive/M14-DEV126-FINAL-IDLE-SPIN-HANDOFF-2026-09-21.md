# M14 dev126 — final idle-spin pacing A/B

Date: 2026-09-21  
Branch: `m14-native-execution`

## Why this exists

The residual camera/world-scroll hitch is a sparse tail problem, not the old
broad 30-fps throughput collapse.

The project already proved:

- dev114: canonical Yold median first-retrace critical path ~13.514 ms;
- dev118: most VI deliveries reach true Nintendo scheduler idle before the fixed
  deadline;
- dev119: Immediate presentation removed roughly three quarters of the visible
  judder;
- dev122: camera-5 position/target/view translation continues moving through
  most visible ~30–32 ms host-present gaps;
- dev122 steady-gameplay tails correlate closely with the minority of dev118
  `busy_due` deliveries.

dev125 removed a concrete source of overhead from dev118's fine-wait path by
caching the invariant QueryPerformanceFrequency value. Inspection of the same
path found another potentially important host-scheduling behavior:

```text
OPENSPM_M14_PRECISE_IDLE_WAIT=1
```

uses `Sleep(1)` while more than 2 ms early, then repeatedly calls `Sleep(0)`
inside the final <=2 ms before the fixed VI deadline.

Steady Yold logs commonly report roughly **150k–190k Sleep(0) yields per host
second**. That is thousands of scheduler yields per displayed frame. The
average delivery lateness is low, so this is not a broad VI-timing failure, but
rare host descheduling during that yield storm is a credible source of the
remaining tail.

## dev126 A/B

Run:

```bat
M14-LLVM-SPINWAIT-RUN.cmd
```

The configuration remains:

- chunk256;
- state_mem=1;
- heavy_diag=0;
- fixed 60 Hz VI grid;
- dev118 precise wait enabled;
- Immediate presentation;
- FIFO Drain;
- sticky pressed-edge delivery ON;
- normal Win32 producer priority;
- system-managed producer QoS.

Only the final true-idle waiting policy changes.

When:

1. Nintendo's scheduler is at the proven no-runnable-thread idle PC;
2. the live run queue is empty;
3. the next fixed VI deadline is <=2 ms away;

dev126 uses `YieldProcessor()` in a local loop until the same existing
deadline instead of returning through thousands of `Sleep(0)` scheduler
yields.

No guest work is run during the spin. No VI deadline moves. No guest timebase,
FIFO ordering, renderer ordering, presentation policy or input semantics change.

Expected marker:

```text
M14 dev126 final-idle-spin A/B active:
```

dev118 telemetry now ends with:

```text
precise=1 spin2ms=1
```

In this run, `yield0` should collapse toward zero while `from_idle`,
`busy_due`, delivery lateness and subjective camera motion remain directly
comparable.

## Interpretation

If subjective camera scrolling improves and `busy_due` / >25 ms present tails
drop, repeated `Sleep(0)` scheduler handoffs were consuming the final tail
margin. A production implementation should then replace the 2 ms brute-force
spin with a lower-CPU hybrid such as a high-resolution waitable timer plus a
short final spin.

If unchanged, keep dev125's QPC cache but reject the spin as a player solution.
Continue with the independent scheduling A/Bs:

```bat
M14-LLVM-PRIORITY-RUN.cmd
M14-LLVM-HIGHQOS-RUN.cmd
```

and then a soft fast-core CPU Set experiment.

## Sticky-input audit

The player's sticky-input suspicion was audited at the active dev29/dev123
transport.

The historical sticky behavior does **not** replay held direction bits:

- `v9_keyboard_down()` samples physical keys once per native input/retrace
  epoch;
- `g_v9_input_down` is replaced with that fresh physical mask;
- KPAD `buttonsHeld` is written from `g_v9_input_down` (plus the independent
  SDK repeat marker);
- sticky dev29 behavior carries only unobserved **buttonsPressed edge bits**
  across retraces until the real guest pressed getter consumes them.

Therefore holding Left/Right cannot be cancelled every other frame by the sticky
edge mechanism itself.

The repository still includes the clean falsification launcher:

```bat
M14-LLVM-NOSTICKY-INPUT-RUN.cmd
```

It disables only pressed-edge carry. Held-direction sampling remains unchanged.
If it unexpectedly improves camera motion, investigate higher-level SPM input
consumption rather than the low-level held mask.
