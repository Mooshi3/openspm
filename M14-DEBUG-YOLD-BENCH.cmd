@echo off
setlocal
set "ROOT=%~dp0"

echo ============================================================
echo OpenSPM M14 - Yold Town scoped performance benchmark
echo ============================================================
echo Stand in Yold Town / he2_03.
echo During the 8-second capture, run/jump steadily in one direction.
echo.

call "%ROOT%M14-DEBUG-CONSOLE.cmd" --script "%ROOT%tools\native_bridge\experiments\yold_perf_benchmark.spmdbg"
if errorlevel 1 (
    echo.
    echo [OpenSPM] Yold capture failed or was stopped; no report generated.
    exit /b %errorlevel%
)

echo.
echo [OpenSPM] Latest Yold capture summary + quality gate:
call "%ROOT%M14-PERF-REPORT.cmd" --latest-capture yold-perf --quality-gate
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [OpenSPM] Capture was saved, but benchmark quality did not pass.
    echo [OpenSPM] Fix the reported identity/measurement issue before using it in an A/B.
)
exit /b %RC%
