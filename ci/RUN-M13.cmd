@echo off
setlocal
title OpenSPM M13
set "GAME_ROOT=%~1"
pushd "%~dp0"
if defined GAME_ROOT goto launch_with_path

start "" OpenSPM.exe --gui
popd
exit /b 0

:launch_with_path
set "GAME_ROOT=%GAME_ROOT:"=%"
if not exist "%GAME_ROOT%\sys\main.dol" goto bad_game
OpenSPM.exe --launch --game "%GAME_ROOT%"
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%

:bad_game
popd
echo ERROR: sys\main.dol was not found in "%GAME_ROOT%".
pause
exit /b 3
