@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev130 combined smooth-pacing candidate:
rem - dev129 dynamic scheduler-idle side exit active in the bridge
rem - retain dev125 cached QPC frequency
rem - retain dev118 fixed-grid precise VI wait
rem - retain dev119 Immediate presentation + FIFO Drain
rem - sticky pressed-edge retention ON (no-sticky was a player-visible regression)
rem - combine the two helpful scheduling A/Bs: final 2 ms idle spin + fast-core CPU Sets
rem - keep priority and QoS neutral
rem - explicitly force dev132 cadence diagnostic OFF so this remains a clean 60 Hz baseline
set "OPENSPM_M14_DIAG_HALF_99_943HZ=0"
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_FINAL_IDLE_SPIN=1"
set "OPENSPM_M14_PRESENT_MODE=immediate"
set "OPENSPM_M14_FIFO_MODE=drain"
set "OPENSPM_M14_STICKY_PRESSED=1"
set "OPENSPM_M14_MAIN_PRIORITY=off"
set "OPENSPM_M14_MAIN_QOS=off"
set "OPENSPM_M14_MAIN_CPUSET=fast"

echo [OpenSPM] Launching dev130 combined smooth-pacing candidate.
echo [OpenSPM] dev129 dynamic empty-idle side exit is active in the bridge.
echo [OpenSPM] chunk=256 state_mem=1 heavy_diag=0.
echo [OpenSPM] Presentation: Immediate. FIFO: Drain. Sticky pressed edges: ON.
echo [OpenSPM] Scheduling: fast-core soft CPU Sets + final 2 ms true-idle spin.
echo [OpenSPM] Cadence diagnostic: OFF. Authoritative simulation target: 60 Hz.
echo [OpenSPM] Priority/QoS remain system-default.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
