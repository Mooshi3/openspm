@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev120 safe parser-pacing A/B:
rem - Immediate presentation (dev119 winner)
rem - precise final-2ms VI wait (dev118 small win)
rem - same-thread Aurora FIFO Inline parsing at command-complete prefixes
rem - no asynchronous worker; mutable guest RAM remains Broadway-synchronous
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_PRESENT_MODE=immediate"
set "OPENSPM_M14_FIFO_MODE=inline"

echo [OpenSPM] Launching dev120 same-thread FIFO Inline pacing candidate.
echo [OpenSPM] Baseline knobs: chunk=256 state_mem=1 heavy_diag=0 precise-VI-wait=1.
echo [OpenSPM] Presentation: Immediate. FIFO parser: Inline same-thread complete-prefix publication.
echo [OpenSPM] No Aurora FIFO worker is used; guest-memory observation stays synchronous.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
