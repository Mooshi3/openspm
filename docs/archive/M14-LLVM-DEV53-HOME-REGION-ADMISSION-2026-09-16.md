# M14 LLVM dev53 — HOME region admission repair

Date: 2026-09-16
Branch: `m14-native-execution`

## Why dev51/dev52 stalled at boot

The dev51 player log (`282deeb55056`) and dev52 player log (`ea176defb631`) both reached the first renderer/input safepoint and delivered VI retraces near 60 Hz, but then remained at:

- PC `0x801A7AAC` (`spmarioMain`)
- seq/stage `0/0`
- LR `0x8001D954`
- CTR `0x8028F344`

The cadence counter advanced absurdly fast: roughly 15 million `spmarioMain` observations/s in dev51 and 8–9 million/s in dev52. A known-progressing dev47 control observes only about 20–21 real `spmarioMain` entries/s while advancing through seq_logo stages and later seq 3. Therefore the dev51/dev52 count was a dispatcher/admission loop, not real game execution.

## Root cause

The proven HOME seam from `legacy_c_adapter_v21.c.in` has this player-local shape:

- `spmarioMain = 0x801A7AAC`
- HOME linked callsite = `0x801A7ABC`
- HOME continuation = `0x801A7AC0`
- `homebuttonMain = 0x8023F994`

The old working implementation blocks the **callee** region containing `homebuttonMain`. That linked-native side exit materializes the caller at `0x801A7ABC`, where OpenSPM completes the PC policy (`HOME inactive`, `r3=0`) and resumes at `0x801A7AC0`.

dev50 accidentally broadened its lean region classifier to block a native range whenever it contained either the HOME callee **or the HOME caller instruction**. The first `spmarioMain` native range contains `0x801A7ABC`, so dev50 rejected the whole region before the body could execute. The dispatcher retried `0x801A7AAC` forever.

dev52 did not fix this because its one-shot phase permit belongs to the historical v23 classifier, while the active region-admission decision is owned by dev50.

## dev53 implementation

`tools/native_bridge/legacy_c_adapter_v41.c.in` now contains dev53 and includes dev51 directly; the dev52 runtime wrapper is retired while its source-contract marker remains for archaeology/CMake compatibility.

The dev53 top classifier makes two narrow corrections before delegating to dev50:

1. When the queried region is rooted at `spmarioMain` and contains the verified HOME caller `0x801A7ABC` (but not the callee `0x8023F994`), admit the region. The real callee remains blocked by dev50, preserving the proven HOME side-exit behavior.
2. When `CPUState.pc` is stale during a region query, `external_addr`/`external_value` are authoritative. Only in that stale-PC case, dev53 binary-searches the direct-hostcall seam table at `external_addr`; an exact callsite start is blocked so the ordinary semantic hostcall path can execute. Normal queries do not pay this extra lookup.

No Nintendo scheduler state, sequence state, input state, or GX state is fabricated.

Active module string:

`m14-dev53-llvm-home-region-admission`

Expected startup marker:

`M14 LLVM dev53 corrected production region admission active`

Expected first-frame proof:

`M14 LLVM dev53 HOME admission correction active: allowing spmarioMain native range ...`

## Success criteria for next player run

A successful repair should show all of the following:

- `spmarioMain` cadence collapses from millions/s to a plausible frame cadence (historical LLVM control was ~20–21/s before performance cleanup);
- `M14 LLVM native system-UI seam active` appears;
- seq/stage advances beyond `0/0`;
- GX DrawDone/PE-finish completions continue;
- input frame latch remains active;
- no dev51 GX shadow fail-closed event.

Once boot progression is restored, compare real `spmarioMain` updates/s against the old dev47 ~20–21/s control. dev50's main purpose is to remove millions of expensive diagnostic region-query operations; do not judge its performance while the dispatcher loop is active.

## If dev53 still loops at 0x801A7AAC

Do not add another phase permit. Instrument the first 8 dev53 region queries rooted at `0x801A7AAC` with `[external_addr,external_value)`, `cpu->pc`, and the dev50 blocker category. The next likely issue would then be another over-broad region blocker, not frame timing, input, GX, or VI.

## Constraints preserved

- no emulator fallback;
- player supplies proprietary WBFS/extracted content;
- dev47 real PE-finish completion remains authoritative;
- dev48/49 PC-native display-list transport remains active;
- dev51 player-derived GXData and frame-latched input contracts remain active;
- blanket `bl+4` partitioning remains disabled/audit-only.
