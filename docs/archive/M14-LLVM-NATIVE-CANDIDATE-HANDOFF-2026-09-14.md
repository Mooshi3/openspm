# M14 standalone LLVM-native candidate frontier — 2026-09-14

## Purpose

This is an **OpenSPM standalone-native** experiment. It does **not** restore the
old ModernGekko/Dolphin runtime.

DolRecomp + LLVM are used only as player-local compiler tooling:

```text
player clean R8PE01 WBFS / extracted DATA
        |
        +-- clean sys/main.dol -----------------------------+
        |                                                    |
        +-- /rel/relF.bin -- OpenSPM LZ10 decode -- relF ----+--> relocate at proven base
                                                                  |
                                                                  v
                                                        codegen-only augmented DOL
                                                                  |
                                                                  v
                                                       DolRecomp LLVM compiler
                                                                  |
                                                                  v
                                                         host-native COFF .o
                                                                  |
                                                                  v
                                              openspm_local_native.dll
                                                                  |
                                                                  v
                          OpenSPMNative + openspm.native + Aurora renderer
```

No Dolphin, ModernGekko or RecompCore runtime process/library participates in the
launched game. `--runtime recompcore` is DolRecomp's name for its ordinary
`CPUState*` generated-object ABI, not a request to launch RecompCore. The objects
are linked directly into OpenSPM's player-local DLL and are driven by the same
OpenSPM native bridge used by M14.

The normal `M14-UPDATE-RUN.cmd` generated-C path remains the default/proven path.
The LLVM candidate is deliberately isolated behind `M14-LLVM-UPDATE-RUN.cmd`.

## Player-local code image

The runtime-proven M14 source tree was not DOL-only. Its 218 generated chunks
were:

- 193 main-DOL chunks;
- 25 live `relF` executable chunks.

The old private tree represented the REL executable code as `text2`, immediately
after DOL `text0`/`text1`.

### Retail relF storage

A clean retail DATA-partition extraction does **not** normally contain an
uncompressed `relF.rel`. SPM's real loader prefers `/rel/relF.bin`, which is
Nintendo/CX **LZSS10 (type 0x10)** compressed. The game supports an uncompressed
`relF.rel` only as a fallback when `relF.bin` is absent.

OpenSPM mirrors that policy in `tools/native_bridge/relf_input.py`:

1. prefer the unique player-owned `relF.bin`;
2. require LZ10 magic `0x10` and the exact declared decompressed size
   `0x31BEC8`;
3. bounds-check every literal/backreference and overlapping copy;
4. otherwise accept a unique exact-size `relF.rel` fallback;
5. record both compressed-source and decoded SHA-256 in player-local provenance.

No external decompression utility and no decomp-project payload is needed.

### Linked/codegen image

The new compiler pipeline reproduces the historical generated address space
without a private rescue archive:

- clean DOL remains the runtime/source identity;
- decoded relF is module 1, version 3, 18 sections, size `0x31BEC8`;
- proven load base `0x80C03F40`;
- executable section 1: file offset `0xDC`, address `0x80C0401C`, size `0x63D88`;
- module-0 relocations resolve to absolute DOL addresses;
- self-relocations resolve through the proven linked section layout;
- imports from another REL fail closed;
- only relocated executable section 1 is inserted into unused DOL text slot 2;
- the resulting augmented DOL lives under `.openspm` and is **compiler input only**.

OpenSPM never boots, ships, uploads or identifies the game by the augmented DOL.
The player-local module identity header still hashes the clean original
`sys/main.dol`.

Core tools:

- `tools/native_bridge/relf_input.py`
- `tools/native_bridge/build_relf_codegen_dol.py`
- `tools/native_bridge/generate_local_llvm.py`

## LLVM toolchain

Pinned compiler revision:

- `ExpansionPak/DolRecomp@40637c4683bd2820ac5b23607ee344720beb26df`

The compiler checkout/build stays under:

- `.openspm/m14/llvm-candidate/dolrecomp-toolchain`

`prepare_dolrecomp_llvm.py`:

1. owns/resets only its marked private checkout;
2. checks out the exact pinned commit;
3. applies OpenSPM's private LLVM WPAR codegen patch;
4. finds an LLVM **19 or 20** development package and skips positively identified
   unsupported LLVM 21+ packages;
5. builds `dolrecomp` with `DOLRECOMP_ENABLE_LLVM=ON`;
6. proves the resulting executable can emit native objects using a synthetic DOL;
7. writes `toolchain.json` with `runtime_dependency=false` and the exact matching
   `src/` CPU runtime root.

The generated module compiles `cpu.c` / headers from this same pinned DolRecomp
revision, preventing `CPUState` layout/helper drift between the emitter and DLL.

## v19 display-list seam preservation

Generated C implemented protected graphics milestone v19 with source-injected
before-call / durable-return hooks around:

- `GXBeginDisplayList = 0x80291DC8`
- `GXEndDisplayList   = 0x80291E78`

LLVM objects cannot be source-text rewritten. Instead:

- `derive_v19_llvm_seams.py` scans the verified clean DOL;
- it requires exactly three direct linked calls to each SDK entry;
- it writes caller, caller+4 continuation, GX entry and frame-phase addresses to
  a player-local DolRecomp MAP;
- current DolRecomp treats every MAP symbol address as an LLVM partition point;
- bridge v15 blocks native regions containing protected seam PCs through
  `PPC_HOST_CALL_NATIVE_REGION_QUERY`;
- architectural state is materialized only at that protected boundary;
- a one-shot permit lets the real guest instruction/region run after the v19
  before/after hook executes.

This retains the protected v19 physical display-list recorder architecture rather
than replacing it with a new graphics implementation.

## Broadway WPAR breakthrough candidate

The main performance hypothesis is now much narrower than the rejected FIFO
coalescer experiments.

Stock DolRecomp LLVM generic MMIO stores materialize architectural `CPUState`,
call `CPUState::external_write`, then reload live state. SPM produces millions of
Broadway WPAR/GX stores per host second in heavy scenes.

OpenSPM already has a proven native 32-byte Broadway gather in bridge dev16. The
LLVM candidate moves the **common append operation** into generated native code:

```text
normal RAM store
    -> direct native MEM1/MEM2 pointer store

Broadway WPAR store, gather has room
    -> generated LLVM range check
    -> direct big-endian bytes into dev16 gather storage
    -> increment gather size/pending count
    -> continue with guest registers in LLVM SSA

WPAR store completing 32-byte line
    -> same native append
    -> one C call to bridge_flush_wg_v8()
    -> existing OpenSPM GX FIFO submit/order/error semantics

rare multi-byte line overflow
    -> v16 helper preserves flush-before-append behavior

non-WPAR MMIO
    -> unchanged generic DolRecomp external-write path
    -> existing v8 bridge flushes any pending WPAR bytes first
```

So the common WPAR path avoids both:

1. full architectural state materialization/reload; and
2. one C callback per individual WPAR store.

The state is not duplicated. Bridge v17 exposes pointers to dev16's exact
existing static gather buffer, size, pending-store count and FIFO-available flag.
All old semantic-boundary flushes therefore still see the same state:

- generic external reads/writes;
- sync/eieio/isync;
- semantic host calls;
- presentation safepoints;
- teardown.

Files:

- `tools/native_bridge/apply_openspm_llvm_wpar_codegen_patch.py`
- `tools/native_bridge/legacy_c_adapter_v16.c.in`
- `tools/native_bridge/legacy_c_adapter_v17.c.in`

Expected first runtime marker:

```text
M14 LLVM inline WPAR gather active: common Broadway GX stores append directly in native LLVM SSA and call C only at completed 32-byte lines; dev16 buffer/order/flush semantics preserved.
```

Expected shutdown stats:

```text
M14 LLVM inline WPAR stats completed_lines=... inline_stores=... failures=...; inline_stores avoided per-store C/MMIO materialization callbacks.
```

If the first marker never appears in gameplay, do not judge the performance
hypothesis yet: the patched path was not actually hot.

## Transactional player A/B

Player entry point:

```text
M14-LLVM-UPDATE-RUN.cmd
```

It is intentionally separate from `M14-UPDATE-RUN.cmd`.

Before expensive compiler work, `run_m14_llvm_candidate.py` requires:

1. clean `main.dol` SHA-256 matches `state/m14-runtime-frontier.json`;
2. retail `relF.bin` LZ10-decodes (or exact-size `relF.rel` fallback loads) and
   matches the proven REL structural fingerprint above;
3. exact v19 DOL seam shape;
4. staged C player DLL byte-matches the independently built normal C DLL at
   `.openspm/m14/local-module-build/module/openspm_local_native.dll`.

The fourth rule prevents a power-loss/interrupted old experiment from becoming
its own rollback source. If the two C DLLs differ, run normal
`M14-UPDATE-RUN.cmd` once; the LLVM runner refuses to continue.

For the one-shot launch:

1. build LLVM module under `.openspm/m14/llvm-candidate`;
2. hash/copy the independently known-good C DLL into transaction backup;
3. copy/hash-verify LLVM DLL into the normal staged module location;
4. launch the same strict-native OpenSPM candidate;
5. restore/hash-verify the C DLL in a `finally` block after normal exit or crash.

A machine power loss can bypass any process `finally`, which is why the next run
also verifies the staged DLL against the normal C build before it will start.

## Source contracts

`tools/native_bridge/test_llvm_native_pipeline.py` is the mandatory first step of
the LLVM runner. It is game-data-free and checks:

- retail LZ10 relF decoding, including overlapping backreferences and the game's
  compressed-first/fallback-second filename policy;
- relF text2 augmentation model;
- relocation primitives;
- v19 seam scanner;
- exact generated-object manifest validation and CMake-safe object paths;
- C default / LLVM opt-in CMake separation;
- v15/v16/v17 bridge chain;
- inline SSA WPAR emitter patch shape;
- LLVM 19/20 / matching `src/` runtime toolchain assumptions;
- no ModernGekko/Dolphin runtime selection;
- player preflight and independent-C rollback requirements;
- presence/wiring of `M14-LLVM-UPDATE-RUN.cmd`.

## First Windows run triage

The first player run is primarily a **build/boot proof**, not yet a performance
claim.

Triage in this order:

1. source-only contract failure -> fix repo source first;
2. player-code preflight failure -> stale/incomplete extraction, bad retail LZ10
   input, or wrong game revision;
3. LLVM discovery failure -> locate/install LLVM 19/20 development package;
4. DolRecomp compile failure -> toolchain/API issue;
5. LLVM generation failure -> augmented-DOL/relocation/partition/codegen issue;
6. local DLL link failure -> COFF/generated-header/C symbol/runtime ABI issue;
7. module load/boot failure -> bridge ABI/region-seam correctness issue;
8. visible regression -> compare protected v19 seam hits and WPAR stats before
   changing renderer behavior;
9. correct boot but unchanged heavy-scene performance -> use WPAR stats to prove
   whether per-store materialization was actually removed, then profile the next
   producer cost rather than reverting to broad renderer guesses.

Do not use GitHub Actions for M14 validation. The intended validation is the
player's local Windows build/runtime.

## Current branch frontier

This file is a living handoff. Check branch HEAD rather than treating a prose SHA
as a fixed milestone. The normal C/v13 path remains authoritative until a player
run proves the LLVM candidate.

Do not replace `M14-UPDATE-RUN.cmd` automatically based on source review alone.
