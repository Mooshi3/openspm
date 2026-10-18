@echo off
setlocal
color 0A

echo ============================================================
echo   OpenSPM M11D - SAFE BASELINE
echo ============================================================
echo.

if "%~1"=="" (
  echo Paste the path to your extracted US R8PE01 game folder below.
  echo Example: C:\Games\Super Paper Mario
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
set "LOG=%BUNDLE_ROOT%OpenSPM.log"
set "USER_DIR=%BUNDLE_ROOT%User-OpenSPM"
set "M10_MMU=1"
set "MODERNGEKKO_STATICRECOMP=1"
set "OPENSPM_BUSYWAIT=0"
set "OPENSPM_IDLE_PC=0"
set "MODERNGEKKO_CACHE_AFFINITY=0"
set "OPENSPM_PERF="

pushd "%BUNDLE_ROOT%"
echo Starting OpenSPM safe baseline...
echo Runtime output is being saved to:
echo   %LOG%
echo.
moderngekko-run.exe --game "%GAME_ROOT%" --user-dir "%USER_DIR%" --graphics OGL > "%LOG%" 2>&1
set "RESULT=%ERRORLEVEL%"
popd

echo.
echo OpenSPM exited with code %RESULT%.
echo Log:
echo   %LOG%
pause
exit /b %RESULT%
