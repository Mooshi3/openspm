@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"
set "TARGET_FPS=%~2"
if not defined TARGET_FPS set "TARGET_FPS=100"

rem dev140b offscreen interpolation replay-encode proof:
rem - authoritative Wii simulation remains 60 Hz
rem - dev139 remains the visible TARGET_FPS presenter
rem - dev140a prepares adjacent-frame display-rational transform-uniform arenas
rem - dev140b replays those poses into isolated color/depth targets
rem - replay targets are NOT published to the swapchain yet
rem - this tests replay correctness/perf without risking visible corruption
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

echo [OpenSPM] Launching dev140b offscreen interpolation replay-encode proof.
echo [OpenSPM] Authoritative simulation: 60 Hz. Visible presenter target: %TARGET_FPS% Hz.
echo [OpenSPM] Interpolated GX poses are rendered offscreen but NOT presented.
echo [OpenSPM] Expected log: M14 dev140b replay encode frame=... encoded=... rejected=0.
echo [OpenSPM] Success means encoded phases with no crash/corruption and tolerable producer cost.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
