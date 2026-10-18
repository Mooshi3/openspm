@echo off
setlocal
set "ROOT=%~dp0"

echo ============================================================
echo OpenSPM M14 - chunk256 state-memory Yold A/B
echo ============================================================
echo Compares the newest canonical yold-perf captures:
echo   baseline:  chunk=256 state_mem=0
echo   candidate: chunk=256 state_mem=1
echo.

call "%ROOT%M14-PERF-REPORT.cmd" --compare-latest-state-memory --quality-gate --regression-gate
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo [OpenSPM] State-memory A/B is valid and non-regressing.
) else if "%RC%"=="2" (
    echo [OpenSPM] Missing one side of the A/B. Capture Yold once under each mode.
) else if "%RC%"=="3" (
    echo [OpenSPM] State-memory A/B capture quality is incomplete.
) else if "%RC%"=="4" (
    echo [OpenSPM] State-memory candidate materially regressed the baseline.
) else (
    echo [OpenSPM] State-memory comparison stopped with code %RC%.
)
exit /b %RC%
