# M14 dev127 — soft fast-core CPU Set A/B

Date: 2026-09-21
Branch: `m14-native-execution`

## Purpose

The remaining camera/world-scroll hitch is a sparse host-production tail after
the large throughput and presentation problems were already fixed.

Independent scheduling experiments now cover several different Windows axes:

- dev123: producer thread priority -> ABOVE_NORMAL;
- dev124: producer HighQoS / execution-speed throttling disabled;
- dev126: true-idle final <=2 ms spin instead of repeated Sleep(0);
- dev127: soft CPU Set preference for the fastest reported processor class.

These experiments must remain separate so a subjective improvement can be
attributed to one scheduling mechanism.

## Why CPU Sets

Windows CPU Sets are a soft scheduling-affinity mechanism rather than the old
hard process/thread affinity mask. A thread selected to multiple CPU Sets may be
scheduled on any processor in that selected set according to scheduler policy.

`SYSTEM_CPU_SET_INFORMATION.EfficiencyClass` reports the processor's intrinsic
performance/efficiency class. Microsoft defines higher numerical values as
faster but less power-efficient processors.

dev127 therefore does not hardcode logical CPU numbers or assume a particular
hybrid CPU topology.

## Implementation

New environment switch:

```text
OPENSPM_M14_MAIN_CPUSET=fast
```

The native launcher resolves these Kernel32 APIs dynamically:

- `GetSystemCpuSetInformation`;
- `GetThreadSelectedCpuSets`;
- `SetThreadSelectedCpuSets`.

After Aurora renderer creation/binding, and before `backend.run()`, the
producer/main thread:

1. snapshots any existing explicit thread CPU Set selection;
2. enumerates current system CPU Sets for this process;
3. skips parked CPU Sets;
4. skips CPU Sets reserved for another process;
5. finds the highest available `EfficiencyClass`;
6. selects all eligible CPU Set IDs in that class;
7. applies them only to the producer/main thread.

On exit, the exact prior thread CPU Set assignment is restored.

If the machine reports only one EfficiencyClass, dev127 logs a skip instead of
pretending to perform a meaningful A/B.

## Player run

```bat
M14-LLVM-FASTCORE-RUN.cmd
```

This launcher keeps all other axes neutral:

- chunk256;
- state_mem=1;
- heavy_diag=0;
- dev118 precise wait enabled;
- dev126 spin disabled;
- Immediate presentation;
- FIFO Drain;
- sticky pressed-edge delivery ON;
- normal Win32 thread priority;
- system-managed QoS.

Expected success marker on a heterogeneous machine:

```text
M14 dev127 fast-core CPU Set A/B active:
  selected=...
  eligible=...
  fastest_efficiency_class=...
  slowest_efficiency_class=...
```

A homogeneous machine instead reports:

```text
M14 dev127 fast-core CPU Set A/B skipped:
  all ... eligible CPU Sets report the same EfficiencyClass=...
```

## Interpretation

If subjective camera/world scrolling improves and dev118 `busy_due` / host
present tails drop, rare producer migration to slower processors was consuming
the residual tail margin. A production version can retain CPU Sets as a soft
preference and should not replace them with hard affinity.

If unchanged, host scheduling becomes much less likely after the independent
priority, HighQoS, final-spin and fast-core tests. Return to exact producer-tail
ownership:

- correlate `busy_due` with the active frame phase;
- add tail-only display and DrawDone ownership rather than broad profiling;
- distinguish deterministic p95/p99 GX work from Windows scheduling.

## Input note

dev127 leaves sticky pressed-edge delivery ON. The separate
`M14-LLVM-NOSTICKY-INPUT-RUN.cmd` remains the one-axis input falsification
test. Code audit shows sticky carry affects only `buttonsPressed`; held
directions remain freshly sampled each input epoch.
