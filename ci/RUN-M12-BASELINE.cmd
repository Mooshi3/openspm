@echo off
setlocal
set "OPENSPM_NATIVE_HELPERS=0"
call "%~dp0RUN-OPENSPM.cmd" %*
exit /b %ERRORLEVEL%
