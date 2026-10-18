# M14 LLVM v24 Wii manifest link fix — 2026-09-15

## Symptom

The v24 deterministic-relF proof never reached the runtime window. DolRecomp generation completed, but the local-module link failed:

```text
... R8PE01_generated\chunks (14416 files)
M14 OpenSPM LLVM generation ready: objects=14414 ...
-- OpenSPM M14 validated LLVM manifest: 14414 native objects ...
chunk_8270_text1_8023C860.o : error LNK2001: unresolved external symbol func_8023C97C_budget
openspm_local_native.dll : fatal error LNK1120: 1 unresolved externals
```

The existing `launch.log` therefore remained the previous v23 run; there was no new process to replace it.

## Root cause

This was not a missing DolRecomp code-generation range and is not a reason to remove the new relF continuation partition at `0x8023C97C`.

Pinned DolRecomp uses different output names depending on title classification:

- old titleless/GC-style output: `generated/generated.h` + `generated.c`
- Wii/title-aware R8PE01 output: `R8PE01_generated/R8PE01.h` + `R8PE01.c`

OpenSPM previously migrated the old titleless `generated/` directory to `R8PE01_generated/` once so that exact-stamped LLVM objects could be reused. That migration also carried the old generic `generated.h/generated.c` files into the new directory.

A later correct Wii/title-aware generation then wrote fresh `R8PE01.h/R8PE01.c` beside those stale generic files. `locate_generated()` still looked specifically for `generated.h`, and `validate_llvm_generated.py` then parsed the adjacent stale `generated.c`.

The terminal counts prove the mixed generation:

- fresh DolRecomp range/object set: **14416**
- stale OpenSPM manifest selected for linking: **14414**

The current callsite object was regenerated and correctly referenced the new range beginning at `0x8023C97C`, but the stale 14414-object manifest omitted the object which defines `func_8023C97C_budget`. The linker failure is the expected consequence of combining those two generations.

## Fix

`tools/native_bridge/generate_local_llvm.py` now treats the Wii/title-aware directory as authoritative whenever it exists:

1. Require `<game>.h` and `<game>.c`; fail closed if the Wii output is incomplete.
2. Copy the fresh `<game>.h` to the generic `generated.h` alias OpenSPM consumes.
3. Copy the fresh `<game>.c` to `generated.c`.
4. Copy `<game>_symbols.h` to `generated_symbols.h` when present.
5. Only use legacy generic discovery when there is no Wii/title-aware tree at all.

Expected marker:

```text
[OpenSPM] Normalized current DolRecomp Wii title-aware output: R8PE01.h/R8PE01.c -> generated.h/generated.c
```

Regression coverage is in `tools/native_bridge/test_llvm_generated_layout.py`, including the exact stale-alias migration shape and an incomplete-Wii-output fail-closed case.

Code fix commit: `3e30a978233d9a671246e34ea2a72c47e9ab5188`
Regression test commit: `c389eb4d2fe7a63257c716b385ff608c3d11fd5d`

## Next run

Run `M14-LLVM-UPDATE-RUN.cmd` again from a normal shell. Before the runtime launches, verify the terminal now shows all of these:

```text
... R8PE01_generated\chunks (14416 files)
[OpenSPM] Normalized current DolRecomp Wii title-aware output: R8PE01.h/R8PE01.c -> generated.h/generated.c
M14 OpenSPM LLVM generation ready: objects=14416 ...
-- OpenSPM M14 validated LLVM manifest: 14416 native objects ...
```

The exact object count can change if future partitioning changes; the invariant is that DolRecomp's current title-aware manifest and OpenSPM's validated manifest describe the same generation. The previous 14416/14414 mismatch must be gone.

If linking succeeds, a new runtime log should finally exercise v24. The proof markers to look for are:

```text
M14 LLVM relF deterministic-tail allocation active: ...
M14 LLVM relF tail allocation result: base=... prolog=... empty_tail=... match=yes|no; old codegen base remains 0x80C03F40 for this proof run.
```

Do **not** rebase relF codegen merely because the module links. A `match=yes` runtime result is still required first.

## After `match=yes`

Before changing `build_relf_codegen_dol.py` from old codegen base `0x80C03F40` to the proven deterministic live base, census relF relocation records for dependencies from executable sections to relF BSS. Runtime `relMain()` allocates BSS separately from the REL file, while the current codegen image models BSS at a synthetic address. If executable relocations depend on that BSS address, deterministic REL-file placement alone is insufficient; BSS placement must also be made deterministic or represented correctly in codegen.

Generated-C rollback remains unchanged throughout this proof.
