@echo off
setlocal
title OpenSPM M12
set "GAME_ROOT=%~1"
if defined GAME_ROOT goto got_path
if exist "D:\Desktop\spm-m10-windows-playable-runtime\spmExtracted\sys\main.dol" set "GAME_ROOT=D:\Desktop\spm-m10-windows-playable-runtime\spmExtracted"
if defined GAME_ROOT goto got_path
set /p "GAME_ROOT=Extracted game folder: "
:got_path
set "GAME_ROOT=%GAME_ROOT:"=%"
if not exist "%GAME_ROOT%\sys\main.dol" goto bad_game
set "BUNDLE_ROOT=%~dp0"
if not exist "%BUNDLE_ROOT%gR8PE01_recomp.dll" goto bad_module
if not defined OPENSPM_USER_DIR set "OPENSPM_USER_DIR=%BUNDLE_ROOT%User-OpenSPM-M11D2"
if not defined OPENSPM_NATIVE_HELPERS set "OPENSPM_NATIVE_HELPERS=1"
set "M10_MMU=1"
set "MODERNGEKKO_STATICRECOMP=1"
set "OPENSPM_BUSYWAIT=0"
set "OPENSPM_IDLE_PC=1"
set "MODERNGEKKO_CACHE_AFFINITY=0"
set "OPENSPM_PERF="
set "STATICRECOMP_DISPATCH_SAMPLES="
set "LOG=%BUNDLE_ROOT%M12-NATIVE-%OPENSPM_NATIVE_HELPERS%.log"
echo OpenSPM M12 - native helper=%OPENSPM_NATIVE_HELPERS%
echo Log: %LOG%
echo Choose OpenSPM / Aspect Ratio / 21:9 in the game window.
pushd "%BUNDLE_ROOT%"
moderngekko-run.exe --game "%GAME_ROOT%" --user-dir "%OPENSPM_USER_DIR%" --graphics OGL > "%LOG%" 2>&1
set "RESULT=%ERRORLEVEL%"
popd
echo OpenSPM exited with code %RESULT%.
echo Log saved to %LOG%.
pause
exit /b %RESULT%
:bad_game
echo ERROR: sys\main.dol was not found in "%GAME_ROOT%".
pause
exit /b 3
:bad_module
echo ERROR: gR8PE01_recomp.dll is missing from the bundle.
pause
exit /b 4
