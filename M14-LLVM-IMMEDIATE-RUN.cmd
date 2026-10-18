@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev119 presentation A/B: keep dev118's precise VI wait and all proven
rem chunk/state-memory semantics, but force Aurora/Dawn Immediate presentation.
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_PRESENT_MODE=immediate"
set "OPENSPM_M14_FIFO_MODE=drain"

echo [OpenSPM] Launching dev119 Immediate presentation candidate.
echo [OpenSPM] Baseline knobs: chunk=256 state_mem=1 heavy_diag=0 precise-VI-wait=1.
echo [OpenSPM] Presentation: Immediate forced instead of Mailbox.
echo [OpenSPM] Guest timing, fixed VI grid, GX ordering and dev117 callback walker unchanged.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
