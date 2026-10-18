# M14 LLVM relF live-layout rebase — 2026-09-15

## Runtime proof

The v24/v25 controlled proof runs established deterministic HEAP_MAIN placement using the real guest `MEMAllocFromExpHeapEx` tail path (`align=-0x20`).

Latest proof (`launch(20260915-233500).log`, repo revision `4e4e16683a7f`):

- relF file allocation: expected/model `0x80CE7220`, actual `0x80CE7220`, `match=yes`.
- relF prolog: `0x80D2533C` (`relF + 0x3E11C`).
- relF BSS allocation call: derived `0x8023C9B0`.
- relF BSS size: `0x00000EBC`.
- relF BSS model: `0x80CE6340`, actual `0x80CE6340`, `match=yes`.
- next boundary: detail 12 at `0x80D2533C`, exactly because the proof build still compiled relF at the historical `0x80C03F40` address.

This closes the dynamic-address question for the current verified R8PE01 revision. The window teardown in that run is the intentional no-native-function frontier, not a new renderer/access-violation crash.

## Codegen change

`build_relf_codegen_dol.py` now models the proven live OSLink layout directly:

- `REL_BASE = 0x80CE7220`
- `REL_EXEC_ADDRESS = 0x80CE72FC`
- `REL_BSS_BASE = 0x80CE6340`
- `REL_BSS_SIZE = 0x00000EBC`
- `REL_PROLOG_ADDRESS = 0x80D2533C`

The important correctness change is BSS modelling. relF file storage and BSS are separate game allocations. OSLink maps file-backed sections relative to the relF pointer and maps the zero-offset BSS section to the separately supplied BSS pointer. The compiler image therefore must not synthesize BSS at `align(rel_base + fix_size, bss_alignment)`.

The builder now fails closed unless the decoded player relF still has exactly one BSS section and the header/section BSS size remains `0xEBC`.

## Source contracts

`test_llvm_native_pipeline.py` now expects the proven live file/BSS/prolog addresses and tests BSS-shape drift in addition to executable-section drift. Generated-C remains on v13. LLVM remains on the v25 deterministic allocator layer for the first rebased execution run.

## Next run

Run `M14-LLVM-UPDATE-RUN.cmd`.

Expected build markers include a relF codegen line with:

`rel=0x80CE7220 exec=0x80CE72FC+0x63D88 bss=0x80CE6340+0xEBC prolog=0x80D2533C`

DolRecomp should invalidate/regenerate the relF-address-dependent native chunks under its exact-stamp resume policy; generated and validated manifest counts must still agree.

At runtime, the old detail-12 boundary at `0x80D2533C` should disappear. If a new frontier occurs, treat that new PC/detail as the next real LLVM migration defect. If execution reaches title/file-select/gameplay, begin performance A/B against the generated-C path before doing renderer cleanup.

Note: v25 log strings still describe themselves as a proof layer; that wording is historical. For this first rebased run the allocator behavior is intentionally retained because it is what guarantees the compiled addresses.
