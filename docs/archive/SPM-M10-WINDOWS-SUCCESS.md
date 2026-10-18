# SPM M10 Windows Success — Testing Handoff

**Status:** M10 Windows portable build completed successfully. The project is now entering the **testing/debugging phase**.

## Immediate goal

**Vanilla playable:** native Windows app → title → file select/load → visibly controllable Mario, with zero interpreter fallback.

Do not restart DOL/REL architecture work unless new runtime evidence proves a regression. M09 already established natural native REL execution.

## Successful Windows CI

Workflow: `.github/workflows/build-m10-windows.yml`

Run ID: `34008103869`
Job ID: `101418915444`
Branch: `m10-windows-runtime`
Build commit: `462f6e827f260fa98dd881754c7e3bb145bc3c05`

Every substantive step passed:
- payload unpack
- Windows/MSVC setup
- exact upstream fetch
- ABI3 compatibility
- portable REL patch validation/application
- ModernGekko configure
- `moderngekko-run.exe` build
- portable SPM module configure
- all 218 SPM chunks → `gR8PE01_recomp.dll`
- native module descriptor probe
- package
- artifact upload

Artifact:
- name: `spm-m10-windows-playable-runtime`
- artifact ID: `9982087423`
- size: `34,346,094` bytes
- artifact digest: `sha256:2b6fea4b10a4f769205ab82d20d93b247b38751d1fb26553f649d458415b6361`
- expires: 2026-10-06

## Zero-frame fix rebuild (current test candidate)

The shared native/interpreter frame-0 exit was traced to the optional real-Wiimote HIDAPI scanner asserting when `hid_init()` failed after OpenGL initialization. The runtime now treats that failure as nonfatal: it logs the condition and disables only real Wii Remote scanning. Emulated Wii Remote input remains enabled.

Fixed workflow run: `34013690308`
Job: `101433736071`
Build commit: `619b1217c0dffecd6d8ccb02812deda5691a46d1`

Every workflow step passed, including application of both RecompCore patches, the REL model test, the 218-chunk DLL build, descriptor probe, packaging, and upload.

Current artifact:
- name: `spm-m10-windows-playable-runtime`
- artifact ID: `9983674258`
- size: `34,347,610` bytes
- ZIP digest: `sha256:f4378a7defad9da39fc85403da24eabfb56f8ecaaae9153a0e7fabf509b7e7a7`
- `moderngekko-run.exe`: `bb133292e523cf660c12ed4f936ad93e4d5bbd38b0fc192df573be01bf595406`
- `gR8PE01_recomp.dll`: `d06abe2d1299f3ac9de140c9a430a12370f2964e77bccc847c57623e9cd917fd`
- expires: 2026-10-06

The downloaded ZIP was independently hashed against the GitHub artifact digest and its packaged `SHA256SUMS.txt` passed for both binaries. Provenance records `hidapi_init_failure=nonfatal`; descriptor remains `abi=3 cpu=3 size=3528 game=R8PE01 entry=80006124 code=3 smc=27 chunks=218 rel=1 PASS`.

Windows visual/input acceptance is still pending; this section proves the exact fixed test candidate, not the title-to-gameplay milestone.

## Artifact contents verified

Important files:
- `moderngekko-run.exe`
- `gR8PE01_recomp.dll`
- `Sys/`
- `m10-module-probe.log`
- `m10-rel-model.log`
- `BUILD-PROVENANCE.txt`
- `SHA256SUMS.txt`
- `recompcore-portable-rel-discovery.patch`

Binary hashes from CI:
- `moderngekko-run.exe`: `8ed616b2365948cd04ef8014197569699133bee083c0d7f39f6ad015a8eb1815`
- `gR8PE01_recomp.dll`: `3f991c447edcfa8c00620bc00d490baaf13ac06ce977d0c52b9c326b99ae535b`

Descriptor probe output:

```text
abi=3 cpu=3 size=3528 game=R8PE01 entry=80006124 code=3 smc=27 chunks=218 rel=1 PASS
```

Portable REL behavioral model output:

```text
PASS: targeted REL discovery; scans=1; candidates=751468; raw+OSLink sectionInfo accepted; allocation-independent runtime hint
```

Pinned provenance:
- ModernGekko: `5417826c31187d4dadf8588c7aa25bf107782936`
- RecompCore: `55c7b023fa0f4eba1cf3fdbbb25b1c5ec468d5ac`
- DolRecomp: `1bec3554ecc4817cf78319ca3d8a0669477f29fa`
- module ABI: 3
- CPU ABI: 3
- CPUState: 3528 bytes
- chunks: 218
- REL modules: 1
- game: R8PE01

## Golden prior proof — M09

Canonical checkpoint: `SPM-native-rel-milestone-09.zip`.

M09 already proved clean natural boot from extracted R8PE01, discovery of the real linked relF, and entry into native REL recompilation:

```text
[lazyrel] armed REL discovery at pc=802e0580 after 121819136 dispatches
[lazyrel] FIRST REL NATIVE DISPATCH pc=80c4205c
[lazyrel] disabled further REL discovery scans
```

Trusted M09 shutdown:
- native dispatches: 174,230,429
- fallback steps: 0
- native exceptions: 15,921
- hook fallback: 3,760,253
- SMC failed: 0
- verifications: 116
- reverify: 7
- cycles: 3,709,461,222

Do **not** rediscover how to combine DOL + REL. That architecture problem is closed.

## M10 portable REL fix

Pinned RecompCore previously swept almost all MEM1 in 4-byte increments during REL discovery, causing the severe apparent stall when REL metadata was advertised before relF existed.

M10 replaces the M09 Linux proof scaffolding with a portable source-level strategy:
- trigger discovery only for PCs relevant to known linked REL executable ranges;
- search only the plausible file-sized allocation window;
- accept raw and OSLink-mutated absolute `sectionInfoOffset`;
- support runtime allocation differing from the static linked address;
- avoid repeated whole-MEM1 sweeps.

Correct branch patch: `ci/recompcore-portable-rel-discovery.patch`.

## NEXT ACTION: test the Windows bundle

The next session should download fixed artifact `9983674258` and **immediately test the actual bundle**, using the known extracted R8PE01 data that worked in prior milestones.

Capture actual evidence, not inferred state:
- stdout/stderr/runtime logs
- screenshots/pixels at several startup points
- window title/state
- frame and present progression
- native/fallback counters
- REL native dispatch evidence
- input/navigation response

Do not treat frame/present counters alone as proof that visible rendering works.

Debug only the blocker actually observed. Likely categories:
- game-data path/launcher assumptions
- Windows renderer/presentation
- input/controller/Wiimote mapping
- title/file-select navigation
- timing/audio
- REL mapping regression

The next milestone is complete only when **Mario is visible in gameplay and responds to user input**.

## Product direction after vanilla works

North star: the **OpenPete of Super Paper Mario** — native, fast, cross-platform, faithful vanilla base, then extensive enhancements/modding.

Do not fork vanilla and modded into separate engines. Vanilla is the strict zero-mod compatibility profile; mods overlay the same base through expandable registries/APIs.

Long-term extension goal must support adding arbitrary Pixls/maps/content (even fan-sequel scale), not merely replacing fixed vanilla slots.

## Public release legal boundary

Private prototypes may contain rough/game-derived development artifacts. Public release must be cleaned separately.

Public distribution should contain our runtime/tooling/compatibility code and should not contain Nintendo disc images, extracted assets, DOL/REL files, or other Nintendo content. Preferred public UX is user supplies their own supported R8PE01 disc image; launcher verifies and locally derives/caches required game material. Ideally do not distribute mechanically translated Nintendo game code either unless counsel specifically approves that model.

Do not let public-release packaging constraints slow the private vanilla-playability push, but keep provenance/separation intact.

## Next-session prompt

> Resume Super Paper Mario PC from `SPM-M10-WINDOWS-SUCCESS.md` and `SPM-NEXT-SESSION-HANDOFF.md` on branch `m10-windows-runtime`. M10 Windows fixed-runtime CI run `34013690308` is fully green and artifact `9983674258` (`spm-m10-windows-playable-runtime`) contains the HIDAPI fix, successfully probed Windows frontend, and 218-chunk R8PE01 DLL. Download the artifact and begin actual Windows launch/visual/input testing. Goal: title/file-select/load → controllable Mario with zero interpreter fallback. Do not redo the DOL+REL architecture; M09 already proved natural native REL execution.
