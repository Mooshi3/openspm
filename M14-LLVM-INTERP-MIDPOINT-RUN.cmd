@echo off
setlocal

echo [OpenSPM] RETIRED: dev137 live midpoint substitution is rejected.
echo [OpenSPM] It caused severe DEMODoneRender stalls and visible cross-instance geometry corruption.
echo [OpenSPM] Redirecting to the safe dev138 geometry-identity probe instead.
echo.
call "%~dp0M14-LLVM-INTERP-IDENTITY-RUN.cmd" "%~1"
exit /b %errorlevel%
