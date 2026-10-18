@echo off
setlocal
color 0B

echo ============================================================
echo   SPM M10 FIRST-FAULT TEST 2 OF 2 - MMU ON
echo ============================================================
echo.
echo This run should reproduce the 0.0 FPS / frozen-looking state.
echo When you are done, close the game and send back M10-FIRSTFAULT-MMU-ON.log.
echo.

if "%~1"=="" (
  echo Paste the path to your extracted R8PE01 game folder below.
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
  echo Make sure you selected the extracted R8PE01 game folder.
  pause
  exit /b 3
)

set "BUNDLE_ROOT=%~dp0"
set "LOG=%BUNDLE_ROOT%M10-FIRSTFAULT-MMU-ON.log"
set "USER_DIR=%BUNDLE_ROOT%User-MMU-ON"
set "M10_MMU=1"
set "MODERNGEKKO_STATICRECOMP=1"

pushd "%BUNDLE_ROOT%"
echo.
echo Starting MMU-ON test...
echo Let it reach the 0.0 FPS / frozen-looking state, then close it.
echo Output is being saved to:
echo   %LOG%
echo.
moderngekko-run.exe --game "%GAME_ROOT%" --user-dir "%USER_DIR%" --graphics OGL > "%LOG%" 2>&1
set "RESULT=%ERRORLEVEL%"
popd

echo.
echo Test finished. Log saved here:
echo   %LOG%
echo.
echo Upload that log to ChatGPT. You do not need to interpret it.
pause
exit /b %RESULT%
