@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"
set "TARGET_FPS=%~2"
if not defined TARGET_FPS set "TARGET_FPS=100"

rem Visible translation interpolation. Optional third argument: identity.
set "OPENSPM_M14_INTERP_PRESENT=1"
set "OPENSPM_M14_INTERP_IDENTITY=0"
if /i "%~3"=="identity" set "OPENSPM_M14_INTERP_IDENTITY=1"
if %TARGET_FPS% LSS 61 exit /b 2
if %TARGET_FPS% GTR 120 (
  echo [OpenSPM] This first visible replay path supports targets from 61 to 120 Hz.
  exit /b 2
)
set "OPENSPM_M14_DIAG_HALF_99_943HZ=0"
set "OPENSPM_INTERPOLATION=on"
set "OPENSPM_INTERPOLATION_TARGET_FPS=%TARGET_FPS%"
set "OPENSPM_M14_ASYNC_PRESENTER=1"
set "OPENSPM_M14_INTERP_REPLAY=1"
set "OPENSPM_M14_INTERP_REPLAY_ENCODE=1"
set "OPENSPM_M14_INTERP_PROBE=0"
set "OPENSPM_M14_INTERP_VISUAL=off"
set "OPENSPM_M14_VSYNC=1"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_FINAL_IDLE_SPIN=1"
set "OPENSPM_M14_PRESENT_MODE=fifo"
set "OPENSPM_M14_FIFO_MODE=drain"
set "OPENSPM_M14_STICKY_PRESSED=1"
set "OPENSPM_M14_MAIN_PRIORITY=off"
set "OPENSPM_M14_MAIN_QOS=off"
set "OPENSPM_M14_MAIN_CPUSET=fast"

echo [OpenSPM] Launching world-camera replay interpolation at %TARGET_FPS% Hz.
echo [OpenSPM] Game simulation stays authoritative; actor/object transforms are not interpolated in normal mode.
echo [OpenSPM] Extra frames replay the scene with one shared world-camera offset; HUD/screen-space draws stay fixed.
echo [OpenSPM] Identity replay control: %OPENSPM_M14_INTERP_IDENTITY% (diagnostic legacy replay path).
echo [OpenSPM] Check dev145 world-camera replay/selected-frame counters and actual camera motion.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
