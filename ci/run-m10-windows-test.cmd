@echo off
setlocal
if "%~1"=="" (
  echo Usage: %~nx0 ^<extracted R8PE01 game root^>
  echo Example: %~nx0 "C:\Games\Super Paper Mario"
  pause
  exit /b 2
)

set "GAME_ROOT=%~f1"
set "BUNDLE_ROOT=%~dp0"
set "LOG=%BUNDLE_ROOT%M10-WINDOWS-RUNTIME.log"
set "USER_DIR=%BUNDLE_ROOT%User"

if not exist "%GAME_ROOT%\sys\main.dol" (
  echo Missing "%GAME_ROOT%\sys\main.dol"
  pause
  exit /b 3
)

pushd "%BUNDLE_ROOT%"
echo Launching fixed M10 runtime. Complete title -^> file select/load -^> controllable Mario.
echo Runtime output will be saved to "%LOG%".
moderngekko-run.exe --game "%GAME_ROOT%" --user-dir "%USER_DIR%" --graphics OGL > "%LOG%" 2>&1
set "RESULT=%ERRORLEVEL%"
popd

echo.
echo Runtime exited with code %RESULT%.
echo Please keep "%LOG%" for the final native/fallback counters.
pause
exit /b %RESULT%
