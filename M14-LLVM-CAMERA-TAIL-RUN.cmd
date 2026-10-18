@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev122 camera/present tail attribution:
rem - dev121 quiet normal-play path
rem - dev118 precise VI wait
rem - dev119 Immediate presentation
rem - proven FIFO Drain
rem - logs only >20 ms produced frames and >25 ms host Present intervals
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_PRESENT_MODE=immediate"
set "OPENSPM_M14_FIFO_MODE=drain"

echo [OpenSPM] Launching dev122 camera/present tail attribution run.
echo [OpenSPM] chunk=256 state_mem=1 heavy_diag=0 precise-VI-wait=1.
echo [OpenSPM] Presentation: Immediate. FIFO parser: Drain.
echo [OpenSPM] Tail-only diagnostics correlate long frame phases with Mario/cam5 state and host Present gaps.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
