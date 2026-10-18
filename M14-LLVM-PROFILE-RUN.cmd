@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"
echo [OpenSPM] Launching chunk256 state_mem=1 with heavy dev111/dev112/dev115 profiling enabled.
echo [OpenSPM] Normal M14-LLVM-UPDATE-RUN.cmd leaves this profiling off.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 1
exit /b %errorlevel%
