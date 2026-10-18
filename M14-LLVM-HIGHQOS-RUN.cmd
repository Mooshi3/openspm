@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev124 producer HighQoS A/B:
rem - retain current best rendering/pacing baseline
rem - keep producer/main thread priority NORMAL
rem - keep sticky pressed-edge delivery ON
rem - explicitly disable Windows execution-speed throttling on producer thread
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_PRESENT_MODE=immediate"
set "OPENSPM_M14_FIFO_MODE=drain"
set "OPENSPM_M14_STICKY_PRESSED=1"
set "OPENSPM_M14_MAIN_PRIORITY=off"
set "OPENSPM_M14_MAIN_QOS=high"

echo [OpenSPM] Launching dev124 producer HighQoS A/B.
echo [OpenSPM] chunk=256 state_mem=1 heavy_diag=0 precise-VI-wait=1.
echo [OpenSPM] Presentation: Immediate. FIFO parser: Drain.
echo [OpenSPM] Input: sticky pressed edges ON. Producer priority: normal.
echo [OpenSPM] Windows QoS: explicit HighQoS ^(execution-speed throttling OFF^) on producer only.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
