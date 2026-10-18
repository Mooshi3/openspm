@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev126 final-idle-spin A/B:
rem - retain current best renderer/pacing baseline
rem - normal producer priority/QoS
rem - sticky pressed-edge delivery ON
rem - while Nintendo scheduler is truly idle, replace only final <=2ms Sleep(0)
rem   yield storm with a local YieldProcessor spin to the same fixed VI deadline
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_FINAL_IDLE_SPIN=1"
set "OPENSPM_M14_PRESENT_MODE=immediate"
set "OPENSPM_M14_FIFO_MODE=drain"
set "OPENSPM_M14_STICKY_PRESSED=1"
set "OPENSPM_M14_MAIN_PRIORITY=off"
set "OPENSPM_M14_MAIN_QOS=off"

echo [OpenSPM] Launching dev126 final-idle-spin A/B.
echo [OpenSPM] chunk=256 state_mem=1 heavy_diag=0.
echo [OpenSPM] Presentation: Immediate. FIFO: Drain. Producer priority/QoS: normal.
echo [OpenSPM] Input: sticky pressed edges ON. VI grid unchanged.
echo [OpenSPM] Final <=2ms true-idle wait: YieldProcessor spin instead of repeated Sleep(0).
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
