# M14 LLVM v25 relF/BSS proof handoff — 2026-09-15

## v24 result: deterministic relF file placement is proven

Runtime `launch(20260915-224310).log` at repo revision `d7cbe44c6bc3` completed the v24 HEAP_MAIN tail-allocation proof exactly as designed.

Observed live values:

- HEAP_MAIN range: `[0x80703100, 0x81003100)`
- deterministic empty-tail prediction: `0x80CE7220`
- actual relF file base: `0x80CE7220`
- relF prolog offset: `0x0003E11C`
- resulting live prolog: `0x80D2533C`
- proof result: `match=yes`

The next native migration boundary was exactly `0x80D2533C`, detail 12 (no native function), because v24 deliberately kept LLVM codegen at the old observed relF base `0x80C03F40`. This is the expected proof frontier, not a new unexplained crash.

Therefore the dynamic relF-file-address problem is solved architecturally: OpenSPM can preserve the real Revolution MEM allocator and make the retail relF file image deterministic by changing only this verified allocation to `MEMAllocFromExpHeapEx(..., -0x20)`.

## Why codegen is not rebased yet

SPM does not allocate relF BSS as part of the file image. `relMain` allocates the decoded relF file, frees the file-manager entry, then separately allocates `relFile->bssSize`, clears it, calls `OSLink(relFile, bss)`, and finally invokes the relF prolog.

That means a deterministic file base alone is sufficient only if executable relF relocations do not embed addresses into the separately allocated BSS. Rebasing immediately without proving that would risk compiling correct-looking native code with wrong absolute BSS pointers.

## New BSS relocation census

`relf_bss_relocation_census.py` performs a read-only second pass over the already validated REL relocation stream. It counts only relocations where:

1. the import is relF self-import module 1;
2. the patch section is executable; and
3. the target section is BSS.

The LLVM generator now prints:

```text
[OpenSPM] relF executable->BSS relocation census: count=N patch_mask=0x... target_mask=0x...
```

and records `relf_exec_self_bss=N` in the generation summary/provenance.

Interpretation:

- `N == 0`: LLVM executable code does not bake relF BSS addresses. After v25 confirms the runtime remains stable, the relF file image can be rebased to `0x80CE7220` without teaching codegen a BSS base.
- `N > 0`: the codegen relocation model must use the deterministic live BSS base for those targets before relF text is rebased.

## v25: deterministic BSS proof

`derive_v19_llvm_seams.py` now derives the later relMain `bl __memAlloc` used for BSS from the verified R8PE01 main DOL and emits both its call and continuation as LLVM partition points.

`legacy_c_adapter_v25.c.in` includes the complete v24 proof layer, then intercepts only that derived BSS allocation. It again enters the real guest `MEMAllocFromExpHeapEx` with signed alignment `-0x20`.

Expected new runtime markers:

```text
M14 LLVM relF deterministic-tail allocation active: ...
M14 LLVM relF tail allocation result: base=0x80CE7220 ... match=yes ...
M14 LLVM relF BSS deterministic-tail allocation active: derived relMain callsite=0x... size=0x... relf_base=0x80CE7220 model_tail=0x...
M14 LLVM relF BSS tail allocation result: base=0x... size=0x... model_tail=0x... match=yes|no relf_base=0x80CE7220; codegen remains at the old relF/BSS model for this proof run.
```

The BSS `model_tail` is diagnostic. The guest SDK allocator's returned address is authoritative. A model mismatch does not justify guessing or rebasing; it means the free-list shape must be modeled from the observed run first.

v25 intentionally leaves `build_relf_codegen_dol.py` at:

- relF file base `0x80C03F40`
- relF executable address `0x80C0401C`

so the safe expected end of this proof run remains migration detail 12 at the live prolog `0x80D2533C`.

## Next decision after one v25 run

If the generator reports `relf_exec_self_bss=0`, rebase the codegen file image to `0x80CE7220` / executable `0x80CE72FC` and graduate v24's deterministic relF allocation from proof to normal LLVM behavior. BSS need not be represented in compiled executable relocations in that case.

If `relf_exec_self_bss>0`, first make the codegen BSS section addresses match the v25 deterministic BSS result, preserving REL section alignment semantics, then rebase file-backed relF sections. Do not synthesize BSS immediately after `fix_size`; SPM's runtime allocation is separate and authoritative.

After the rebase succeeds, the decisive milestone is that execution enters the actual relF prolog instead of stopping there with detail 12. Only then resume deeper LLVM correctness/performance work.
