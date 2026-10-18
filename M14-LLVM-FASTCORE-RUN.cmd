@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev127 soft fast-core CPU Set A/B:
rem - current best renderer/pacing baseline
rem - normal producer priority and QoS
rem - no final-spin experiment
rem - sticky pressed-edge delivery ON
rem - select only Windows CPU Sets in the highest reported EfficiencyClass
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_FINAL_IDLE_SPIN=0"
set "OPENSPM_M14_PRESENT_MODE=immediate"
set "OPENSPM_M14_FIFO_MODE=drain"
set "OPENSPM_M14_STICKY_PRESSED=1"
set "OPENSPM_M14_MAIN_PRIORITY=off"
set "OPENSPM_M14_MAIN_QOS=off"
set "OPENSPM_M14_MAIN_CPUSET=fast"

echo [OpenSPM] Launching dev127 soft fast-core CPU Set A/B.
echo [OpenSPM] chunk=256 state_mem=1 heavy_diag=0 precise-VI-wait=1.
echo [OpenSPM] Presentation: Immediate. FIFO: Drain. Sticky pressed edges: ON.
echo [OpenSPM] Scheduling: normal priority/QoS; producer soft-preferred to highest EfficiencyClass CPU Sets.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
