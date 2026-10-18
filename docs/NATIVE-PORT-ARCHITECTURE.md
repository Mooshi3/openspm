# OpenSPM native-port architecture

## End state

OpenSPM is a native PC port runtime for Super Paper Mario, not a Wii emulator.

The player supplies a legally obtained R8PE01 disc image or extracted game. OpenSPM verifies it and locally produces/loads native host code derived from that player's copy. The public repository and distributable packages contain only OpenSPM-owned code, metadata needed for compatibility, and explicitly compatible third-party dependencies. They do not contain Nintendo game code, assets, DOL/REL payloads, decoded textures/audio, or a bundled disc image.

The finished program should behave like an ordinary PC game: native executable/runtime, keyboard and modern controller input, ordinary PC files for saves/configuration, modern graphics APIs, arbitrary display resolutions/aspect ratios, high presentation frame rates, optional interpolation, fast loading, profiling, and a stable mod API.

## Non-goals

The final architecture must not depend on Dolphin, a hidden Dolphin process, a Wii system image/NAND, Wii Bluetooth/USB/storage device emulation, or a general-purpose PowerPC interpreter/JIT for ordinary gameplay.

Temporary compatibility shims are acceptable only as migration scaffolding. Each must have an explicit replacement path and must not become the permanent way the game runs.

## Execution model

1. **Player-owned input** — verify the source game's identity before translation.
2. **Local static recompilation** — translate the game's executable code into native host code in the player's local cache. Generated Nintendo-derived code is never committed or redistributed.
3. **Direct native calls** — ordinary game-to-game calls stay native and do not bounce through a dispatcher.
4. **Semantic host boundaries** — calls that represent console platform services side-exit into small OpenSPM-owned PC services: filesystem, saves, settings, input, timing/events, audio, and graphics.
5. **No console device stack as the destination** — once a semantic service exists, do not execute the original Wii device/IPC stack just to reach the same result.

`state/m14-host-targets.json` is the current machine-readable list of static function targets that must remain host boundaries during M14. It is migration data, not the final mod API.

## Graphics plan

Graphics has two deliberate stages.

### Stage A — correctness renderer

Get the native game visually playable without an emulator. The recompiled game may continue to emit its GX command stream while an OpenSPM-owned renderer decodes that stream and submits equivalent work to a modern graphics backend.

`include/openspm/gx_platform.h` and `core/gx_platform.c` define the CPU/renderer seam. The renderer owns:

- GX command consumption;
- guest-address to host-memory translation for vertex/index/texture data;
- draw completion;
- draw-sync tokens;
- actual raster bounding-box feedback;
- frame presentation.

The reusable runtime must never substitute the old fixed 64x64 diagnostic bounding box. If real renderer feedback is unavailable, the boundary fails rather than silently inventing raster results.

Aurora (`encounter/aurora`) is a promising MIT-licensed modern GX/WebGPU foundation, but it is not a drop-in solution for OpenSPM's original-game FIFO. Upstream Aurora is primarily a source-port compatibility layer: original-console CP array-base translation and GX bounding-box behavior need OpenSPM-specific adaptation. If Aurora is used, pin an audited upstream revision and keep OpenSPM's guest-memory/bbox glue in a clearly separated adapter.

### Stage B — game-aware native renderer

Once visual correctness is stable, move hot or restrictive rendering paths above the low-level GX compatibility layer. The long-term model is OpenPete-like: consume stable game/render state directly where that produces cleaner PC behavior, while leaving a compatibility path for effects that have not been lifted yet.

This stage enables the features we actually want rather than merely reproducing Wii hardware limits:

- internal resolutions independent of the original EFB size;
- true ultrawide camera/projection behavior;
- modern depth precision and filtering;
- high-resolution textures and mod-provided assets;
- render-time motion interpolation;
- presentation frame rates decoupled from the game's simulation tick;
- optional modern post-processing and anti-aliasing.

Interpolation must be presentation-only unless a specific gameplay system is proven safe to retime. The default rule is: preserve the original simulation/event timing and render intermediate visual state between completed game ticks. Never write interpolated transforms back into gameplay-readable memory merely to obtain smoother output.

## Platform services

The native runtime should progressively replace console-facing behavior with narrow PC-native contracts:

- **GameFS:** read player-owned extracted files through ordinary host I/O.
- **Save:** serialize the game's logical save data to ordinary PC files with atomic writes/backups.
- **Input:** map keyboard/controllers to the game's normalized per-frame input state; no Wii Bluetooth transport.
- **Settings:** expose only the system settings the game semantically needs.
- **Timing/events:** drive retrace/frame events and the game's scheduler from a native clock while preserving game-visible ordering.
- **Audio:** feed the game's logical audio command/data path into a native mixer/backend; do not require Wii DSP hardware emulation as the final solution.
- **Graphics:** use the renderer seam above; no fixed diagnostic PE values.

Broad IOS compatibility stubs that exist during M14 are debt. Replace them with the specific semantic services actually used by SPM, then remove those generic IPC boundaries from the target manifest.

## Mod architecture

Mods target OpenSPM contracts, not emulator internals. Stable interfaces should be versioned and use semantic names/types. Raw guest addresses are acceptable inside compatibility adapters and version-specific symbol maps, but should not be the primary public modding API.

Target capabilities include:

- lifecycle/frame/event hooks;
- safe game-state queries and mutations;
- custom items, characters, Pixls and other data-driven content where the game permits extension;
- renderer hooks and custom assets/shaders;
- input hooks;
- save-extension namespaces;
- optional symbol/debug interfaces for advanced native mods.

Mods should be able to add content without consuming or overwriting fixed original REL slots whenever OpenSPM can provide a native extension table instead.

## M14 completion gate

M14 is complete only when a reproducible Windows-native package, from a clean repository plus the player's own verified game, can:

1. locally generate/load the native game module;
2. boot through the real startup flow to the title screen without a forced sequence jump;
3. accept real keyboard/controller input and proceed through file select/load;
4. render visible frames through the native renderer with real draw completion, draw-sync and bounding-box feedback;
5. use persistent OpenSPM GameFS/settings/input/save/timing/audio services rather than private diagnostic glue;
6. run without loading Dolphin or ModernGekko/RecompCore as a gameplay dependency;
7. keep every Nintendo-derived generated file and asset out of the public repository/package.

After that gate, performance and presentation work becomes the priority: profile native CPU hotspots, eliminate remaining dispatcher-style crossings, lift rendering paths, add interpolation, and raise presentation frame rates without changing simulation correctness.

## Rules for future work

- Keep `m12-native-display` frozen as a regression baseline; do not develop M14 on it.
- Prefer a reproducible committed service over another one-off diagnostic patch.
- Do not call a diagnostic fast-forward rate an FPS measurement.
- Do not hide correctness problems behind synthetic GPU values in production code.
- Do not commit generated game code/assets to make CI convenient.
- Optimize only after measuring host CPU/GPU time or demonstrating a controlled speedup.
- Every compatibility shim should move OpenSPM toward the native-port end state above, not toward a more complete general Wii emulator.
