# M14 LLVM relF runtime-base frontier — 2026-09-15

## Runtime proof from `launch(20260915-031935).log`

The nonblocking frame-phase bridge worked: LLVM passed the previous `spmarioMain` continuation frontier, KPAD and the HOME/system-UI seam remained active, and native execution advanced until the standalone bridge reported:

```text
Native migration boundary reached reason=3 detail=0x0000000C source=0x80AF1DDC
```

`reason=3/detail=12` is the bridge's `dolrecomp_call()` miss: the guest PC has no native function in the loaded LLVM object set. It is not an MMIO or Wii-service failure.

## Exact relF correlation

The player-local codegen path currently relocates retail `relF` at the older observed canonical module base `0x80C03F40`; its executable section begins at `0x80C0401C`. Earlier runtime work identified the canonical linked prolog as `0x80C4205C`, so the prolog offset within the module is:

```text
0x80C4205C - 0x80C03F40 = 0x0003E11C
```

Applying that offset to the new missing PC gives:

```text
0x80AF1DDC - 0x0003E11C = 0x80AB3CC0
```

and translating the new PC by the base delta lands exactly on the old canonical prolog:

```text
0x80C03F40 - 0x80AB3CC0 = 0x00150280
0x80AF1DDC + 0x00150280 = 0x80C4205C
```

This is strong proof that `relF` was linked at live base `0x80AB3CC0` in this run while the LLVM native code exists at the old `0x80C03F40` codegen base.

## Why PC rebasing is not a correct fix

SPM's `relMain()` allocates the decompressed REL from `HEAP_MAIN`, allocates BSS, calls `OSLink`, then calls the prolog pointer written/relocated by the linker. The live allocation address is therefore part of the REL's relocation semantics. Absolute/intra-REL pointers in the linked image move with the module.

A dispatcher-only alias from `0x80AB...` to `0x80C0...` would execute native code compiled with the old absolute relocation values while guest memory contains OSLink's new relocation values. That can silently corrupt data/function references even if the first PC dispatch appears to work. Do not use a PC-only alias as the production solution.

## v24 proof strategy

`legacy_c_adapter_v24.c.in` makes only the relF allocation deterministic and leaves the clean player DOL untouched.

Revolution `MEMAllocFromExpHeapEx` uses the sign of the alignment argument as its allocation direction. Positive alignment allocates from the head; negative alignment allocates from the tail while still creating an ordinary tracked MEMExpHeap block. v24 therefore redirects the uniquely verified `relMain -> __memAlloc(HEAP_MAIN, 0x31BEC8)` allocation into the real guest `MEMAllocFromExpHeapEx` with alignment `-0x20`.

Do **not** rely on intercepting the `__memAlloc` callee entry: LLVM can direct-lower a guest direct call without another dispatcher query. `derive_v19_llvm_seams.py` therefore verifies `0x8023C978 == 0x4BF69031` (`bl 0x801A59A8`) in the clean player DOL and emits explicit partition points at:

```text
0x8023C978  relF allocation callsite
0x8023C97C  PC+4 return continuation
```

This makes both sides of the proof materializable regardless of native direct-call lowering. Adding those partition points can re-emit the small set of LLVM objects whose ranges/ABI edges change; it does not require throwing away the whole 14k-object cache.

The redirect fails closed unless all of these exact R8PE01 facts still hold:

- current PC is the partitioned callsite `0x8023C978`;
- allocation size is `0x31BEC8` and heap id is `HEAP_MAIN == 0`;
- `0x8023C978 == 0x4BF69031` (`bl __memAlloc`);
- `0x801A59D4 == 0x38A00020` (`li r5,+0x20` in the normal wrapper);
- `0x801A59D8 == 0x480EE0B5` (`bl MEMAllocFromExpHeapEx`);
- `memory_wp @ 0x8056CB68` resolves a sane MEM1 `MemWork`;
- HEAP_MAIN handle/start/end are sane and large enough.

`MemWork` offsets for US2 are the public game layout: heap handle `+0x00`, heap start `+0x24`, heap end `+0x48`.

On success, v24 reproduces the architectural link result of the verified BL while entering the real SDK allocator directly:

```text
r3 = live HEAP_MAIN MEMHeapHandle
r4 = 0x31BEC8
r5 = -0x20
pc = 0x80293A8C   ; real guest MEMAllocFromExpHeapEx
lr = 0x8023C97C   ; original relMain continuation
```

It intercepts the partitioned return continuation once to log the actual allocation base and `base + 0x3E11C` prolog. The current old codegen base is deliberately retained for this proof run, so a later detail-12 stop at that newly logged prolog is expected.

If the tail allocator returns NULL, v24 retries the original `0x8023C978` BL once, restoring the game's normal `__memAlloc` failure/assert behavior. Signature or heap-shape drift likewise bypasses v24 and executes the original callsite.

## Acceptance criteria for the next run

Expected markers:

```text
M14 LLVM relF deterministic-tail allocation active: ... heap=[...,...) empty_tail=...
M14 LLVM relF tail allocation result: base=... prolog=... empty_tail=... match=...; old codegen base remains 0x80C03F40 for this proof run.
```

The returned base is the address to consider for the player-local relF codegen if the tail placement proves deterministic. Do **not** change `REL_BASE` before observing this result.

After the base is proven, update the codegen-only relF canonical base and let exact-stamp cache hashing re-emit only native objects whose relF bytes/ranges/relocation-derived entry set changes. Then rerun and require the relF prolog to execute natively instead of hitting detail 12.

## Secondary observations

The CP-array mapping errors printed after the migration boundary are currently secondary shutdown/render fallout. Fix the CPU relF placement mismatch before treating those renderer messages as a new graphics frontier.

Generated-C remains the rollback authority and is not changed by v24.
