@echo off
setlocal
color 0E

echo ============================================================
echo   SPM M10 FIRST-FAULT TEST 1 OF 2 - MMU OFF
echo ============================================================
echo.
echo This run should reproduce the invalid-read popup.
echo When you are done, send back M10-FIRSTFAULT-MMU-OFF.log.
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
set "LOG=%BUNDLE_ROOT%M10-FIRSTFAULT-MMU-OFF.log"
set "USER_DIR=%BUNDLE_ROOT%User-MMU-OFF"
set "M10_MMU=0"
set "MODERNGEKKO_STATICRECOMP=1"

pushd "%BUNDLE_ROOT%"
echo.
echo Starting MMU-OFF test...
echo If an invalid-read popup appears, that is expected for this test.
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
