# M14 LLVM first non-crashing black-window frontier — 2026-09-14

## Observed runtime

First run after restoring historical M14 Broadway SPR delegation no longer faults on HID4/BAT SPRs.

Runtime reaches native execution and then leaves a black, unresponsive Windows window. The existing v6 diagnostic thread continues to run for over 100 seconds and repeatedly samples:

- `pc=0x80271E48`
- `lr=0x8026CA3C`
- `wg_writes=0`

No M14 LLVM first-guest-exception line is emitted. This is therefore distinct from the earlier Program/Illegal SPR failures.

## Symbol / SDK interpretation

R8PE01 rev-2 symbols place:

- `OSDisableInterrupts = 0x80271E28`
- `OSRestoreInterrupts = 0x80271E50`

Matching RVL SDK `OSInterrupt.c` implementations place `OSEnableInterrupts` between those functions. Its instruction sequence is effectively:

1. `mfmsr r3`
2. `ori r4,r3,0x8000`
3. `mtmsr r4`
4. `extrwi r3,r3,1,16`
5. `blr`

Thus `0x80271E48` is the post-`mtmsr` `extrwi` continuation in `OSEnableInterrupts`, not a legitimate guest polling loop.

## Important caveat: CPUState.pc can be stale under LLVM

The legacy Windows heartbeat samples materialized `CPUState.pc`. LLVM keeps register/state values in native SSA/registers and only materializes architectural state at selected boundaries. A stationary heartbeat PC does **not** prove the host thread is currently executing that guest instruction.

The black/unresponsive window suggests the main thread is spending an unbounded interval inside one native invocation or helper, while the diagnostic thread remains alive.

## What was ruled out before adding behavior changes

### Old M14 hosted semantic addresses are already protected

`legacy_c_adapter_v7.c.in` already implements `PPC_HOST_CALL_NATIVE_REGION_QUERY` and blocks any LLVM native region containing an address from `m14_host_targets.inc`. `legacy_c_adapter_v15.c.in` delegates to that underlying query before applying v19 GX/frame seams.

Therefore the first response should **not** be to duplicate/rebuild all hosted scheduler semantics or broaden host-call interception blindly.

### LLVM already models the simple interrupt-control instructions

Pinned DolRecomp lowers `mfmsr`, `mtmsr`, `rlwinm/extrwi`, and ordinary indirect return through DolIR/native LLVM state. `mtmsr` itself is a direct MSR state write after the supervisor check; it is not a runtime helper that should block.

### Native indirect return path is not obviously broken

For `DOLIR_TERM_INDIRECT`, the LLVM native ABI returns the computed branch target through its native result / continuation machinery. A resumed mid-function entry can therefore still return to LR through the public dispatcher when the continuation is not local. Do not patch BLR handling without runtime evidence.

## New live-frontier diagnostic

`legacy_c_adapter_v18.c.in` wraps the proven v17 LLVM bridge without changing guest semantics.

On Windows it replaces only the old v6 once-per-second heartbeat and records:

- materialized PC
- LR
- CTR
- MSR
- exception and program-exception state
- WPAR write count
- total `PPC_HOST_CALL_NATIVE_REGION_QUERY` count
- most recent queried native `[start,end)` range
- most recent ordinary host-call address
- whether the region-query count was stationary since the previous heartbeat

Expected line:

`M14 LLVM frontier heartbeat #... pc=... lr=... ctr=... msr=... exc=... program=... wg=... region_queries=... region=[0x...,0x...) last_hostcall=0x... query_stationary=...`

Interpretation:

- `query_stationary=1` for successive seconds: the main thread is likely trapped **inside** the last queried native range/helper and is not returning to a new native-region entry. Inspect that exact range for an unguarded loop, helper wait, or native ABI escape problem.
- `query_stationary=0` with a rapidly rising query count while PC/frontier stays similar: execution is returning/re-entering. Then investigate guest/host semantic state, repeated side exits, or a service/event that fails to advance the guest rather than an unguarded native loop.
- A changing `last_hostcall` that matches a hosted M14 target gives an immediate semantic-boundary lead.

## Commits

- `a365d743caf3949a9099e38c2aecbaeeb989ac24` — restore historical M14 Broadway SPR delegation in staged LLVM runtime.
- `f42ef7da5f1211eb5827beb0ed6c89874d0cbe82` — early-boot parity handoff.
- `f53ea4ba5b440ffe19eb64942a79317d13bc9b94` — add LLVM live native-region frontier bridge v18.
- `6f6cabc5d5267a844e94ac600af9b77dda538ddf` / `8ce90e8772137c9898b40846955c2acc2a25e6ff` — activate v18 for LLVM while preserving the existing source contract marker.

## Next run

Run exactly:

`M14-LLVM-UPDATE-RUN.cmd`

No cache deletion, LLVM redownload, or normal C rebuild should be required beyond what the runner itself decides. The 14k native LLVM object cache should remain reusable; v18 is a bridge/module relink change.

If it hangs again, only a few consecutive `M14 LLVM frontier heartbeat` lines are needed to choose the next fix.
