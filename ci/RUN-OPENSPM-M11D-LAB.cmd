@echo off
setlocal
color 0E

echo ============================================================
echo   OpenSPM M11D - RUNTIME LAB (NO PROFILE)
echo ============================================================
echo.
echo   1. Baseline             - busy-wait OFF, known idle OFF
echo   2. Known idle           - R8PE01 8027551C only
echo   3. Known idle + affinity- scheduler idle plus cache affinity
echo   4. Dolphin JIT          - JIT control, static recomp OFF
echo.
echo Automatic analyzer busy-wait detection is disabled here because the first
echo M11D build showed a boot crash when that experiment was enabled.
echo Profiling is OFF in every mode so FPS comparisons are meaningful.
echo.
set /p "MODE=Mode [1-4, default 2]: "
if "%MODE%"=="" set "MODE=2"

set "OPENSPM_BUSYWAIT=0"
set "OPENSPM_IDLE_PC=0"
set "MODERNGEKKO_CACHE_AFFINITY=0"
set "MODERNGEKKO_STATICRECOMP=1"
set "OPENSPM_PERF="
set "STATICRECOMP_DISPATCH_SAMPLES="
set "MODE_NAME=baseline"

if "%MODE%"=="2" goto mode2
if "%MODE%"=="3" goto mode3
if "%MODE%"=="4" goto mode4
goto mode_ready

:mode2
set "OPENSPM_IDLE_PC=1"
set "MODE_NAME=known-idle"
goto mode_ready

:mode3
set "OPENSPM_IDLE_PC=1"
set "MODERNGEKKO_CACHE_AFFINITY=1"
set "MODE_NAME=known-idle-affinity"
goto mode_ready

:mode4
set "MODERNGEKKO_STATICRECOMP=0"
set "MODE_NAME=dolphin-jit64-control"

:mode_ready
echo.
if "%~1"=="" (
  set /p "GAME_ROOT=Game folder: "
) else (
  set "GAME_ROOT=%~1"
)
set "GAME_ROOT=%GAME_ROOT:"=%"
if not exist "%GAME_ROOT%\sys\main.dol" (
  echo ERROR: cannot find "%GAME_ROOT%\sys\main.dol"
  pause
  exit /b 3
)

set "BUNDLE_ROOT=%~dp0"
set "USER_DIR=%BUNDLE_ROOT%User-OpenSPM-M11D-AB"
set "M10_MMU=1"

echo.
echo Starting %MODE_NAME%...
echo Use File - Load State after the game window opens if you have a benchmark savestate.
echo.

pushd "%BUNDLE_ROOT%"
moderngekko-run.exe --game "%GAME_ROOT%" --user-dir "%USER_DIR%" --graphics OGL
set "RESULT=%ERRORLEVEL%"
popd

echo.
echo Finished with exit code %RESULT%.
pause
exit /b %RESULT%
