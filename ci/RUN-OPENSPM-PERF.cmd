@echo off
setlocal
color 0E

echo ============================================================
echo   OpenSPM M11B - PERFORMANCE DIAGNOSTIC
echo ============================================================
echo.
echo This run records one-second static-recomp runtime samples.
echo For the current opening benchmark, let the game pass through:
echo   - title screen
echo   - Bowser castle exterior
echo   - Mario/Luigi house
echo   - Bowser castle interior
echo Then close OpenSPM and upload OpenSPM-PERF.log.
echo.

if "%~1"=="" (
  echo Paste the path to your extracted US R8PE01 game folder below.
  echo.
  set /p "GAME_ROOT=Game folder: "
) else (
  set "GAME_ROOT=%~1"
)
set "GAME_ROOT=%GAME_ROOT:"=%"

if not exist "%GAME_ROOT%\sys\main.dol" (
  echo.
  echo ERROR: I cannot find "%GAME_ROOT%\sys\main.dol"
  pause
  exit /b 3
)

set "BUNDLE_ROOT=%~dp0"
set "LOG=%BUNDLE_ROOT%OpenSPM-PERF.log"
set "USER_DIR=%BUNDLE_ROOT%User-OpenSPM-PERF"
set "M10_MMU=1"
set "MODERNGEKKO_STATICRECOMP=1"
set "OPENSPM_PERF=1"
set "STATICRECOMP_DISPATCH_SAMPLES=1"

pushd "%BUNDLE_ROOT%"
echo Starting profiled OpenSPM run...
echo Output is being saved to:
echo   %LOG%
echo.
moderngekko-run.exe --game "%GAME_ROOT%" --user-dir "%USER_DIR%" --graphics OGL > "%LOG%" 2>&1
set "RESULT=%ERRORLEVEL%"
popd

echo.
echo Performance run finished.
echo Upload this file to ChatGPT:
echo   %LOG%
pause
exit /b %RESULT%
