@echo off
setlocal
title OpenSPM M11D2 Baseline
color 0A

echo OpenSPM M11D2 BASELINE
echo Known scheduler idle optimization OFF
echo Automatic analyzer detector OFF
echo Profiling OFF
echo.

if not "%~1"=="" goto have_arg
set "GAME_ROOT="
set /p "GAME_ROOT=Game folder: "
goto got_path
:have_arg
set "GAME_ROOT=%~1"
:got_path
set "GAME_ROOT=%GAME_ROOT:"=%"

if exist "%GAME_ROOT%\sys\main.dol" goto valid_game
echo ERROR: cannot find %GAME_ROOT%\sys\main.dol
pause
exit /b 3

:valid_game
set "BUNDLE_ROOT=%~dp0"
set "USER_DIR=%BUNDLE_ROOT%User-OpenSPM-M11D2"
set "M10_MMU=1"
set "MODERNGEKKO_STATICRECOMP=1"
set "OPENSPM_BUSYWAIT=0"
set "OPENSPM_IDLE_PC=0"
set "MODERNGEKKO_CACHE_AFFINITY=0"
set "OPENSPM_PERF="
set "STATICRECOMP_DISPATCH_SAMPLES="

pushd "%BUNDLE_ROOT%"
moderngekko-run.exe --game "%GAME_ROOT%" --user-dir "%USER_DIR%" --graphics OGL
set "RESULT=%ERRORLEVEL%"
popd

echo.
echo OpenSPM exited with code %RESULT%.
pause
exit /b %RESULT%
