# M14 dev121 — quiet normal-play slice-profiler retirement

Date: 2026-09-20  
Branch: `m14-native-execution`

## Trigger

dev120 same-thread FIFO Inline parsing did **not** remove the remaining small
stutter. The player still reports the same occasional hitch.

That falsifies the hypothesis that concentrating Aurora parser work at DrawDone
is the dominant residual pacing defect. Return to the proven Drain parser
baseline for ordinary play.

## New finding

The dev119 log's shutdown telemetry reports:

```text
M14 LLVM dev68 slice stats calls=10027806 wall_us=64711728 ...
```

That is about 10.0 million native runtime slices over 64.7 seconds, or roughly
155,000 slices per host second.

In the active bridge, **every one** of those slices was wrapped with:

1. a host monotonic-clock read before the native run;
2. a host monotonic-clock read after the native run;
3. elapsed-time arithmetic;
4. histogram accounting;
5. start-PC/end-PC pair accounting;
6. hang counters;
7. once-per-second rescue/callback cadence bookkeeping.

This is historical bring-up instrumentation. It does not participate in Wii
execution semantics.

At ~155k slices/sec, even a few hundred nanoseconds per host clock/callback plus
bookkeeping can consume the final millisecond-scale headroom needed to make a
~56-58 Hz producer consistently hit 60 Hz.

## dev121 change

When `OPENSPM_M14_HEAVY_DIAGNOSTICS=0` (normal play),
`bridge_run_dev67()` now directly returns the underlying runtime call:

```c
return g_m14_dev67_underlying_run(opaque, soft_cycle_budget, out_exit);
```

The old dev67 slice profiler remains byte-for-byte available behind
`heavy_diag=1`.

Normal play also stops installing the retired observation-only wrappers for:

- dev95 sequence tracking;
- dev96 HP/player/bbox tracking;
- dev106 jump-death tracing;
- dev100 scene-owner sampler;
- dev111/dev112/dev115 census/motion diagnostics.

The actual correctness repairs these probes helped discover remain active.
Only observation code is retired.

## Telemetry retained

The lightweight four-phase frame timing remains active.

dev118 VI pacing reporting also remains active, but its once-per-second report
check now runs from the ~60 Hz frame boundary instead of the ~155 kHz native
slice wrapper.

Therefore the next player log can still answer whether frames are ready before
the fixed VI deadline without restoring the old profiling tax.

## Candidate

Run:

```bat
M14-LLVM-QUIET-RUN.cmd
```

Configuration:

- chunk = 256
- state_mem = 1
- heavy_diag = 0
- precise VI wait = 1
- presentation = Immediate
- FIFO mode = Drain

This deliberately combines only player-proven positive settings and the new
diagnostic retirement.

Expected marker:

```text
M14 LLVM dev121 quiet normal-play path active
```

At shutdown, normal mode should report that per-slice timing was intentionally
not collected rather than showing millions of timed slice calls.

## Interpretation

If the small stutter disappears or materially drops, the final pacing defect was
largely self-inflicted diagnostic overhead.

If cadence is unchanged, the next step is a tail-only frame attribution probe:
log only anomalous frames and distinguish display, main, DrawDone and missing
GXCopyDisp/present-production events. Do not restore broad per-slice profiling.

## Retired hypothesis

dev120 Inline remains available for forensic A/B, but it is not the preferred
normal-play path because the player observed no meaningful reduction in the
remaining stutter.
