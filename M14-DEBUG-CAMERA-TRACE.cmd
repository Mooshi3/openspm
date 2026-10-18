@echo off
setlocal
set "ROOT=%~dp0"

echo ============================================================
echo OpenSPM M14 - live camera follow trace
echo ============================================================
echo No native rebuild is required.
echo.
echo Stand in Yold Town / he2_03 and hold RIGHT steadily while this runs.
echo It records Mario plus camera 11 ^(2D^) and camera 5 ^(3D^) together.
echo For a 3D-specific sample, flip first and run this command again.
echo.

call "%ROOT%M14-DEBUG-CONSOLE.cmd" --script "%ROOT%tools\native_bridge\experiments\camera_follow_trace.spmdbg"
if errorlevel 1 exit /b 1
exit /b 0
