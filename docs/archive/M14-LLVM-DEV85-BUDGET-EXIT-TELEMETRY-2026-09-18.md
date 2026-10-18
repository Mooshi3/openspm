# M14 LLVM dev85 — exact voluntary budget-exit telemetry

Date: 2026-09-18

dev84 keeps the performance candidate at a 32,768-cycle legacy x86 SJLJ yield
quantum. dev85 adds one bounded observation so the next player run can prove or
disprove the current causal model instead of inferring it from public callbacks.

## What is counted

Both LLVM budget-guard overloads increment one player-local uint64 counter only
when the guard actually takes its voluntary exit edge, and only for:

`native_abi_ && cold_escapes_ && intrinsic_escapes_`.

The generated code performs a non-atomic load/add/store through
`openspm_m14_llvm_budget_exit_counter_ptr`. OpenSPM runs the emulated CPU on one
thread, so no atomic synchronization is required. The counter changes no guest
state and is not consulted by control flow.

The existing once-per-host-second cadence logger now reports:

```text
M14 LLVM dev85 cadence ... ordinary_rate_per_s=...
    budget_exits=... budget_exit_rate_per_s=...
```

This lets us distinguish three cases immediately:

- budget-exit rate ~= ordinary callback rate: voluntary guard churn is the
  dominant public-dispatch source;
- budget exits collapse but ordinary callbacks stay high: another native escape
  path is dominant;
- both collapse but simulation remains ~30 Hz: dispatch churn was real but not
  the missing frame budget; use dev81 phase timings next.

## Active performance semantics

The guard itself remains dev84:

```text
legacy Windows/x86 SJLJ: 32768 cycles
all other modes:         256 cycles
outer OpenSPM budget:    200000 cycles (unchanged)
```

Host-first dispatch and all semantic platform/GX/DSP/HOME/relF/post-MTMSR
routing remain unchanged.

## Provenance

`llvm-inline-wpar-indirect-budget-v8-dev85-budgetcounter`

No manual cache wipe is required.

## Next test

Run `M14-LLVM-UPDATE-RUN.cmd`, pass one wedding, then spend >=30 seconds in a
known heavy NPC area. Send the log. The two most important lines are the dev85
cadence line and dev81 `M14 main-loop phases` line.
