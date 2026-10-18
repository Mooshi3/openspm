@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev136 OpenPete-style transform interpolation scaffold:
rem - authoritative Wii simulation restored to 60 Hz
rem - tear-free FIFO/vsync presentation for a clean visual baseline
rem - renderer-visible GX transform history/matching enabled
rem - observation only: no synthetic presentation frame is emitted yet
set "OPENSPM_M14_DIAG_HALF_99_943HZ=0"
set "OPENSPM_M14_INTERP_PROBE=1"
set "OPENSPM_M14_VSYNC=1"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_FINAL_IDLE_SPIN=1"
set "OPENSPM_M14_PRESENT_MODE=fifo"
set "OPENSPM_M14_FIFO_MODE=drain"
set "OPENSPM_M14_STICKY_PRESSED=1"
set "OPENSPM_M14_MAIN_PRIORITY=off"
set "OPENSPM_M14_MAIN_QOS=off"
set "OPENSPM_M14_MAIN_CPUSET=fast"

echo [OpenSPM] Launching dev136 transform-interpolation scaffold.
echo [OpenSPM] Authoritative simulation: 60 Hz. Presentation: FIFO/vsync ON.
echo [OpenSPM] Transform history/matching: ON. Synthetic frames: not yet emitted.
echo [OpenSPM] Test normal gameplay/camera motion; pairing stats are written to launch.log.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
