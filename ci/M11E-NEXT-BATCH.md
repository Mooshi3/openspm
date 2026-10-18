# M11E next module-rebuild batch (staged research only)

Do not trigger a game-DLL rebuild until M11D runtime measurements are in.

## Confirmed observations

- M11C improved heavy scenes substantially (Bowser interior roughly 20 -> 29 FPS), but scene-complexity scaling remains.
- M11C aspect requests reach the module (`[OPENSPM-ASPECT]`) but no `[OPENSPM-PROJ]` line ever appears. The current camera-entry hook is therefore not producing a live projection adjustment.
- The pinned DolRecomp/RecompCore pair already contains the upstream inline FP gate and type-0 paired-single helpers. Do not rebuild merely to port RecompCore PR #9 / DolRecomp PR #12; those mechanisms are already present.
- Unsafe C cross-chunk direct calls remain disabled because they bypass chassis dispatch-time SMC validation.
- Current CI compiles pre-generated portable C chunks; it does not possess the user's retail DOL, so newer DolRecomp emitter changes do not affect this DLL unless a future BYO-game regeneration path is used.

## Runner-minute strategy for the next DLL build

The M11C workflow put the ModernGekko frontend behind sccache but configured the 218-chunk SPM module without `CMAKE_C_COMPILER_LAUNCHER=sccache` / `CMAKE_CXX_COMPILER_LAUNCHER=sccache`. Fix that before the next module build.

Expected shape:
- use the existing sccache action/cache for both frontend and module compilation;
- pass the compiler-launcher settings to `m10/build-module` too;
- keep generated source paths stable so unchanged chunk objects can hit cache across attempts;
- patch only the minimum generated chunk(s) plus module helper source;
- keep preflight signature/anchor checks before any compile;
- package provenance with the exact patched chunk list.

This should make follow-up fixes to one or two chunks much cheaper than recompiling all 218 every time.

## Candidate 1: robust true-Hor+ projection hook

Prefer hooking the guest `C_MTXPerspective` entry (R8PE01 mapping previously identified near 0x80278E10) rather than depending on a fragile `CamEntry*` register at one `camDraw` call site.

Proposed behavior:
- inspect the perspective aspect argument at function entry;
- only rewrite values close to stock 4:3 or 16:9 ratios;
- set them to the selected OpenSPM target ratio (16:9 / 21:9 / 32:9), preserving vertical FOV;
- leave unusual perspective matrices untouched;
- log the first adjusted call per selected mode;
- leave orthographic/UI projection unchanged in the first experiment, then handle eligible world-ortho cameras separately once 3D Hor+ is proven.

Before patching, validate the exact generated chunk/label from the portable R8PE01 source rather than trusting an external symbol map.

## Candidate 2: hot guest helper replacement

M11B dispatch sampling put `strcmp` at an extremely hot guest PC. Only consider a native replacement after M11D measurements. A replacement must preserve observable PPC return/register/CR behavior, MMU faults, and reasonable cycle charging; do not substitute an unsafe host pointer/string call.

## Candidate 3: newer LLVM/native-ABI backend

Upstream DolRecomp has a much newer LLVM/native-ABI path after our pin. It is a large backend rewrite, not a drop-in optimization. Treat it as a separate experimental arm with its own module build, not part of the next low-risk production batch.

## Next decision gate

1. Measure M11D modes from the same heavy-scene savestate.
2. If idle skipping closes most of the gap, prioritize ultrawide + QoL.
3. If heavy scenes remain far below 60, use M11D counters/JIT control to decide between more runtime-contract work and generated-code optimization.
