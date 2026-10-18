# M14 LLVM v24 proof-run readiness — 2026-09-15

## Current branch state

The durable relocation handoff at `f74f684991dafa9fb4ee6ca4d4c9e65a12b9daaa` said the next step was to activate `legacy_c_adapter_v24.c.in` in `tools/native_bridge/CMakeLists.txt`.

A fresh source audit found that instruction was already stale at that commit: the LLVM CMake branch already selects `legacy_c_adapter_v24.c.in`, while generated-C still selects `legacy_c_adapter_v13.c.in` as the rollback path.

The missing activation guard was `tools/native_bridge/test_llvm_native_pipeline.py`, which still only proved that older v17 text appeared somewhere in CMake.

Commit:

```text
62f9cc9fbb8934184d5437ca1a78fd847ce6b3f7
M14 LLVM: require active v24 relF proof bridge
```

now makes the source-only pipeline contract require:

- generated-C active bridge remains `legacy_c_adapter_v13.c.in`;
- LLVM active bridge is exactly `legacy_c_adapter_v24.c.in`;
- v24 includes v23;
- the v23 nonblocking frame-phase marker remains present;
- the relF allocation seam stays at `0x8023C978 / 0x8023C97C` and the verified BL word remains `0x4BF69031`;
- v24 still redirects through guest `MEMAllocFromExpHeapEx` with signed `-0x20`;
- v24 activation/result markers and `m14-dev34-llvm-relf-tail-proof` module version remain present;
- the proof-run codegen base is still deliberately `REL_BASE=0x80C03F40`, `REL_EXEC_ADDRESS=0x80C0401C`.

No generated-C behavior was changed.

## Allocator audit

Public RVL SDK/decomp implementations confirm the v24 return-address prediction for a pristine free tail:

```text
reqMBlock = RoundDown(freeBlockEnd - size, alignment)
```

for negative-alignment tail allocation. The returned pointer is the requested block pointer; the allocator creates its metadata immediately before it. Therefore v24's:

```text
align_down_32(heap_end - 0x0031BEC8)
```

is the correct empty-tail prediction. There is no additional hidden block-header subtraction required in that expected returned base.

## Important post-proof caveat: relF BSS

Do not declare the relocation architecture completely solved from REL-file base agreement alone.

SPM's real `relMain()` does this in order:

```text
relFile = __memAlloc(HEAP_MAIN, decompressed_size)
...
bss = __memAlloc(HEAP_MAIN, relFile->bssSize)
OSLink(relFile, bss)
relFile->prolog()
```

The BSS allocation is therefore separate from the REL-file allocation. By contrast, `build_relf_codegen_dol.py` currently assigns fileless REL sections from:

```text
align_up(REL_BASE + fix_size, bss_alignment)
```

That approximation is only harmless if the LLVM-emitted executable section has no relocation whose resolved target depends on a BSS section, or if the runtime BSS address happens to match that modeled address.

After the v24 proof yields the deterministic REL base, inspect the retail relF relocation stream and count executable-section relocation patches whose self-import target section is BSS. If the count is nonzero, also make the runtime BSS placement deterministic (or teach the codegen image the independently proven BSS address) before treating LLVM/live REL relocation state as aligned. Do not paper over this with PC aliasing.

This caveat does **not** block the first v24 proof run: that run exists only to measure and validate the deterministic relF allocation base while the old codegen base intentionally remains active.

## Next exact run

Run unchanged on the Windows player machine:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

Require all of these before changing `REL_BASE`:

```text
m14-dev34-llvm-relf-tail-proof
M14 LLVM frame-phase telemetry is nonblocking: ...
M14 LLVM relF deterministic-tail allocation active: ...
M14 LLVM relF tail allocation result: base=0x........ prolog=0x........ empty_tail=0x........ match=yes; old codegen base remains 0x80C03F40 for this proof run.
```

The expected next native-missing stop should move to the newly logged deterministic relF prolog because the LLVM objects are intentionally still compiled for `0x80C03F40` in this proof run.

If the v24 interception refuses, do not weaken any signature/heap checks. Diagnose the exact differing instruction/register/heap field from the log.

If the result says `match=no`, do not use the logged base as a supposedly heap-end-derived invariant until the tail free-list state is understood.

Only after a clean proof should the relF codegen base be changed and the affected LLVM objects regenerated.