@echo off
setlocal
set "ROOT=%~dp0"

echo ============================================================
echo OpenSPM M14 - performance regression gate
echo ============================================================
echo Exit 0 = valid non-regression
echo Exit 3 = invalid/incomplete benchmark
echo Exit 4 = valid benchmark with material regression
echo.

if "%~1"=="" (
    echo [OpenSPM] No mode supplied; comparing latest chunk128 -> chunk256 Yold captures.
    call "%ROOT%M14-PERF-REPORT.cmd" --compare-latest-chunks --quality-gate --regression-gate
) else (
    call "%ROOT%M14-PERF-REPORT.cmd" %* --quality-gate --regression-gate
)

set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo [OpenSPM] Performance gate PASS.
) else if "%RC%"=="3" (
    echo [OpenSPM] Performance gate INVALID: benchmark quality requirements were not met.
) else if "%RC%"=="4" (
    echo [OpenSPM] Performance gate FAIL: candidate crossed a configured regression tolerance.
) else (
    echo [OpenSPM] Performance gate stopped with code %RC%.
)
exit /b %RC%
