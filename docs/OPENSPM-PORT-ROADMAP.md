# OpenSPM port roadmap

## Product target

OpenSPM's destination is an ordinary native PC port of Super Paper Mario in the same broad class as projects such as OpenPete: the player supplies their own supported game image, OpenSPM locally verifies and derives what it needs from that copy, and the public project distributes only OpenSPM-owned code plus license-compatible third-party runtime components.

The finished player experience should be approximately:

```text
install OpenSPM
-> choose a legally obtained supported SPM disc image/WBFS
-> verify game revision
-> extract/translate/cache locally
-> launch as a native PC game
```

No Nintendo executable code, REL payloads, textures, audio, levels, decoded assets, generated recompilation output, or disc images belong in a public OpenSPM package or repository.

## Non-negotiable invariants

1. **Native execution is the destination.** Ordinary gameplay must execute statically recompiled/native code. Dolphin, ModernGekko/RecompCore, a general PPC interpreter/JIT, Wii NAND, and emulated console device stacks are migration or regression tools only, never release gameplay dependencies.
2. **Player-owned source material stays local.** A public package can contain hashes, symbol metadata, transforms, adapters, compilers/tooling, and compatible dependencies, but not game-derived output.
3. **Simulation correctness and presentation are separate.** High refresh, ultrawide, high resolution, post-processing, texture replacement, and interpolation must not silently retime gameplay or write presentation-only state back into memory the game can observe.
4. **Enhancements are game-aware where hardware-level compatibility becomes restrictive.** Stage A may decode the original GX stream for correctness. Stage B should lift stable render state into semantic PC-native rendering rather than preserving Wii limitations forever.
5. **Mods target semantic/versioned OpenSPM interfaces.** Raw R8PE01 addresses remain useful compatibility metadata but are not the long-term public mod API.
6. **Every milestone is reproducible.** If a successful run depends on a private harness tweak, synthetic GPU value, manually copied DLL, or unrecorded local operation, that behavior is not yet promoted.

## Gate A — M14 native correctness candidate

**Goal:** produce the first Windows package that runs the real game without an emulator fallback.

Required:

- local static recompilation/native module generated from the player's verified R8PE01 copy;
- native MEM1/MEM2 and direct game-to-game calls;
- semantic host services for GameFS, settings, saves, input, timing/events, audio bootstrap, cache boundaries, and GX completion;
- native Aurora-based correctness renderer consuming the original GX stream;
- bounded guest-memory resolution for arrays, display lists, textures, palettes and copy data;
- real renderer-owned GX bounding-box feedback;
- renderer-recognized display-copy/presentation boundaries;
- natural boot -> title -> native PC input -> load/file-select path;
- no forced sequence transitions or fixed 64x64 bbox fallback;
- strict launcher that refuses legacy/emulator fallback;
- clean candidate directory containing only redistributable runtime files before the user supplies a game.

**Status (2026-09-27): met for play.** The game runs from the player's own
copy with no emulator: natural boot, title, file select and gameplay across
maps, about 58 fps in play on the tester's PC, sound effects and the
experimental audio path working. The strict package and the one-step player
workflow are Gate B.

## Gate B — bring-your-own-WBFS player workflow

**Goal:** remove developer setup from the normal player path.

The release launcher should accept a supported WBFS/ISO (and optionally an already extracted game), verify the exact revision, and prepare a private local cache. Preparation should be resumable and deterministic:

```text
source image
-> identity check
-> local extraction
-> local static translation
-> OpenSPM direct-call/host-boundary transforms
-> native module build/cache
-> launch
```

The cache must have an explicit format/version key covering at least game hash, translator revision, OpenSPM ABI, transform version and target architecture. A stale cache should rebuild automatically rather than fail mysteriously.

Release packages should include or otherwise provide all redistributable preparation/runtime dependencies needed by the supported player workflow. Players should not need Visual Studio, CMake, Git, Python, WSL, or a separate emulator installation merely to run a prepared release.

## Gate C — native graphics fidelity and sharp output

**Goal:** make the game look like a high-quality PC port rather than a Wii framebuffer displayed in a PC window.

After the Stage-A GX correctness renderer is stable, introduce a game-aware rendering layer with a compatibility path for effects not yet lifted. Priorities:

- render resolution independent of the original EFB dimensions;
- integer/native-resolution and supersampling options;
- modern texture filtering controls and optional anisotropy;
- anti-aliasing appropriate to the native renderer;
- correct depth precision and transparency behavior;
- texture replacement hooks that can use arbitrary-resolution user assets;
- renderer diagnostics/captures sufficient to compare native output against reference behavior.

Do not fake higher resolution by only upscaling a completed 640x480 frame when the underlying geometry can be rendered at the requested resolution.

## Gate D — true ultrawide

**Goal:** support 16:9, 21:9, 32:9 and follow-window modes without stretching gameplay.

Treat ultrawide as a game-aware camera/UI problem, not a viewport hack. Audit and separately handle:

- perspective and orthographic projection construction;
- camera horizontal field of view / Hor+ behavior;
- world, NPC and effect culling that assumed the original view;
- background/sky/card geometry that was authored only for the original camera bounds;
- HUD anchors and safe areas;
- speech bubbles, nameplates and world-attached UI;
- cutscene staging where revealing off-camera actors exposes original developer shortcuts.

Presentation changes must not expand gameplay detection/AI ranges unless intentionally offered as a gameplay mod.

## Gate E — uncapped presentation with interpolation

**Goal:** preserve the game's original simulation/event cadence while presenting at the monitor's refresh rate or an explicit user cap.

The default model is fixed simulation + independent presentation:

```text
simulation tick N ---- simulation tick N+1 ---- simulation tick N+2
       \ many interpolated presentation frames between completed ticks /
```

Build a versioned render-snapshot service containing only presentation-safe state. Interpolate camera, object transforms, animation parameters, scrolling/UV state and other proven-safe visual state between two completed simulation snapshots. Do not write interpolated values back into gameplay-readable SPM memory.

Interpolation work needs explicit handling for spawn/despawn, teleports, scene transitions, particles, attached effects, 2D UI, scripted cameras and animation discontinuities so objects do not streak between unrelated states.

The presentation loop should support at least: original-rate lock, integer multiples, arbitrary cap, vsync, and uncapped/VRR-friendly modes. Profile CPU/GPU/frame pacing independently; a high average FPS is not sufficient if simulation ticks or presents are irregular.

## Gate F — stable mod SDK

**Goal:** let mods extend the native port without fighting Wii REL slots or emulator internals.

Evolve `include/openspm/mod_api.h` into versioned semantic services. Target surfaces include:

- startup/shutdown and simulation/presentation lifecycle hooks;
- safe queries/mutations for player, map, NPC, battle and story state;
- input actions rather than raw device packets;
- renderer hooks, custom shaders, texture/asset replacements and post-processing;
- namespaced mod save data with migration/versioning;
- filesystem overlays that never mutate the player's extracted source tree;
- symbol/debug access for advanced native mods;
- native extension registries for custom characters, Pixls, items and other extensible content so additions do not have to consume original fixed REL slots.

Mods should declare an ID, version, OpenSPM API range and capabilities. The loader should reject incompatible versions cleanly and isolate save/asset namespaces by mod ID.

## Future audio fidelity contract

The native audio renderer must preserve **SPM's commanded playback-rate semantics**, not infer intended speed from source sample rate alone. A useful reverse-engineering observation from the original game is that soundtrack material is stored at 44.1 kHz, while much of the music is normally played at approximately 0.5x source rate; the Slow Flower effect can reduce that to approximately 0.25x. Treat those ratios as observations to verify against the game's own audio commands before hard-coding them globally.

Consequences for the PC audio path:

- do not automatically stream every 44.1 kHz music asset at nominal 1.0x and call that faithful playback;
- preserve runtime pitch/rate changes as semantic game state so effects such as Slow Flower remain correct;
- separate source decoding/sample rate from commanded playback rate, pitch, resampling and presentation-device sample rate;
- when lifting Wii audio to a custom mixer, validate ordinary tracks and rate-changing effects against reference playback before replacing the compatibility path.

## Gate G — performance, QA and release hardening

Once native correctness exists, optimize from measurements rather than dispatcher counts. Maintain separate telemetry for simulation CPU time, renderer CPU time, GPU time, frame pacing, file I/O and synchronization stalls.

Release confidence should include:

- clean-machine bring-your-own-disc install tests;
- deterministic game identity/cache tests;
- long playthrough/save-load testing;
- renderer regression captures at multiple resolutions/aspect ratios;
- simulation-state comparisons where feasible to prove presentation features do not leak into gameplay;
- controller hotplug/remapping and keyboard coverage;
- mod API compatibility tests;
- no-game-data packaging audit;
- crash diagnostics that never collect or redistribute player game contents by default.

## Status by gate (2026-09-27)

| Gate | State | What is left |
| --- | --- | --- |
| A native correctness | met for play | the scene-dependent game-update time (4.4 to 7 ms); the test list items still waiting for a run (docs/TEST-LIST.md) |
| B player workflow | runs through `.cmd` scripts that need VS Build Tools, git, Python and network (docs/RELEASE-PLAN.md) | ship the toolchain or a prebuilt pipeline so the exe alone prepares the game |
| C graphics fidelity | Aurora correctness renderer; texture packs (Dolphin format) | internal resolution, AA, game-aware rendering |
| D ultrawide | 16:9 opt-in | 21:9 / 32:9, HUD anchors, culling audit |
| E interpolation | Stage C opt-in above 60 Hz, frame mode setting | snapshot coverage (particles, UI, cutscenes), make it default-safe |
| F mod SDK | services: tick, guest memory, guest calls, input, overlay, content (characters, Pixls, items), function hooks v2 (pre/post, integer and float); samples: Shell Crouch, Glock, Koopa Striker, SFX Log | first in-game hook run; characters v2 on hooks; mod manifests and versions; save namespaces; asset overrides |
| G release hardening | local ctest (28 C tests with asserts on) and Python tests; CI blocked (Actions minutes used up) | clean-machine installs, long playthroughs, packaging audit |

## Next milestones (proposed order)

1. **Prove hooks in game.** Enable `spsndSFXOn`, run the SFX Log mod. Then
   one character hook (`marioSetCharacter`) with a no-op pre callback, to
   show a hook on a function the content depends on changes nothing.
2. **Characters v2 on the base slot** (docs/CHARACTER-TABLES.md: a real id
   4+ would cost about 25 table moves and eight function hooks for nothing a
   player sees). Swap more of the base slot's rows while an added character
   plays (8-bit and pipe models, landing sound, the size-scaled tables such
   as the Pixl position), and give abilities through hooks on the behaviour
   functions.
3. **Performance pass.** Find what grows the game-update phase in the slow
   scenes (per-function counts from the profile run), fix the top item.
4. **Player workflow (Gate B).** Pick option (a) or (b) of RELEASE-PLAN.md
   and build the first unzip-and-run package; this is what lets anyone else
   play.
5. **Mod SDK polish (Gate F).** Mod manifest (id, version, API range,
   capabilities), per-mod save data, asset overrides; docs for writing a mod
   from outside the repo.
6. Then Gates C / D / E by player demand.

The longer-term order below still holds; the milestones above are its next
concrete steps.

## Immediate priority order

Do not jump ahead merely because later features are exciting. The shortest route to the final product is:

1. persistent native runtime reaches the already-proven natural title/load path;
2. Aurora correctness renderer builds, launches, presents real frames and returns real bbox data;
3. strict public-code-only Windows candidate layout runs with a player-local generated module;
4. automate the WBFS -> verified local cache -> native-module player workflow;
5. profile and stabilize full-game native execution;
6. lift rendering into game-aware state for sharp output and ultrawide;
7. add render snapshots and uncapped/interpolated presentation;
8. mature the existing mod ABI into the semantic SDK above.

This ordering preserves momentum toward the end state without turning temporary Wii compatibility scaffolding into permanent architecture.
