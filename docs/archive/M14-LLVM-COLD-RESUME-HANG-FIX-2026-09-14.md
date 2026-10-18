# M14 LLVM cold-resume hang fix — 2026-09-14

## Observed failure

After Broadway PVR/SPR parity was restored, the LLVM candidate stopped crashing but opened a black, unresponsive window. The v18 live-frontier heartbeat proved the main thread was stationary with:

- `pc=0x80271E48`
- `lr=0x8026CA3C`
- `exc=0`
- `wg=0`
- `region_queries=132107` permanently unchanged
- last region `[0x80271E3C,0x80271E50)`
- `last_hostcall=0x80271E48`

`0x80271E3C` is R8PE01 `OSEnableInterrupts`; `0x80271E48` is its instruction immediately after `mtmsr` (the `extrwi` that returns the previous MSR[EE] bit), followed by `blr`.

## Root cause

This is not a renderer, GX, MMIO, or Wii-hardware stall. It is an LLVM/RecompCore cold-entry contract mismatch.

Early generated-C M14 could resume at every guest instruction address. Its privileged/control fallback completed `mtmsr` and set `PC=CIA+4`, so resuming at a post-`mtmsr` instruction was normal.

LLVM native ranges only admit selected region leaders. A valid aligned PC inside a range that is not a region leader enters DolRecomp's cold fallback path. Under RecompCore, that path invokes `CPUState::instruction_fallback` and assumes it is a complete interpreter fallback. OpenSPM deliberately supplies only a narrow migration fallback for privileged/cache/control instructions. An ordinary instruction such as the post-`mtmsr` `extrwi` is therefore not consumed, PC remains unchanged, and LLVM's internal fallback retry path loops forever inside one native range. That exactly explains why the region-query counter stopped advancing while the process remained alive.

## Immediate cache-stable recovery

`tools/native_bridge/legacy_c_adapter_v19.c.in` is LLVM-only and inherits all v18/v17/v15 behavior. At the existing host-call-before-dispatch boundary it handles only the three R8PE01 Revolution OSInterrupt post-`mtmsr` continuation PCs:

- `0x80271E34` — `OSDisableInterrupts` tail
- `0x80271E48` — `OSEnableInterrupts` tail
- `0x80271E6C` — `OSRestoreInterrupts` tail

The remaining guest semantics are exact and tiny: return the previous EE bit via `r3`, then branch through `LR`. The helper charges the two skipped guest instructions (`EXTRWI + BLR`). The preceding `mtmsr` has already executed, so the shim does not alter MSR itself.

This is intentionally done in the bridge instead of the LLVM symbol map. Adding these PCs to the map would alter the global LLVM function-range table; DolRecomp includes that range table in every job's cache key, risking a full 14,414-object rebuild. The bridge recovery relinks only the local module and preserves the generated-object cache.

Expected proof marker:

`M14 LLVM RVL cold-resume recovery active: completed post-MTMSR OSInterrupt tail at 0x80271E48 directly; generated object cache unchanged.`

## Long-term compiler fix

Once LLVM boot parity is proven, remove this migration shim by making externally resumable guest PCs legal LLVM cold native entries without requiring a full interpreter callback. A cache-friendly implementation should extend entry-point discovery independently of partition/range layout so only objects containing newly legal entry points invalidate.

Do not broaden `instruction_fallback` into a general interpreter and do not change the renderer/scheduler based on this hang.
