@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev123 sticky-input isolation A/B:
rem - retain current best rendering/pacing baseline
rem - keep producer/main thread at NORMAL priority
rem - disable ONLY pressed-edge carry across retraces
rem - held directions still come directly from the physical key sample each input epoch
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_PRESENT_MODE=immediate"
set "OPENSPM_M14_FIFO_MODE=drain"
set "OPENSPM_M14_STICKY_PRESSED=0"
set "OPENSPM_M14_MAIN_PRIORITY=off"

echo [OpenSPM] Launching dev123 no-sticky-input A/B.
echo [OpenSPM] chunk=256 state_mem=1 heavy_diag=0 precise-VI-wait=1.
echo [OpenSPM] Presentation: Immediate. FIFO parser: Drain. Producer priority: normal.
echo [OpenSPM] Input: pressed-edge carry OFF; held-direction sampling is unchanged.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
