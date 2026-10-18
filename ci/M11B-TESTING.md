# OpenSPM M11B test pass

Use the complete US R8PE01 extraction that already boots the known-good M10 runtime.

## 1. Normal run

Launch `RUN-OPENSPM.cmd`.

Check these before doing the profiled run:

- Title still boots normally.
- `OpenSPM -> Aspect Ratio` can switch between Auto / 4:3 / 16:9 / 21:9 / 32:9.
- At 21:9, 3D perspective should be Hor+ rather than horizontally stretched.
- `OpenSPM -> Fast Forward (Toggle)` disables the runtime speed limiter until toggled off.
- The existing rebindable hold-fast-forward input remains available through `OpenSPM -> Configure Runtime Hotkeys...`.
- `OpenSPM -> Configure Game Controls...` opens the current controller binding backend.

Record rough observed FPS at the same four opening checkpoints used for the M10 baseline:

1. Title screen (M10 approximately 50 FPS)
2. Bowser castle exterior (M10 approximately 52 FPS)
3. Mario/Luigi house (M10 approximately 35 FPS)
4. Bowser castle interior (M10 approximately 20 FPS)

Control measurement on the same PC: regular SPM in regular Dolphin holds approximately 59.95 FPS.

## 2. Profiled run

Launch `RUN-OPENSPM-PERF.cmd` and let the opening pass through the same four checkpoints. Close OpenSPM after reaching the Bowser castle interior, then return `OpenSPM-PERF.log`.

The log emits `[OPENSPM-PERF]` samples approximately once per second with:

- native dispatches
- native bursts
- dispatches per burst
- fallback JIT runs
- interpreter fallback steps
- host calls
- instruction-hook fallbacks
- REL whole-RAM rediscovery count
- runtime-to-linked REL resolutions
- linked-to-runtime REL resolutions
- REL section checks

The key comparison is how these rates change as FPS falls from the simple scenes to the crowded Bowser interior.

## Regression rule

If M11B fails to boot or behaves materially worse than M11A, keep M11A as the working baseline and diagnose M11B on its isolated `m11b-perf-qol` branch. Do not modify the known-good M10 branch to repair M11B.
