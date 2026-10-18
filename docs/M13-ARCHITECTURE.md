# OpenSPM M13 platform architecture

M13 is the boundary between the research rig and the long-lived PC port architecture.
The final M12 branch and its proven Windows artifact remain immutable fallbacks.

## Stable layers

1. `OpenSPM.exe` is a thin launcher. It knows the OpenSPM ABI, paths, configuration inputs and backend discovery. It does not know ModernGekko, Dolphin, Wii structures or R8PE01 addresses.
2. `runtime/moderngekko_backend.dll` implements the backend ABI. The first M13 backend is intentionally transitional: it starts the byte-identical M12 runtime stored under `runtime/legacy/`. This proves the boundary without rewriting working gameplay code. Phase 4 replaces that implementation behind the same ABI with an in-process runtime library adapter while retaining the legacy adapter as fallback.
3. The backend ABI is size-prefixed and versioned. A later custom renderer backend or other runtime may replace the DLL without changing the launcher contract.
4. `mods/` is currently an active DLL canary surface. ABI-compatible DLLs are discovered deterministically and receive startup/shutdown events. The long-lived user-facing distribution unit is an `.openspm` package as defined in `MOD-PACKAGES.md`; the DLL becomes one possible trusted execution payload inside that package.
5. Generated/recompiled R8PE01 code remains game-specific. It must not leak ModernGekko or Dolphin types into the public OpenSPM headers.
6. `OpenSPM.ini` is portable launcher state stored beside the executable. The first successful explicit game path is persisted so subsequent launches do not need to ask for the extracted R8PE01 folder again. Mods can be disabled without deleting packages/payloads.

## North-star invariants

The near-term platform is judged by two independent green checks:

1. **Performance:** the Bowser minion-crowd stress scene is stable at 60 FPS rather than the observed ~24-31 FPS M12 slowdown.
2. **Extensibility:** a genuinely additive native-content proof (Goomba Capsule) works without replacing a vanilla slot.

The order is not strictly serial after Phase 4: once basic profiling and the in-process service boundary exist, performance diagnosis and item/save feasibility work may proceed in parallel. Neither should block keeping the game fully playable.

## Non-destructive content rule

The extracted R8PE01 directory is **immutable source input**. OpenSPM itself, its backends, and all official OpenSPM services must never patch, replace, rename, delete, or generate files inside the user's extracted game dump.

- Base-game files are opened for reading only.
- Code patches and function replacements are applied to loaded/recompiled state at runtime, never written back to `main.dol`, `_Main.rel`, or other source files.
- REL/file replacement mods use virtual overlays: a replacement shadows the base path while the original file remains untouched.
- Generated or converted assets belong under an OpenSPM-owned cache/overlay tree, not under the extracted dump.
- Saves, configuration, logs, caches, generated patches, mod state and future extended save data belong under `User-OpenSPM` (or an explicitly selected user root).
- Disabling/removing a mod must restore vanilla source behavior without requiring the user to repair or re-extract the game.
- Future patch/import tools must emit overlays or cached transformed copies rather than modifying the source dump in place.

The public launch contract marks the game root as read-only with `OPENSPM_LAUNCH_FLAG_GAME_ROOT_READ_ONLY`. Native DLL mods are trusted native code and technically have the same OS permissions as OpenSPM, so the platform cannot sandbox a deliberately malicious DLL without moving it into a restricted execution tier. The supported SDK therefore exposes non-destructive runtime/overlay services and never requires direct writes to the game root. A future sandboxed payload may enforce stronger capabilities, but it must not delay the native C/C++ SDK.

Persistent mod/content semantics are defined separately in `MOD-STATE-RECONCILIATION.md`. In particular, disabling a mod cannot permanently bake transient behavior into vanilla state, and owned additive content must have a lossless reconciliation path.

## ABI rules

- ABI v1 structures begin with `{struct_size, abi_version}`. New fields append to the end; existing fields never move.
- Public strings are UTF-8. Windows-specific UTF-16 conversion stays inside platform code.
- No public ABI exposes `CPUState`, Dolphin objects, GX implementation structs, guest pointers, or C++ types.
- Backends advertise capabilities rather than requiring launcher version checks.
- The host owns host callbacks for the lifetime of loaded backends and mods.
- Game-specific services are not frozen into ABI v1 until the in-process backend provides a real consumer and tests.
- `game_root_utf8` is a read-only source location; writable game-derived state must use the user/cache/overlay services instead.
- Creator-facing gameplay APIs are semantic services. Ordinary mods must not require guest addresses, Wii save layouts, unused vanilla slots, or full runtime rebuilds.

## M13 package shape

```text
OpenSPM.exe
OpenSPM.ini                 # created after first configured launch
runtime/
  moderngekko_backend.dll
  moderngekko_legacy_backend.dll   # retained during Phase 4 migration
  legacy/
    moderngekko-run.exe     # exact M12 bytes
    gR8PE01_recomp.dll      # exact M12 bytes
    Sys/
mods/
  openspm_debug.dll         # transitional first-party canary/debug shell
  *.openspm                 # intended long-lived installable unit
patches/
profiles/
sdk/include/openspm/
RUN-OPENSPM.cmd
RUN-LEGACY-BACKEND.cmd
RUN-DIRECT-M12-FALLBACK.cmd
BUILD-PROVENANCE.txt
SHA256SUMS.txt
```

The process-backed adapter is a migration shim, not the final runtime. Its value is that the user-facing entry point and ABI stop depending on that implementation immediately.

## Phase 4: parity before power

Phase 4 changes process ownership, not gameplay behavior. Its first acceptance criterion is that the proven ModernGekko/RecompCore runtime can live directly inside the OpenSPM process and still behave like M12.

Rules:

- the in-process backend is the candidate default;
- the process-backed adapter remains a one-DLL regression fallback;
- the exact proven M12 runner/module remain a direct fallback during migration;
- host-executable path assumptions (such as Dolphin `Sys` discovery) are made explicit rather than relying on `moderngekko-run.exe` being the process image;
- normal Windows boot parity is tested before exposing memory/gameplay services;
- a candidate is not called accepted until the Windows user test passes.

After parity, the in-process boundary can honestly provide lifecycle and game-service access instead of routing commands through a child process.

## First-party debug mod

`openspm_debug` is both a user-facing debug tool and the regression harness for the mod SDK. It proves real DLL discovery, ABI negotiation, lifecycle events and launch-profile plumbing. Gameplay manipulation waits for the in-process game service rather than smuggling R8PE01 addresses into the general host ABI.

Once that service exists, the intended debug surface includes:

- skip/short-circuit intro and other long test setup sequences;
- room, map, chapter and checkpoint warp;
- reload current room/scene;
- free camera and camera-state inspection;
- actor/NPC/script identity inspection;
- story/save flag inspection and controlled edits;
- toggles for presentation/culling/UI experiments;
- frame timing and subsystem performance overlays;
- direct warp presets for known stress scenes, including the Bowser minion crowd used as a stable-60 benchmark.

The intended lifecycle vocabulary is `on_launch_configure`, `on_game_initialized`, `on_scene_load`, `on_tick`, `on_present`, `on_scene_unload`, and `on_shutdown`. Exact game identifiers are derived from the running title rather than invented in the public API.

## First additive vertical slice

After in-process parity and the minimum service/ownership plumbing, the Goomba Capsule is the first end-to-end proof:

- a truly new namespaced item, not a replacement slot;
- price 0 in Flipside shop;
- ordinary 10-slot carried-inventory rules;
- 32-slot shared shop storage plus lossless Flipside mail fallback;
- native use callback spawns a Goomba through an actor service;
- namespaced persistence and clean disable/re-enable behavior;
- debug boot directly to the Flipside Shop for iteration.

This is intentionally more valuable than a toy callback: it proves the registry, stock-facing UI/shop integration, inventory semantics, native game service, and persistence model together.

## Exit criteria for M13

- M12 artifact hashes are verified during every M13 package build.
- `OpenSPM.exe` loads the backend only through the public ABI.
- Swapping the backend requires replacing one DLL plus any backend-private payload, not rebuilding the launcher.
- ABI negotiation and rejection paths have CI tests.
- DLL mod discovery/loading and lifecycle have CI tests with the actual first-party debug DLL.
- Game path/config state persists in `OpenSPM.ini`; `--no-mods` provides a recovery path.
- The extracted game dump remains byte-identical after supported OpenSPM/mod operations.
- M12 gameplay can still be launched through the transitional backend and through a direct byte-identical fallback.
- The in-process backend builds, loads through the ABI, packages its complete Windows runtime dependency set, and passes real Windows boot parity before it becomes an accepted baseline.
- Before M13 is frozen, add crash/provenance reporting and useful performance counters.

M13 does not yet claim that gameplay hooks are active or that the renderer has been replaced. Those are subsequent slices of the same milestone.