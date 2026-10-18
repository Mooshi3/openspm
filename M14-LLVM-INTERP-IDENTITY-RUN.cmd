@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev138 geometry-identity interpolation probe:
rem - authoritative Wii simulation remains 60 Hz
rem - FIFO/vsync keeps presentation tear-free
rem - no visual interpolation or synthetic presents
rem - hash final staged geometry + material identity for replay correspondence
rem - target 0 continues to mean active display for the future presenter
set "OPENSPM_M14_DIAG_HALF_99_943HZ=0"
set "OPENSPM_INTERPOLATION=on"
set "OPENSPM_INTERPOLATION_TARGET_FPS=0"
set "OPENSPM_M14_INTERP_PROBE=1"
set "OPENSPM_M14_INTERP_PROBE_INTERVAL=300"
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

echo [OpenSPM] Launching dev138 geometry-identity interpolation probe.
echo [OpenSPM] Authoritative simulation/output: 60 Hz. Presentation: FIFO/vsync ON.
echo [OpenSPM] Visual interpolation: OFF. Synthetic presents: OFF.
echo [OpenSPM] Probe: sampled geometry/material identity + ambiguity rejection every 300 display copies.
echo [OpenSPM] Ordinary frames do no identity hashing/matching.
echo [OpenSPM] Run the same rooms/flips/motion that exposed dev137.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
