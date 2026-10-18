# M12 Windows test candidate

This bundle adds a render-submission ultrawide override and a native string
comparison specialization to the exact green M11D2 frontend. It does not yet
add rendering above 60 FPS or replace the whole compatibility runtime.

## Launch and saves

Keep the old bundle intact. Extract this bundle into a new folder. To keep your
progress/settings, copy the old bundle's user directory into the new bundle as
`User-OpenSPM-M11D2`. Do not overwrite your only save copy. Alternatively set
`OPENSPM_USER_DIR` to your chosen test copy before launching.

Run `RUN-OPENSPM.cmd`. It recognizes the previously supplied game path:
`D:\Desktop\spm-m10-windows-playable-runtime\spmExtracted`.
You can also pass any complete extraction folder as the first argument.

## First acceptance check: non-stretched ultrawide

1. Load a save and stand still in the same scene/camera view.
2. Select OpenSPM > Aspect Ratio > 16:9, then 21:9.
3. At equal viewport height, Mario and world objects should keep their shape
   and vertical size while additional world becomes visible at the sides.
4. Test the 2D/3D flip, HUD, dialogue and one room transition. Take a screenshot
   at both ratios. Note disappearing edge objects, clipped backgrounds,
   full-screen effects, or misaligned UI. Those require follow-up coverage.
5. Close the game normally. Keep `M12-NATIVE-1.log`. Expect
   `[OPENSPM-DISPLAY] submit ...` for adjusted cameras and final fallback counters.

The source camera matrices are not changed. Some game-side culling may still
use the original view, and non-camera projections deliberately remain stock.
The runtime currently uses exact 21/9; automatic 3440/1440 output matching is
not implemented in this batch.

## Second acceptance check: frame rate

Compare `RUN-OPENSPM.cmd` against `RUN-M12-BASELINE.cmd` in the same demanding
scene for about 20–30 seconds. The latter disables the new string helper;
both use the same DLL, display setting, runner, idle optimization and user data.
Logs are separate: `M12-NATIVE-1.log` and `M12-NATIVE-0.log`.
Warm shaders and use the same resolution/aspect ratio for both runs.

Record steady FPS and hitches at Flipside Tower or crowded Bowser interior.
The isolated CPU microbenchmark is not a prediction of game FPS. Verify the
title > file select/load > Mario path still works and retain shutdown counters.
`fallback=0` is the interpreter-step counter; it does not imply that all
fallback-JIT execution has been eliminated.

## Build gates

The workflow first runs MSVC differential tests against the actual original
generated routines. It then builds the 218-chunk DLL through sccache, probes
ABI3/3528 bytes, verifies native exports and packages SHA256SUMS, symbols,
logs and provenance. The runner comes byte-for-byte from run 34156272495.
