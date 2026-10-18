@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem dev121 quiet normal-play candidate:
rem - retain dev118 precise VI wait
rem - retain dev119 Immediate presentation
rem - return to proven FIFO Drain after dev120 Inline did not change stutter
rem - heavy_diag=0 now bypasses the historical dev67 per-native-slice profiler
set "OPENSPM_M14_VSYNC=0"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_PRESENT_MODE=immediate"
set "OPENSPM_M14_FIFO_MODE=drain"

echo [OpenSPM] Launching dev121 quiet normal-play candidate.
echo [OpenSPM] chunk=256 state_mem=1 heavy_diag=0 precise-VI-wait=1.
echo [OpenSPM] Presentation: Immediate. FIFO parser: proven Drain baseline.
echo [OpenSPM] Retired from normal play: dev67 per-slice clocks/histograms and legacy sequence/HP/bbox observation wrappers.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
