@echo off
setlocal
pushd "%~dp0"
echo Launching OpenSPM through the proven process-backed M13 adapter...
OpenSPM.exe --backend "runtime\moderngekko_legacy_backend.dll" --launch
set "RESULT=%ERRORLEVEL%"
popd
echo OpenSPM legacy backend exited with code %RESULT%.
pause
exit /b %RESULT%
