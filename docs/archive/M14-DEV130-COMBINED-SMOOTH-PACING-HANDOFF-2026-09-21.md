# M14 dev130 — combined smooth-pacing candidate

Date: 2026-09-21
Branch: `m14-native-execution`

## Purpose

The six independent player A/Bs did not produce a single perfect result, but
two host-side changes moved in the right direction:

- dev127 fast-core CPU Set preference felt best subjectively, while still
  retaining some jitter;
- dev126 final-idle spin reduced the amount of Windows Sleep(0) scheduler
  handoff in the last <=2 ms before VI and did not regress playability.

The no-sticky run was clearly worse, so sticky pressed-edge retention remains
ON.

dev129 is now the architectural baseline in every LLVM build: empty scheduler
idle exits immediately to the host VI wait path, while runnable Nintendo work
continues through native SelectThread.

dev130 combines only the two helpful scheduling settings on top of dev129.

## Launcher

```bat
M14-LLVM-SMOOTH-RUN.cmd
```

Configuration:

- chunk = 256
- state_mem = 1
- heavy_diag = 0
- fixed-grid precise VI wait = ON
- dev126 final <=2 ms true-idle spin = ON
- presentation = Immediate
- FIFO = Drain
- sticky pressed-edge retention = ON
- Win32 producer priority = normal
- producer HighQoS = OFF / system managed
- producer CPU Set preference = highest available EfficiencyClass

This deliberately does **not** combine ABOVE_NORMAL priority or HighQoS because
their independent player runs were not clearly better.

## Expected markers

From dev129:

```text
M14 LLVM dev129 scheduler-idle dynamic dispatch active:
```

From dev126:

```text
M14 dev126 final-idle-spin A/B active:
```

From dev127:

```text
M14 dev127 fast-core CPU Set A/B active:
```

dev128 remains observational in the same run.

## Comparison

First run the clean dev129 baseline:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
```

Then run:

```bat
M14-LLVM-SMOOTH-RUN.cmd
```

Use the same Yold walking path.

Compare:

- subjective camera/world-scroll cadence;
- dev128 busy-due owner distribution;
- dev118 busy_due/from_idle;
- >25 ms host-present tails;
- dev129 empty_sideexits/runnable_native/unknown_native;
- spinwait `yield0` collapse.

## Decision

If dev129 alone fixes most of the remaining jitter, keep host scheduling simple
and do not make fast-core/spin policy mandatory.

If dev129 helps but dev130 is visibly better again, retain fast-core as a soft
Windows preference and replace the 2 ms brute-force spin with a production
high-resolution waitable-timer + shorter final spin.

If dev129 eliminates scheduler-idle-owned misses but residual jitter remains,
use dev128's new remaining gameplay PCs as the next deterministic optimization
targets instead of continuing broad Windows scheduling experiments.
