@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"
set "TARGET_FPS=%~2"
if not defined TARGET_FPS set "TARGET_FPS=100"

rem dev140a interpolation replay-prep proof:
rem - authoritative Wii simulation remains 60 Hz
rem - dev139 still owns visible host presentation at TARGET_FPS
rem - prepare display-rational interpolation phases from adjacent GX draws
rem - clone existing uniform bytes and patch transforms only
rem - retain the recorded FramePacket through the render-worker callback
rem - visual replay is intentionally OFF in this milestone
set "OPENSPM_M14_DIAG_HALF_99_943HZ=0"
set "OPENSPM_INTERPOLATION=on"
set "OPENSPM_INTERPOLATION_TARGET_FPS=%TARGET_FPS%"
set "OPENSPM_M14_ASYNC_PRESENTER=1"
set "OPENSPM_M14_INTERP_REPLAY=1"
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

echo [OpenSPM] Launching dev140a interpolation replay-prep proof.
echo [OpenSPM] Authoritative simulation: 60 Hz. Presenter target: %TARGET_FPS% Hz.
echo [OpenSPM] Visual replay: OFF. Visible output remains dev139 retained-XFB presentation.
echo [OpenSPM] Phase cadence follows the precise display ratio when near target; 100.000 Hz reduces to 2,2,1.
echo [OpenSPM] Success means high matched-uniform coverage with no crash or producer regression.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
