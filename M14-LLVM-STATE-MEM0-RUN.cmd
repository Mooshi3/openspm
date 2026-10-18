@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"
echo [OpenSPM] Launching retained chunk256 state_mem=0 fallback/bisect build.
echo [OpenSPM] Normal M14-LLVM-UPDATE-RUN.cmd now defaults to state_mem=1.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 0
exit /b %errorlevel%
