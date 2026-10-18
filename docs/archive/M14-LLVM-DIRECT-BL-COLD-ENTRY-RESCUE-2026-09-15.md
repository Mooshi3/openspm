# M14 LLVM direct-BL cold-entry rescue — 2026-09-15

## Runtime evidence

Tested head before this fix: `55d4f96a2dbe553a926f49e2dee91885ffc03f4a`.

The v21 HOME/system-UI callsite seam and the v23 one-shot phase permit both worked. The live state advanced from `seq=0 stage=0` to `seq=0 stage=1`, proving `seqMain()` finally executed. The sustained frontier then became:

- PC/LR/last_hostcall: `0x801A7ACC`
- region query: `[0x801A7AAC,0x801A7CAC)`
- `query_stationary=0`
- no guest exception/program exception
- 60 Hz retraces and KPAD remained active

Exact player-owned R8PE01 bytes from the verified checkpoint DOL:

```text
801A7AC8: 4BFD3C65  bl 0x8017B72C   ; seqMain
801A7ACC: 48095225  bl 0x8023CCF0   ; nandMain
```

`0x801A7ACC` is therefore both the linked-call continuation of `seqMain()` and the next direct BL instruction. The previous LLVM chunk is exactly `[0x801A7AAC,0x801A7CAC)`, so the continuation lands one instruction into the next 128-instruction chunk. The private DolRecomp collector is already intended to add every BL/BCL+4 continuation as a legal entry, but this cross-chunk case still reaches OpenSPM's ordinary fallback/host-call path before the BL executes.

The callee bytes at `0x8023CCF0` match SPM's small `nandMain()` task dispatcher (load `wp->task`, switch, branch to task-specific main functions). The game is not yet proven stuck inside NAND; the BL itself is not executing.

## Fix

`legacy_c_adapter_v22.c.in` adds a bounded architectural rescue after the full existing host-call chain has declined an ordinary address:

1. Read the guest instruction at the materialized address.
2. Require PowerPC B-form primary opcode 18 and LK=1.
3. Decode AA/LI exactly and require a valid guest target.
4. Execute only the architectural BL effect: `LR = PC + 4`, `PC = target`.
5. Return handled so normal native dispatch resumes at the target.

Native-region queries are delegated untouched because their boolean means allow/block, not handled/unhandled.

This is not HLE of `nandMain()` and does not special-case `0x801A7ACC`; it is a correctness safety net for any missing direct-BL cold entry. Existing v21 HOME policy fires before this layer, and all older DSP/GX/input/NAND/platform behavior remains underneath.

Marker:

```text
M14 LLVM direct-BL cold-entry rescue active: executed unhandled linked branch at 0x801A7ACC -> 0x8023CCF0 (LR=PC+4); compiler continuation-entry audit remains open.
```

## Next test

Run `M14-LLVM-UPDATE-RUN.cmd`.

Expected:

- HOME marker still fires.
- KPAD still reports controller 0 connected.
- direct-BL rescue marker should fire first at `0x801A7ACC -> 0x8023CCF0`.
- `0x801A7ACC` should no longer be the sustained frontier.
- If the next frontier lies inside `nandMain()` or an async NAND task, classify that separately; do not treat it as another continuation-entry miss unless the materialized PC itself is again a direct linked branch.

No LLVM object rebuild should be required; v22 is bridge/CMake only. The compiler's cross-chunk continuation-entry behavior remains an audit item to fix upstream/privately once boot parity is restored.
