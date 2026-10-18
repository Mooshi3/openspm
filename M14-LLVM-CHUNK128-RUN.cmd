@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"
echo [OpenSPM] Launching retained LLVM chunk128 baseline for direct A/B.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 128 0
exit /b %errorlevel%
