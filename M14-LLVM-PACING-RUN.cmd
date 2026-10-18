@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev118 keeps dev117/chunk256/state-memory/Mailbox semantics and changes
rem only the final host idle wait inside 2 ms of the fixed VI deadline.
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_PRESENT_MODE=mailbox"
set "OPENSPM_M14_FIFO_MODE=drain"

echo [OpenSPM] Launching dev118 retrace-pacing candidate.
echo [OpenSPM] Baseline knobs: chunk=256 state_mem=1 heavy_diag=0 Mailbox.
echo [OpenSPM] Wait A/B: Sleep(1) when more than 2 ms early; Sleep(0) in final 2 ms.
echo [OpenSPM] Wii VI grid, guest simulation, GX ordering and dev117 callback walker unchanged.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
