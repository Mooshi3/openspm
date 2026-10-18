@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev135 tear-free monitor-half cadence diagnostic:
rem - same dev132 ~49.9715 Hz simulation diagnostic
rem - 99.943 Hz display should show each source frame for ~2 refreshes
rem - force FIFO + vsync ON to remove Immediate-mode horizontal tearing
rem - gameplay is intentionally ~16.7%% slow; this is diagnostic only
set "OPENSPM_M14_VSYNC=1"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_FINAL_IDLE_SPIN=1"
set "OPENSPM_M14_PRESENT_MODE=fifo"
set "OPENSPM_M14_FIFO_MODE=drain"
set "OPENSPM_M14_STICKY_PRESSED=1"
set "OPENSPM_M14_MAIN_PRIORITY=off"
set "OPENSPM_M14_MAIN_QOS=off"
set "OPENSPM_M14_MAIN_CPUSET=fast"
set "OPENSPM_M14_DIAG_HALF_99_943HZ=1"

echo [OpenSPM] Launching dev135 tear-free monitor-half cadence diagnostic.
echo [OpenSPM] WARNING: gameplay is intentionally ~16.7%% slow for this A/B.
echo [OpenSPM] VI = ~49.9715 Hz; display = ~99.943 Hz; target mapping is ~2:1.
echo [OpenSPM] Presentation: FIFO with vsync ON. Horizontal tearing should be eliminated.
echo [OpenSPM] Simulation/cadence settings otherwise match dev132.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
