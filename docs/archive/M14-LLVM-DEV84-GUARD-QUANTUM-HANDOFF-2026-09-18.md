# M14 LLVM dev84 — 32768-cycle legacy x86 yield quantum

Date: 2026-09-18

## Why dev84 supersedes the untested 4096-cycle dev83 candidate

dev83 identified a near-exact quantitative match between the old 256-cycle LLVM
budget guard and the measured public-dispatch storm:

- dev79 measured about 2.900M ordinary public callbacks/s;
- 2.900M * 256 = about 742M guest cycles/s;
- Broadway runs at about 729 MHz.

That makes voluntary LLVM budget exits the strongest current explanation for the
remaining heavy-scene simulation loss.

The 4096-cycle dev83 candidate would reduce that pressure by 16x, but it would
still manufacture about 181k public dispatcher crossings/s at Broadway-scale
throughput. Each crossing materializes native state, performs an x86 SJLJ cold
escape, re-enters the host-first public dispatcher, and then re-enters native
code. That is still far more scheduling churn than OpenSPM needs.

OpenSPM already owns an outer 200,000-cycle bridge budget and performs the
scheduler/VI safepoint at that boundary. A 32,768-cycle inner quantum leaves
roughly six voluntary inner yields per outer run while reducing the predicted
budget-exit rate by 128x relative to upstream 256:

```text
256 cycles    -> ~2.900M exits/s   measured dev79 scale
4096 cycles   -> ~181k exits/s     dev83 estimate
32768 cycles  -> ~22.7k exits/s    dev84 estimate
```

32,768 Broadway cycles are only about 45 microseconds of guest time. This is
still tiny relative to a 16.7 ms video frame and substantially smaller than the
existing 200k-cycle outer safepoint window.

## Change

Only the legacy Windows/x86 SJLJ native-ABI guard changes:

```text
native_abi_ && cold_escapes_ && intrinsic_escapes_ ? 32768 : 256
```

Both scalar/direct and dynamic/indirect budget-guard overloads use the same
threshold. All other DolRecomp modes retain 256.

Unchanged:

- host-first public dispatch;
- PE/DSP/HOME/relF/GX/post-MTMSR semantic routing;
- exceptions and fallback exits;
- native-region admission policy;
- outer 200k-cycle bridge budget;
- VI and scheduler safepoints;
- dev77 wedding restart partition;
- dev81 native frame-phase profiler.

This does not skip guest work or synthesize state. It only reduces how often
native execution voluntarily returns to the same dispatcher before the outer
runtime budget is exhausted.

## Active provenance

`llvm-inline-wpar-indirect-budget-v7-dev84-guard32768`

The provenance change intentionally invalidates affected LLVM objects.

## Test

Run `M14-LLVM-UPDATE-RUN.cmd` with no manual cache wipe.

First verify boot past the retired dev80 freeze frontier at `0x8026CA60`, then
pass one wedding and spend at least 30 seconds in a known heavy NPC area.

Expected runtime marker:

```text
M14 LLVM dev84 guard-quantum A/B active: ... 32768-cycle ...
M14 LLVM dev84 cadence host_ms=... ordinary_rate_per_s=...
```

Primary success criteria:

1. ordinary callback rate collapses from the old ~2.9M/s toward tens of
   thousands/s;
2. heavy `spmarioMain` cadence moves materially toward ~60/s while retraces
   remain ~60/s;
3. dev81 phase timing identifies any residual frame cost.

If correctness regresses, revert only this quantum to 4096/256; dev82's
host-first dispatcher restoration is independent.
