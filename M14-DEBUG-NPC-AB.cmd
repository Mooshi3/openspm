@echo off
setlocal
call "%~dp0M14-DEBUG-CONSOLE.cmd" --script "%~dp0tools\native_bridge\experiments\npc_performance_ab.spmdbg"
exit /b %errorlevel%
