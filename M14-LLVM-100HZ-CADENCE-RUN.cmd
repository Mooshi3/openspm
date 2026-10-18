@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev132 monitor-half cadence diagnostic:
rem - identical to dev130 smooth candidate except simulation VI period
rem - player monitor reported 99.943 Hz
rem - diagnostic period is 20,011,407 ns ~= 49.9715 Hz, exactly half display rate
rem - gameplay will intentionally run ~16.7%% slow; this is NOT a production solution
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_FINAL_IDLE_SPIN=1"
set "OPENSPM_M14_PRESENT_MODE=immediate"
set "OPENSPM_M14_FIFO_MODE=drain"
set "OPENSPM_M14_STICKY_PRESSED=1"
set "OPENSPM_M14_MAIN_PRIORITY=off"
set "OPENSPM_M14_MAIN_QOS=off"
set "OPENSPM_M14_MAIN_CPUSET=fast"
set "OPENSPM_M14_DIAG_HALF_99_943HZ=1"

echo [OpenSPM] Launching dev132 monitor-half cadence diagnostic.
echo [OpenSPM] WARNING: gameplay is intentionally ~16.7%% slow for this A/B.
echo [OpenSPM] VI = ~49.9715 Hz so source frames map ~2:1 onto 99.943 Hz display refresh.
echo [OpenSPM] All other settings match M14-LLVM-SMOOTH-RUN.cmd.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
