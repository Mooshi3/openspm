@echo off
setlocal
title OpenSPM M13 - direct M12 fallback
set "GAME_ROOT=%~1"
if defined GAME_ROOT goto got_path
set /p "GAME_ROOT=Extracted R8PE01 game folder: "
:got_path
set "GAME_ROOT=%GAME_ROOT:"=%"
if not exist "%GAME_ROOT%\sys\main.dol" goto bad_game
set "LEGACY=%~dp0runtime\legacy"
if not exist "%LEGACY%\moderngekko-run.exe" goto bad_runtime
if not exist "%LEGACY%\gR8PE01_recomp.dll" goto bad_runtime
if not defined OPENSPM_USER_DIR set "OPENSPM_USER_DIR=%~dp0User-OpenSPM"
if not defined OPENSPM_NATIVE_HELPERS set "OPENSPM_NATIVE_HELPERS=1"
set "M10_MMU=1"
set "MODERNGEKKO_STATICRECOMP=1"
set "OPENSPM_BUSYWAIT=0"
set "OPENSPM_IDLE_PC=1"
set "MODERNGEKKO_CACHE_AFFINITY=0"
set "OPENSPM_PERF="
set "STATICRECOMP_DISPATCH_SAMPLES="
pushd "%LEGACY%"
moderngekko-run.exe --game "%GAME_ROOT%" --user-dir "%OPENSPM_USER_DIR%" --graphics OGL
set "RESULT=%ERRORLEVEL%"
popd
echo Direct M12 fallback exited with code %RESULT%.
pause
exit /b %RESULT%
:bad_game
echo ERROR: sys\main.dol was not found in "%GAME_ROOT%".
pause
exit /b 3
:bad_runtime
echo ERROR: M12 legacy payload is incomplete.
pause
exit /b 4
