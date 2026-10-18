# M14 — Standalone Native Execution Core

## Decision

M14 is the point where OpenSPM starts replacing the Dolphin/ModernGekko training-wheel runtime with the standalone native architecture that was always the project target.

The decisive OpenSPM A/B on 2026-09-08 used the same OpenSPM/ModernGekko build and game data while changing only the CPU core selection:

- current StaticRecomp path: Bowser crowd ~32–33 FPS
- Dolphin JIT64 path: 60 FPS normally, and a solid ~120 FPS with fast-forward held in the same heavy scenes

That establishes two things:

1. the host, renderer and Wii-service implementation have ample performance headroom;
2. the current StaticRecomp execution contract is not suitable as the final OpenSPM game-code runtime.

The protected M12 line remains frozen and is not rewritten in place. M13/v19 remain reference/training-wheel builds while M14 comes up separately.

## Permanent distribution model

The final OpenSPM distribution model remains:

```text
player-supplied clean SPM ROM/WBFS
              |
              v
      OpenSPM verify/extract
              |
              v
   local native-code generation
              |
              v
          OpenSPM.exe
      + locally generated module
      + OpenSPM-owned runtime/services
```

Rules:

- OpenSPM does not ship Nintendo game code, assets, or proprietary symbol tables.
- The player supplies their own game image.
- The extracted game is immutable source input; generated data, patches, saves and caches live under OpenSPM-owned writable state.
- Public OpenSPM APIs do not expose Dolphin/ModernGekko/RecompCore implementation types.
- Dolphin/ModernGekko/RecompCore may be used during development as behavioral references and migration fallback, but are not dependencies of the final shipped runtime.

## Target architecture

The desired execution model is ordinary native program structure:

```text
OpenSPM.exe
  -> locally generated native SPM functions
       -> direct native calls
       -> compiler-managed locals/registers
       -> direct MEM1/MEM2 fast paths where valid
       -> OpenSPM service vtables at actual platform/hardware boundaries
  -> OpenSPM runtime
  -> OpenSPM graphics/audio/input/filesystem/platform implementations
```

The common SPM path must not bounce through a guest-PC dispatcher after small fragments. Guest architectural state, source addresses, exact Wii cycle accounting, migration fallback and instrumentation are boundary mechanisms, not the representation of ordinary game logic.

## What the training wheels taught us

The existing StaticRecomp/ModernGekko stack remains extremely useful as an oracle and bring-up chassis. It proved the game image, recompilation coverage, widescreen changes, mod hooks and test fixtures before the standalone runtime existed.

Upstream measurements independently show why it should not be the final execution architecture. ModernGekko's x86-64 JIT-vs-StaticRecomp experiment measured roughly 321 FPS on normal JIT64, ~80 FPS for JIT64 forced through the StaticRecomp contract, and ~68 FPS for the AOT module. The contract itself therefore carried a very large cost even before generated-code quality was considered.

Recent DolRecomp experiments are useful compiler research because they demonstrate native regions, SSA/register execution, direct control flow and boundary-only state materialization. They are reference material for the local code generator, not a requirement that the standalone OpenSPM runtime adopt ModernGekko's runtime ABI.

## OpenSPM-owned native ABI

M14 uses a new OpenSPM ABI under `include/openspm/native_api.h`.

Its design constraints are deliberate:

- no Dolphin, ModernGekko or RecompCore types;
- no public `CPUState`/`PowerPCState` contract;
- no proprietary symbol names;
- direct MEM1/MEM2 pointers for ordinary RAM traffic;
- OpenSPM-owned service discovery for actual host/platform boundaries;
- batched cycle accounting and control polling at compiler-selected safepoints;
- a native module may execute thousands of ordinary native calls before returning to the host;
- fallback/source-address information exists only for migration diagnostics and unsupported regions.

A locally generated module is identified by source-game metadata and a source hash so OpenSPM can verify that it belongs to the player's supplied input without redistributing that input.

## M14 rules

1. Protect M12 and current M13/v19 references.
2. No more one-function StaticRecomp performance patches unless required for correctness or migration evidence.
3. Avoid GitHub Actions for iterative experiments when local/static tests answer the question.
4. Fallback is a migration side exit, never the normal execution loop.
5. Preserve patchability/mod semantics, but express them through OpenSPM-owned services and invalidation rules.
6. Preserve Wii semantics first; relax only after correctness evidence exists.
7. New standalone-runtime code must be independently OpenSPM-owned code, not transplanted Dolphin/RecompCore implementation source.

## Implementation milestones

### M14.1 — Standalone native ABI + synthetic execution

Build and test the OpenSPM-owned native module contract with no Dolphin/ModernGekko dependency:

- versioned native module query/API;
- direct MEM1/MEM2 access;
- OpenSPM service discovery;
- batched timing accounting;
- pause/stop polling at coarse safepoints;
- native session lifecycle and exit reasons.

Success gate: a synthetic module executes thousands of normal C call chains, mutates native game memory directly, performs only coarse host callbacks, and stops cleanly. No guest-PC dispatcher participates in its hot path.

Status: **implemented and locally smoke-tested**. `m14_native_runtime_test` executes 4096 direct native call chains and checks that cycle accounting/control polling happen once at the safepoint.

### M14.2 — Dynamic local-module loading

Load a locally generated native DLL through `openspm_native_module_query`, validate its ABI and source hash, and run it through the same standalone session API.

Success gate: OpenSPM can load/unload a synthetic DLL from disk and exercise every native exit class without a ModernGekko process or DLL.

Status: **implemented and locally smoke-tested**. `m14_native_loader_test` loads a real shared library, checks source-game ID + SHA-256 before creating a session, exercises SAFEPOINT/FALLBACK_REQUIRED/STOPPED/FAULT, and rejects mismatched source identity.

### M14.3 — First real SPM native region

Teach the local recompilation pipeline to target the OpenSPM native ABI. Start with a deliberately small, well-understood R8PE01 region.

Success gate: a real region generated from the player's verified input executes direct game code and direct RAM accesses in the standalone host, then returns only at an explicit OpenSPM safepoint. This milestone is not complete until the compiler path can preserve direct native calls across a multi-function region rather than relying on the legacy guest-PC dispatch structure.

Progress: **first player-derived region proof passed locally**. Without committing any game bytes, the M10 local generated-C source for R8PE01 region `0x80278220` was extracted into a local-only shared module, loaded through `openspm_native_library_open`, and executed through the new standalone session ABI. Across 10,000 generated matrix cases its 48-byte result matched the independently checked native formulation bit-for-bit; the generated routine also preserved its 51-cycle charge, return target and stack contract. The OpenSPM host saw exactly one coarse cycle-accounting boundary per complete region execution, with no StaticRecompCore/ModernGekko process involved.

This proves the new host ABI can execute real player-derived SPM code. The remaining M14.3 work is compiler-side: make cross-function guest calls become direct native calls/regions so the old local dispatcher is not merely moved inside the generated module.

### M14.4 — Main-DOL boot

Expand native coverage until the title screen and Mario's house boot through the standalone path. During migration, an unsupported region may side-exit to the training-wheel reference runtime, but counters must show the common game path increasingly remains native.

### M14.5 — Bowser performance gate

Run the existing Bowser fixture. Standalone native execution must reach stable 60 FPS without hiding work behind Dolphin JIT fallback. Measure uncapped headroom separately.

### M14.6 — Replace remaining Wii services

Move the runtime boundaries still supplied by Dolphin/ModernGekko onto OpenSPM-owned graphics, audio, input, timing, filesystem and platform implementations. Each replacement gets differential tests against the training-wheel reference before the reference path is removed.

### M14.7 — RELs + public mod ABI

Compile/load SPM RELs into the same native model and expose stable OpenSPM game services so `.openspm` mods do not depend on raw guest addresses or emulator internals.

## Current known-good references

- protected M12 module SHA-256: `f50dea152b0fff11c6ffdc30555ef33b8cb92828363a863f45a14d0f4a6ec4ce`
- protected M12 branch: `m12-native-display`
- current performance/training-wheel base: `m13-phase5-perf-work`
- standalone-native branch: `m14-native-execution`
- historical OpenSPM ModernGekko pin: `5417826c31187d4dadf8588c7aa25bf107782936`
- historical OpenSPM RecompCore pin: `55c7b023fa0f4eba1cf3fdbbb25b1c5ec468d5ac`
- historical portable DolRecomp pin: `1bec3554ecc4817cf78319ca3d8a0669477f29fa`
