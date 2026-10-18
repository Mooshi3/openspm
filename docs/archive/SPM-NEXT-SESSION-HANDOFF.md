# Super Paper Mario PC Recomp — Next Session Handoff

**Date:** 2026-09-05/06
**Repo:** `Mooshi3/spm-recomp` (private)
**Working branch:** `m10-windows-runtime`
**Immediate goal:** **vanilla playable** — native app → title/file select/load → controllable Mario, with zero interpreter fallback.

## 2026-09-06 zero-frame diagnosis and fix

The zero-frame exit was reproduced with the exact pinned ModernGekko/RecompCore source and the clean R8PE01 extraction independently of static recompilation. The process terminated from the optional real-Wiimote HIDAPI scanner:

```text
Couldn't initialise hidapi.
Condition: ret == 0
Source/Core/Core/HW/WiimoteReal/IOhidapi.cpp:97
```

The no-GUI assertion path made this a hard process exit. Because Wiimote scanning starts asynchronously, OpenGL could report successful initialization first. This explains the identical native-module and interpreter-only symptom.

Fix: `ci/recompcore-hidapi-nonfatal.patch` records HIDAPI initialization state, logs failure, disables only real-Wiimote HID scanning, and avoids calling `hid_exit` or enumeration when initialization failed. Emulated Wii Remote input remains enabled.

Exact pinned-source Linux validation using the persistent `ModernGekko/build` directory:

- before patch: exit 132 / illegal instruction, frame 0;
- after patch, interpreter control: `frame_count=212`, `present_count=496`, state running;
- after patch, native M09 DOL+REL module: `frame_count=151`, `present_count=365`, `fallback=0`, `native=134362379`.
- branch workflows now apply and package the patch.
- the diagnostic breadcrumb script also includes its required `<iostream>` header.

Remaining acceptance work is Windows execution of the rebuilt bundle and the manual title → file select/load → controllable Mario path with final zero-fallback shutdown counters. Connector-authored commits do not trigger Actions, so launch either M10 Windows workflow manually at the current branch head.

## Where we are right now

We have crossed from the architecture/build phase into the first real Windows playable-bundle build.

Current GitHub Actions workflow:
- `.github/workflows/build-m10-windows.yml`
- Run ID: `34008103869`
- Job ID: `101418915444`

At handoff time:
- checkout ✅
- M10 portable source payload unpack ✅
- Windows build environment ✅
- MSVC environment ✅
- pinned upstream sources fetched ✅
- ModernGekko CPU ABI3 compatibility applied ✅
- corrected portable REL discovery patch parsed/dry-run/applied ✅
- ModernGekko configured ✅
- **Windows frontend (`moderngekko-run.exe`) built successfully ✅**
- portable SPM module configured ✅
- **all 218 SPM chunks are currently compiling into `gR8PE01_recomp.dll` 🔄**
- descriptor probe/package/artifact upload pending

The next session should FIRST check whether run `34008103869` finished and, if successful, download artifact:

`spm-m10-windows-playable-runtime`

Then move directly into launch/testing/debugging. Do not restart architecture work unless runtime evidence requires it.

---

## Product north star

Build the **OpenPete of Super Paper Mario**: a native, high-performance, cross-platform, mod-friendly PC port that preserves vanilla behavior while lifting Wii-era technical limitations.

Near-term success is not “the recomp builds.” It is:

> **Open the Windows build, load the supported R8PE01 game, reach gameplay, and move Mario.**

After that, polish toward:
- Windows/Linux/macOS
- clean first-run disc selection/verification
- native input/controller support
- window/fullscreen UX
- Hor+ widescreen/ultrawide
- higher-resolution rendering and later presentation-rate interpolation
- asset overrides
- developer/debug tooling
- extensible mod API

---

## Critical architecture decisions — do not rediscover these

### 1. DOL + REL architecture is already proven

**M09 is the golden proof and must not be regressed.**

Canonical checkpoint:
- `SPM-native-rel-milestone-09.zip`

M09 proved a clean natural boot from extracted R8PE01 where RecompCore discovered the real linked REL and execution entered native recompiled REL code:

```text
[lazyrel] armed REL discovery at pc=802e0580 after 121819136 dispatches
[lazyrel] FIRST REL NATIVE DISPATCH pc=80c4205c
[lazyrel] disabled further REL discovery scans
```

`0x80C4205C` is the real mapped `relF` prolog.

Trusted M09 shutdown counters:
- native dispatches: `174,230,429`
- fallback steps: `0`
- native exceptions: `15,921`
- hook fallback: `3,760,253`
- SMC failed: `0`
- verifications: `116`
- reverify: `7`
- cycles: `3,709,461,222`

Therefore: **never go back to “how do we combine the DOL and REL?”** That problem is closed.

### 2. Unified module

Current portable module is one dispatcher covering:
- 193 DOL chunks
- 25 REL chunks
- **218 chunks total**
- 3 code ranges
- 27 SMC ranges
- 1 REL metadata record
- module ABI3
- CPU ABI3
- CPUState size `3528`
- entry `0x80006124`

Local structural proof already passed before Windows CI:

> ABI3 / CPU ABI3 / size 3528 / 3 code ranges / 27 SMC ranges / 218 chunks / 1 REL = PASS

### 3. REL discovery issue and fix

The original pinned RecompCore `RefreshRelSections()` scanned essentially all of MEM1 in 4-byte increments whenever REL metadata was active. Advertising relF too early caused catastrophic repeated scanning.

M09 proved the runtime architecture using a Linux lazy wrapper + diagnostic runner patch.

M10 replaces that proof scaffold with a **portable source-level RecompCore fix**:
- only attempt REL discovery when the requested PC falls in a statically known linked REL executable range;
- search only the plausible file-sized allocation window preceding that address;
- accept both raw `sectionInfoOffset` and OSLink-mutated absolute `sectionInfoOffset`;
- do not repeatedly sweep all of MEM1.

Important Wii behavior:
- SPM OSLink mutates REL header `sectionInfoOffset` from raw `0x4c` to absolute `0x80C03F8C` in the known M09 mapping.

The first M10 patch ZIP had malformed unified-diff hunk counts and failed CI with:

`error: corrupt patch ... line 67`

That was fixed. The corrected branch patch is:
- `ci/recompcore-portable-rel-discovery.patch`

The workflow now explicitly validates it before the expensive build.

### 4. ModernGekko is bootstrap infrastructure, not sacred

ModernGekko/Dolphin-derived runtime is the chassis we are using to get vanilla running. If its renderer/input/runtime architecture later blocks OpenPete-level performance, portability, presentation, or modding, replace subsystems deliberately. Do not block first playability on a custom renderer.

---

## Pinned identities / hashes

Upstream commits:
- ModernGekko: `5417826c31187d4dadf8588c7aa25bf107782936`
- RecompCore/Dolphin submodule: `55c7b023fa0f4eba1cf3fdbbb25b1c5ec468d5ac`
- DolRecomp: `1bec3554ecc4817cf78319ca3d8a0669477f29fa`

Game hashes:
- WBFS SHA256: `b403a6835824b459410586d40f0ad0fb2de496ad953bb417ddcb99f4b920c1e7`
- `main.dol` SHA256: `316fe3a88654cf55f164a493fdc7d872fb7362298613e335a2240b4921bfe7a0`
- `relF.rel` SHA256: `5b4fc7074476675d9e3c41ae3fa31b2fe987920a014efde74a1cffc551c5da97`

Disc:
- R8PE01
- outer revision byte 2
- inner boot revision 0
- Seeky US2 mapping is the working symbol mapping; it is not itself clean-disc certification.

Useful module/runtime hashes from earlier milestones:
- hybrid module: `904b467d47b740b32d4e15525d39dc69ad92868062fc2ddd4cb0632b139f480f`
- M09 lazy wrapper: `e936bf33edf1bed810861953a6dd6999e69b02ca85b718a6f1854f25b5a12949`
- M09 relfast runner: `a361973fe6b8df1821f53762d5319c61546cd6d05598ae24fd1bea325f5cd2a7`

---

## Current M10 source payload

Portable payload is in GitHub at:
- `ci/payload/spm-m10-portable-source.zip`

It contains the generated 218-chunk source and M10 scaffolding.

Local canonical working tree from the prior session was:
- `/mnt/data/spm_m10_work/SPM-portable-native-milestone-10/`

Important contents:
- `module/CMakeLists.txt`
- `module/generated/chunks/` — all 218 chunks
- `module/generated/generated.h`
- `module/include/StaticRecompABI.h`
- `module/src/module_tables.inc`
- `module/src/spm_module.c`
- `scripts/prepare_dolrecomp_cpu.py`
- `tests/module_descriptor_probe.c`
- `tests/test_rel_discovery_model.py`
- `evidence/module-layout.json`
- `provenance/upstream-lock.json`

The final M10 checkpoint should eventually be packaged as:
- `SPM-portable-native-milestone-10.zip`
- include `RESUME-10.md`, evidence, logs, hashes, provenance, Windows CI result/artifact metadata.

Preserve M09 untouched as golden runtime proof.

---

## First testing/debugging plan after Windows artifact succeeds

### Phase A — verify the bundle

Artifact should contain at least:
- `moderngekko-run.exe`
- `gR8PE01_recomp.dll`
- `Sys/`
- provenance/logs/hashes

Verify the descriptor probe passed in CI before treating DLL as valid.

### Phase B — launch with the known extracted R8PE01 game

Use the same extracted game content that worked in M05–M09.

Capture:
- stdout/stderr/logs
- window/title state
- screenshots at several startup points
- whether frames/presents advance
- native/fallback counters
- first REL-native-dispatch evidence if available
- input/navigation state

### Phase C — drive to controllable Mario

Debug only the blocker actually observed. Likely categories:
- launch path / game-data path assumptions
- renderer/window presentation
- controller/keyboard mapping
- file-select/title input
- Wii remote emulation assumptions
- timing/audio stalls
- REL mapping regressions

Do **not** infer a black screen from frame counters alone. M08/M09 taught us that renderer/present counters are not trustworthy proof of visible game state; capture actual pixels/screenshots.

The milestone closes only when:

> title/file select/load → Mario visibly in gameplay → user input moves Mario.

Keep zero interpreter fallback as a hard correctness target.

---

## Long-term vanilla/mod architecture decision

Do not fork into two engines.

Use one base game with:

```text
Original SPM behavior
      |
Compatibility API
      |
Vanilla registry/data (always present)
      |
Optional mod overlays
```

Profiles eventually:
- Vanilla — strict original behavior/no gameplay mods
- Vanilla + Enhancements — ultrawide/HD/controller/accessibility/QoL without changing game logic
- Modded — full extension API/content
- Custom profiles — saved mod stacks/load order

Strict vanilla acts as a regression oracle.

For eventual extensibility, do not bake vanilla fixed limits into extension APIs. Goal is enough flexibility to add 100 Pixls or a fan sequel without replacing vanilla slots.

Future extension-facing systems should use expandable/namespaced IDs and registries for Pixls, characters, items, enemies, maps, music, etc., with compatibility translation only where original structures demand it.

This is Phase 2. **Do not derail vanilla playability to implement it now.**

---

## Public-release legal constraints

Private prototypes may use rough/game-derived artifacts for development. **Public release has a hard clean-room-ish packaging boundary.**

Public distribution should contain:
- our launcher/runtime/tooling
- independently authored compatibility/render/input/mod code
- metadata/hashes/config needed to recognize supported SPM copies

Public distribution should NOT contain:
- SPM WBFS/ISO
- `main.dol` / RELs
- extracted Nintendo textures/audio/maps/dialogue/fonts/models/logos
- Nintendo asset packs or download links
- ideally no prebuilt mechanically translated Nintendo game code unless actual counsel signs off

Preferred final UX/release model:

```text
Launch SPM-PC
→ Select legally obtained R8PE01 disc image
→ Verify supported revision
→ Locally extract/cache required game data
→ Locally generate any game-derived native code if needed
→ Play
```

Public downloader without an original SPM copy should effectively have only a useless engine/launcher.

Use unofficial branding and a clear non-affiliation disclaimer. Before public release, audit the exact public tree/package for accidental Nintendo content and have a software/copyright lawyer review the downloadable release package if practical.

These legal-release constraints should not slow private vanilla bring-up, but preserve provenance/separation so cleanup is straightforward later.

---

## UX target for eventual public build

The end user should not need Dolphin, command-line setup, extracted RELs, or manual build scripts.

Ideal first run:
1. open `SuperPaperMarioPC.exe`
2. choose original SPM WBFS/ISO
3. verify/extract/cache locally
4. play

Subsequent launches:
1. open SPM-PC
2. play

Current M10 may still require developer-style extracted R8PE01 input. That is temporary scaffolding, not final UX.

---

## Recommended next-session opening prompt

> Resume the SPM PC recomp from `SPM-NEXT-SESSION-HANDOFF.md` in `Mooshi3/spm-recomp`, branch `m10-windows-runtime`. First inspect GitHub Actions run `34008103869`. If the `spm-m10-windows-playable-runtime` artifact succeeded, download it and immediately begin Windows launch/visual/input testing with the known R8PE01 game. The goal is title/file-select/load → controllable Mario with zero interpreter fallback. Do not redo the DOL+REL architecture; M09 already proved natural native REL execution.

## 2026-09-06 fixed-bundle checkpoint

GitHub Actions fixed-runtime run `34013690308` (job `101433736071`) passed every build, patch, model-test, descriptor-probe, packaging, and upload step.

Exact current artifact `9983674258`:
- ZIP size: `34,347,610` bytes
- ZIP SHA-256: `f4378a7defad9da39fc85403da24eabfb56f8ecaaae9153a0e7fabf509b7e7a7`
- `moderngekko-run.exe`: `bb133292e523cf660c12ed4f936ad93e4d5bbd38b0fc192df573be01bf595406`
- `gR8PE01_recomp.dll`: `d06abe2d1299f3ac9de140c9a430a12370f2964e77bccc847c57623e9cd917fd`

The artifact was downloaded, its ZIP digest matched GitHub, and its packaged `SHA256SUMS.txt` passed. It has also replaced the older same-named persistent bundle checkpoint.

The exact source tree was reconstructed at ModernGekko `5417826c` / RecompCore `55c7b023`, both branch patches plus ABI3 were applied, and the persistent incremental Linux build completed. Fresh local controls:
- interpreter: `frame_count=338`, `present_count=748` after 8 seconds, state running;
- native archived M09 lazy-REL module: `frame_count=549`, `present_count=1164`;
- clean shutdown: `native=194501302 fallback=0 native_exc=39901 hook_fb=10417754 smc_failed=0 verifications=116 reverify_events=7`;
- natural REL activation reconfirmed at `pc=80c4205c`.

Directly advertising the inner hybrid module from process start remains pathologically slow before frame 1 because it exposes REL metadata too early; the archived lazy-REL wrapper avoids that and is the correct Linux control. This does not change the packaged M10 Windows DLL or reopen the green architecture.

The remaining acceptance boundary is a native Windows visual/input run. This execution environment rejects the AF_UNIX socket support Wine requires and has no physical Windows session, so it cannot honestly prove title → file select/load → controllable Mario. Use the packaged `RUN-M10-WINDOWS-TEST.cmd` with an extracted clean R8PE01 root and retain `M10-WINDOWS-RUNTIME.log`; the final log must show zero fallback.

