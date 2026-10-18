@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"
set "TARGET_FPS=%~2"
if not defined TARGET_FPS set "TARGET_FPS=100"

rem dev139 decoupled host-presenter proof:
rem - authoritative Wii simulation remains 60 Hz
rem - retain each completed Wii XFB into a host-owned ping-pong ring
rem - cadence thread schedules retained-XFB presents at TARGET_FPS
rem - Aurora render worker owns all Dawn/WebGPU swapchain work
rem - no geometry interpolation yet; repeated 60 Hz poses are expected
rem - dev138 identity probe is disabled so this measures presenter overhead
set "OPENSPM_M14_DIAG_HALF_99_943HZ=0"
set "OPENSPM_INTERPOLATION=on"
set "OPENSPM_INTERPOLATION_TARGET_FPS=%TARGET_FPS%"
set "OPENSPM_M14_ASYNC_PRESENTER=1"
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

echo [OpenSPM] Launching dev139 decoupled host-presenter proof.
echo [OpenSPM] Authoritative simulation: 60 Hz. Presenter target: %TARGET_FPS% Hz.
echo [OpenSPM] Visual interpolation: OFF. Retained XFB repetition: ON.
echo [OpenSPM] Success means ~60 source publishes/s and ~%TARGET_FPS% host presents/s without producer slowdown.
echo [OpenSPM] Motion may still judder because intermediate poses do not exist yet.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
