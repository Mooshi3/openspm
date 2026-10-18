@echo off
setlocal
call "%~dp0M14-DEBUG-CONSOLE.cmd" --script "%~dp0tools\native_bridge\experiments\cockpit_smoke.spmdbg"
exit /b %errorlevel%
