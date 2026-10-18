@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"
echo [OpenSPM] state_mem=1 is now the normal chunk256 LLVM default.
echo [OpenSPM] This launcher is retained as a compatibility alias.
echo [OpenSPM] Use M14-LLVM-STATE-MEM0-RUN.cmd for the old sim0 fallback.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1
exit /b %errorlevel%
