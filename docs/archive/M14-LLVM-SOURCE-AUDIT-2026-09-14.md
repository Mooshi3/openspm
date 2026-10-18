# M14 LLVM source audit — 2026-09-14

## Scope

This audit was done before the first player-side Windows LLVM compile/boot. It
checks the pinned DolRecomp/OpenSPM interface assumptions that could otherwise
produce a build which compiles successfully but silently loses M14 semantics.

The architecture remains fully standalone OpenSPM at runtime:

```text
player-owned SPM code -> DolRecomp/LLVM build tooling -> native COFF objects
                       -> openspm_local_native.dll -> OpenSPM/Aurora runtime
```

No Dolphin, ModernGekko or RecompCore runtime is being restored.

## Upstream facts now verified

Pinned upstream revision:

`ExpansionPak/DolRecomp@40637c4683bd2820ac5b23607ee344720beb26df`

### 1. Ordinary LLVM output really defines the bridge macro

DolRecomp's non-ModernGekko LLVM pipeline calls `emit_header_for_cpu()` and then
writes:

```c
#define DOLRECOMP_BACKEND_LLVM 1
```

into `generated.h`. OpenSPM's validator already requires that marker, so the
LLVM-only sections in bridge v15/v16/v17 cannot silently compile out on a valid
candidate tree.

### 2. v17 shared-gather ABI matches the proven v8 storage exactly

The underlying bridge declares:

```c
static uint32_t g_m14_v8_have_gx_fifo;
static uint8_t  g_m14_v8_wg_bytes[32];
static uint32_t g_m14_v8_wg_size;
static uint32_t g_m14_v8_wg_pending_stores;
```

Bridge v17 exports pointers with exactly those widths. There is no hidden
size/alignment mismatch between generated LLVM loads/stores and the proven dev16
gather state.

### 3. Native-region interception is a real side exit, not merely an optimizer hint

Every ordinary DolRecomp LLVM range entry checks `ppc_native_region_available()`.
When OpenSPM's `host_call` blocks the range, generated LLVM materializes the
range's `entry_pc` into `CPUState` and returns from the native body.

The generated dispatcher then handles the materialized PC in this order:

1. replacement dispatch;
2. `host_call`;
3. native generated function lookup/entry.

That is exactly what bridge v15 needs: block a protected seam range, let the
public dispatcher invoke the v19 hook at that PC, arm a one-shot permit, then
immediately re-enter the same LLVM range.

### 4. `PC+4` durable-return seams really force the linked-call continuation out

`derive_v19_llvm_seams.py` writes each GX call's `PC+4` continuation into the
player-local MAP. Pinned DolRecomp adds every executable MAP symbol to its LLVM
partition-point set, and the range builder cuts a native range at the next such
point.

For a linked native call, DolRecomp can resume directly only when that
continuation is local to the caller's current native range. A continuation at
the start of a separately partitioned range is not local, so DolRecomp
materializes/side-exits at the continuation. The public dispatcher therefore
gets a durable opportunity to run the v19 after-return hook even when the real
Nintendo SDK function executed natively and yielded no C wrapper frame.

This validates the intended architecture:

```text
LLVM caller -> protected GX entry side-exit -> v19 before-hook -> permit
            -> real LLVM GX SDK body
            -> PC+4 partition side-exit -> v19 after-return hook -> permit
            -> LLVM caller continuation
```

### 5. Patched emitter objects cannot be confused with stock persistent-cache objects

DolRecomp's persistent LLVM cache key includes `DOLRECOMP_CODEGEN_SOURCE_HASH`.
CMake computes that digest from the codegen source set, which includes
`src/backend/llvm/memory_service.cpp`.

OpenSPM patches that exact private source file before building DolRecomp.
Therefore the inline-WPAR emitter automatically receives a different cache key
from the stock emitter; a stock cached object with identical guest bytes/options
cannot be reused as the patched candidate.

## New OpenSPM object-level proof

Source markers and cache safety are necessary but not sufficient. A candidate
could still be misleading if future upstream/codegen drift caused the private
patch to stop surviving into emitted native objects.

`validate_llvm_generated.py` now has an OpenSPM-specific proof mode. When
`--require-openspm-wpar` is enabled it requires the final COFF/ELF object set to
contain link/string-table references to all six private bridge symbols:

```text
openspm_m14_llvm_wg_have_fifo_ptr
openspm_m14_llvm_wg_bytes_ptr
openspm_m14_llvm_wg_size_ptr
openspm_m14_llvm_wg_pending_ptr
openspm_m14_flush_wg_llvm
openspm_m14_try_wg_write_llvm
```

Long undefined symbol names remain present in COFF/ELF string tables, so this
proof requires no `llvm-nm` dependency. The scan stops as soon as all symbols
have been observed.

The proof is required at all three relevant boundaries:

- immediately after `generate_local_llvm.py` emits the player objects;
- again in `build_local_llvm_module.py` before module configuration;
- again from the LLVM CMake path immediately before linking the DLL.

`test_llvm_native_pipeline.py` contains both a positive synthetic object contract
and a negative contract proving that a stock/native object tree still passes
generic validation but fails the OpenSPM-specific WPAR requirement.

The generated provenance now records:

```text
openspm_inline_wpar_symbols=proven
```

A first Windows run which reaches CMake therefore cannot accidentally measure a
stock LLVM backend while claiming to measure the direct inline WPAR candidate.

## REL audit

The private relF relocation/augmentation path was compared against the pinned
DolRecomp REL implementation. The implemented relocation subset and module-0 /
self-module resolution semantics match upstream's loader model. The compiler
image still inserts only the proven relocated executable section at
`0x80C0401C`; the clean DOL remains the runtime identity and boot image.

The player preflight remains the authoritative proof of the actual retail
relF structure before compiler work begins.

## What remains unproven

Nothing in this source audit substitutes for the real Windows build/runtime.
Still unproven:

1. LLVM 19/20 discovery on the player's machine;
2. pinned DolRecomp + private emitter compile under the local MSVC/LLVM install;
3. full R8PE01 object generation and DLL link;
4. module load and first strict-native boot;
5. v19 graphics parity in real gameplay;
6. whether eliminating per-store state materialization/callbacks supplies the
   required ~1.5x heavy-frame producer throughput.

## Next validation

Use only:

```text
M14-LLVM-UPDATE-RUN.cmd
```

for the first transactional LLVM A/B. `M14-UPDATE-RUN.cmd` remains the normal,
runtime-proven C/v13 fallback.

On success, require the inline-WPAR runtime marker, verify protected v19 graphics,
then compare `spmarioDisp` / `spmarioMain` phase timing and LLVM WPAR counters in
the known heavy Chapter 1 room. On failure, fix the earliest failing pipeline
stage instead of bypassing a preflight.