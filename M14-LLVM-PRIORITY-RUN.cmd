@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev123 producer scheduling A/B:
rem - retain dev118 precise VI wait
rem - retain dev119 Immediate presentation
rem - proven FIFO Drain
rem - normal heavy_diag=0
rem - keep dev29 sticky pressed-edge delivery ON for a one-axis comparison
rem - raise only the already-created producer/main Windows thread to ABOVE_NORMAL
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_PRESENT_MODE=immediate"
set "OPENSPM_M14_FIFO_MODE=drain"
set "OPENSPM_M14_STICKY_PRESSED=1"
set "OPENSPM_M14_MAIN_PRIORITY=above_normal"

echo [OpenSPM] Launching dev123 producer scheduling A/B.
echo [OpenSPM] chunk=256 state_mem=1 heavy_diag=0 precise-VI-wait=1.
echo [OpenSPM] Presentation: Immediate. FIFO parser: Drain.
echo [OpenSPM] Input: historical sticky pressed-edge delivery ON.
echo [OpenSPM] Host scheduling: producer/main thread ABOVE_NORMAL after Aurora worker creation.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
