# M14 dev124 — producer HighQoS A/B

Date: 2026-09-21
Branch: `m14-native-execution`

## Why this exists

dev122 plus a direct report-window correlation narrowed the residual camera-visible
hitch substantially:

- ordinary/median Yold production is already fast enough;
- the remaining >25 ms host-present tails track dev118 `busy_due` counts very
  closely in steady gameplay (Pearson r ~= 0.98);
- camera-5 and Mario continue moving on the correlated tails;
- dev123 therefore tests host scheduling of the producer thread rather than
  reopening broad average-throughput work.

dev123 `M14-LLVM-PRIORITY-RUN.cmd` changes Win32 thread priority to
ABOVE_NORMAL. dev124 tests a different Windows scheduling dimension: explicit
foreground/performance QoS.

## Windows contract

Microsoft documents `ThreadPowerThrottling` with
`THREAD_POWER_THROTTLING_EXECUTION_SPEED` controlled and cleared as the
HighQoS form: execution-speed throttling is explicitly disabled. This prevents
the thread from being intentionally treated as EcoQoS/LowQoS work.

The implementation resolves `SetThreadInformation` from Kernel32 at runtime
instead of depending on newer SDK preprocessor target guards. The ABI constants
used are the documented Windows values:

- ThreadPowerThrottling information class = 3;
- power-throttling structure version = 1;
- execution-speed control bit = 0x1.

If the API is unavailable or fails, the experiment logs a warning and leaves
normal scheduling unchanged.

On exit, the thread's power-throttling control mask is reset to zero so Windows
returns to system-managed behavior.

## Player A/B

Run on a separate launch:

```bat
M14-LLVM-HIGHQOS-RUN.cmd
```

It retains:

- chunk256;
- state_mem=1;
- heavy_diag=0;
- precise VI wait;
- Immediate presentation;
- FIFO Drain;
- sticky pressed-edge delivery ON;
- normal Win32 thread priority.

Only this is added:

```text
OPENSPM_M14_MAIN_QOS=high
```

Expected marker:

```text
M14 dev124 producer HighQoS A/B active:
```

## Interpretation

If the camera/world-scroll tail materially improves and dev118 `busy_due`
falls, retain HighQoS as the less invasive host scheduling fix and compare it
against dev123 ABOVE_NORMAL before combining them.

If unchanged, do not blindly stack priority + QoS. The next scheduling test
should enumerate Windows CPU Sets and use their reported EfficiencyClass to test
a soft fast-core preference on heterogeneous CPUs. Avoid hardcoded processor
numbers or a whole-process affinity mask.

If host scheduling experiments are all negative, return to the rare producer
tail itself: add tail-only DrawDone/display attribution keyed to `busy_due`,
not another broad profiler.
