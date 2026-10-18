@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem Match the player-tested chunk256/state-memory/Mailbox baseline. dev117 lives
rem in the bridge; this launcher keeps every build/runtime knob otherwise fixed.
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=0"

echo [OpenSPM] Launching dev117 callback baseline with dev118 precise idle wait OFF.
echo [OpenSPM] Baseline knobs: chunk=256 state_mem=1 heavy_diag=0 Mailbox A/B.
echo [OpenSPM] dev117: same-PC + lower downcount counts as progress; max nested
echo [OpenSPM] steps=128; unchanged callback cycle quantum=32768.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
