@echo off
setlocal

set "ROOT=%~dp0"
set "CANDIDATE=%ROOT%.openspm\m14\windows-candidate"
set "LAUNCHER=%CANDIDATE%\OpenSPMNative.exe"
set "GAME=%ROOT%.openspm\m14\game"
set "LOGDIR=%ROOT%.openspm\m14\home-test"
set "LOG=%LOGDIR%\launch.log"
set "PREVIOUS_LOG=%LOGDIR%\launch.previous.log"
set "DEBUG_SCRIPT=%ROOT%tools\native_bridge\run_windows_debug.py"
set "SYMBOLIZER=%ROOT%tools\native_bridge\symbolize_renderer_map.py"
set "RENDERER_MAP="
set "GRAPHICS=AUTO"
set "REVISION=unknown"
set "DEBUG_PY="
set "DEBUG_PY_ARGS="

if not "%~1"=="" set "GRAPHICS=%~1"
if not "%~2"=="" set "REVISION=%~2"
if defined OPENSPM_REPO_REVISION set "REVISION=%OPENSPM_REPO_REVISION%"

if not exist "%LAUNCHER%" (
    echo [OpenSPM] Missing candidate launcher:
    echo   %LAUNCHER%
    exit /b 2
)
if not exist "%GAME%\sys\main.dol" (
    echo [OpenSPM] Missing extracted game:
    echo   %GAME%\sys\main.dol
    exit /b 2
)
if not exist "%CANDIDATE%\User-OpenSPM\native\openspm_local_native.dll" (
    echo [OpenSPM] Missing player-local native module.
    exit /b 2
)
if not exist "%DEBUG_SCRIPT%" (
    echo [OpenSPM] Missing Windows fail-fast debugger:
    echo   %DEBUG_SCRIPT%
    exit /b 2
)
if not exist "%SYMBOLIZER%" (
    echo [OpenSPM] Missing renderer map symbolizer:
    echo   %SYMBOLIZER%
    exit /b 2
)
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

rem Use the same Python discovery order as M14-UPDATE-RUN.cmd. The debugger is
rem diagnostic-only and preserves the child's exact 32-bit Windows exit code.
for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined DEBUG_PY set "DEBUG_PY=%%I"
if not defined DEBUG_PY if exist "C:\Python314\python.exe" set "DEBUG_PY=C:\Python314\python.exe"
if not defined DEBUG_PY (
    where py.exe >nul 2>nul
    if not errorlevel 1 (
        set "DEBUG_PY=py.exe"
        set "DEBUG_PY_ARGS=-3"
    )
)
if not defined DEBUG_PY (
    echo [OpenSPM] Python 3 is required for Windows fail-fast capture.
    exit /b 2
)

"%DEBUG_PY%" %DEBUG_PY_ARGS% "%DEBUG_SCRIPT%" --self-test
if errorlevel 1 (
    echo [OpenSPM] Windows fail-fast debugger self-test failed; refusing a blind runtime attempt.
    exit /b 2
)
"%DEBUG_PY%" %DEBUG_PY_ARGS% "%SYMBOLIZER%" --self-test
if errorlevel 1 (
    echo [OpenSPM] Renderer map symbolizer self-test failed; refusing a blind runtime attempt.
    exit /b 2
)

rem Produce a linker map for the exact renderer DLL that is about to run.
rem Source-level /MAP directives proved unreliable under this Ninja/MSVC link
rem path. link.exe explicitly supports the LINK environment variable, so force
rem one renderer-only relink with an absolute map destination, then restore the
rem caller's LINK setting before any other work. Deleting only the DLL is enough
rem to make Ninja relink without rebuilding Aurora or the game module.
set "MAP_CMAKE=%VCMAKE%"
if not defined MAP_CMAKE if defined VSINSTALLDIR if exist "%VSINSTALLDIR%Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" set "MAP_CMAKE=%VSINSTALLDIR%Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if not defined MAP_CMAKE for /f "delims=" %%I in ('where cmake.exe 2^>nul') do if not defined MAP_CMAKE set "MAP_CMAKE=%%I"
if not defined MAP_CMAKE (
    echo [OpenSPM] Could not locate CMake for the diagnostic renderer relink.
    exit /b 2
)

set "RENDERER_DLL=%CANDIDATE%\runtime\openspm_renderer.dll"
set "RENDERER_MAP_SOURCE=%CANDIDATE%\openspm_renderer.map"
set "RENDERER_MAP_SNAPSHOT=%LOGDIR%\openspm_renderer.snapshot.map"
if exist "%RENDERER_MAP_SOURCE%" del /q "%RENDERER_MAP_SOURCE%" >nul 2>nul
if exist "%RENDERER_MAP_SNAPSHOT%" del /q "%RENDERER_MAP_SNAPSHOT%" >nul 2>nul
set "OPENSPM_LINK_SAVE=%LINK%"
set LINK=%OPENSPM_LINK_SAVE% /MAP:"%RENDERER_MAP_SOURCE%" /MAPINFO:EXPORTS
if exist "%RENDERER_DLL%" del /q "%RENDERER_DLL%" >nul 2>nul

echo [OpenSPM] Relinking renderer once with MSVC linker-map capture...
"%MAP_CMAKE%" --build "%CANDIDATE%" --target openspm_renderer -j2
set "MAP_BUILD_RC=%ERRORLEVEL%"
set "LINK=%OPENSPM_LINK_SAVE%"
if not "%MAP_BUILD_RC%"=="0" (
    echo [OpenSPM] Diagnostic renderer relink failed with code %MAP_BUILD_RC%.
    exit /b 2
)
if not exist "%RENDERER_DLL%" (
    echo [OpenSPM] Renderer DLL is missing after diagnostic relink.
    exit /b 2
)
if not exist "%RENDERER_MAP_SOURCE%" (
    echo [OpenSPM] MSVC relink completed but did not emit the requested map:
    echo   %RENDERER_MAP_SOURCE%
    echo Refusing a blind crash reproduction; send this console output instead.
    exit /b 2
)

rem Preserve and byte-verify the exact map that matches this renderer before the
rem game starts. Post-crash symbolization never depends on build-tree lifetime.
copy /y "%RENDERER_MAP_SOURCE%" "%RENDERER_MAP_SNAPSHOT%" >nul
if errorlevel 1 (
    echo [OpenSPM] Could not snapshot the renderer linker map before launch.
    echo Source:
    echo   %RENDERER_MAP_SOURCE%
    exit /b 2
)
if not exist "%RENDERER_MAP_SNAPSHOT%" (
    echo [OpenSPM] Renderer linker-map snapshot is missing after copy.
    exit /b 2
)
fc /b "%RENDERER_MAP_SOURCE%" "%RENDERER_MAP_SNAPSHOT%" >nul
if errorlevel 1 (
    echo [OpenSPM] Renderer linker-map snapshot did not verify byte-for-byte.
    exit /b 2
)
set "RENDERER_MAP=%RENDERER_MAP_SNAPSHOT%"

rem Prefer a revision supplied by M14-UPDATE-RUN.cmd. If that was lost across a
rem nested batch scope, read the checked-out branch ref directly before asking
rem Git. This keeps launch.log provenance useful even when PATH differs here.
if "%REVISION%"=="unknown" if exist "%ROOT%.git\refs\heads\m14-native-execution" set /p "REVISION="<"%ROOT%.git\refs\heads\m14-native-execution"
if "%REVISION%"=="unknown" for /f "delims=" %%I in ('git -C "%ROOT%" rev-parse HEAD 2^>nul') do set "REVISION=%%I"
if not "%REVISION%"=="unknown" set "REVISION=%REVISION:~0,12%"
if exist "%LOG%" copy /y "%LOG%" "%PREVIOUS_LOG%" >nul

echo OpenSPM M14 direct candidate launch
echo Graphics: %GRAPHICS%
echo Revision: %REVISION%
echo Log: %LOG%
echo Keyboard mapping: Arrows/WASD = D-pad, Enter = Wii Remote 2, Space/Z = A, X = 1, C = B, Backspace = Minus
echo Windows fail-fast capture: enabled
echo Renderer symbol map snapshot: %RENDERER_MAP%
if exist "%PREVIOUS_LOG%" echo Previous log: %PREVIOUS_LOG%
echo.
echo Native stdout/stderr and Windows debugger diagnostics are captured by cmd.exe.
echo Close the game window to return to this prompt.
echo.

> "%LOG%" echo OpenSPM repo revision: %REVISION%
>> "%LOG%" echo OpenSPM requested graphics: %GRAPHICS%
>> "%LOG%" echo OpenSPM keyboard mapping: Arrows/WASD=DPad Enter=WiiRemote2 Space/Z=A X=1 C=B Backspace=Minus
>> "%LOG%" echo OpenSPM Windows fail-fast capture: enabled
>> "%LOG%" echo OpenSPM renderer symbol map source: %RENDERER_MAP_SOURCE%
>> "%LOG%" echo OpenSPM renderer symbol map snapshot: %RENDERER_MAP%
>> "%LOG%" echo.
"%DEBUG_PY%" %DEBUG_PY_ARGS% "%DEBUG_SCRIPT%" -- "%LAUNCHER%" --game "%GAME%" --graphics %GRAPHICS% >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
>> "%LOG%" echo.
"%DEBUG_PY%" %DEBUG_PY_ARGS% "%SYMBOLIZER%" --log "%LOG%" --map "%RENDERER_MAP%" >> "%LOG%" 2>&1
set "SYMBOL_RC=%ERRORLEVEL%"
if not "%SYMBOL_RC%"=="0" >> "%LOG%" echo [OpenSPMSymbols] symbolizer exit code: %SYMBOL_RC%
>> "%LOG%" echo.
>> "%LOG%" echo OpenSPMNative process exit code: %RC%

echo.
echo OpenSPMNative exit code: %RC%
echo ---- launch.log ----
type "%LOG%"
echo ---- end launch.log ----
exit /b %RC%