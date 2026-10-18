@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where cl.exe >nul 2>nul
if errorlevel 1 (
    set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
    if not exist "%VSWHERE%" (
        echo [OpenSPM] MSVC build tools are not active and vswhere.exe was not found.
        echo Install Visual Studio 2022 Build Tools with "Desktop development with C++",
        echo or run RUN-M14-HOME-TEST.ps1 from an x64 Native Tools command prompt.
        pause
        exit /b 1
    )

    set "VSROOT="
    for /f "usebackq tokens=*" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%I"
    if not defined VSROOT (
        echo [OpenSPM] Visual Studio C++ build tools were not found.
        echo Install the "Desktop development with C++" workload and rerun this file.
        pause
        exit /b 1
    )

    if not exist "%VSROOT%\Common7\Tools\VsDevCmd.bat" (
        echo [OpenSPM] VsDevCmd.bat is missing from: %VSROOT%
        pause
        exit /b 1
    )

    echo [OpenSPM] Entering Visual Studio x64 developer environment...
    call "%VSROOT%\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64 >nul
    if errorlevel 1 (
        echo [OpenSPM] Could not initialize the Visual Studio developer environment.
        pause
        exit /b 1
    )
)

rem If the caller was already in an x64 Native Tools prompt, VSROOT above was not
rem populated, but vcvarsall provides VSINSTALLDIR.
if not defined VSROOT if defined VSINSTALLDIR set "VSROOT=%VSINSTALLDIR%"

rem Prefer a native Windows CMake/CTest ahead of devkitPro/MSYS. Some Build Tools
rem installations do not include Visual Studio's optional CMake component, while
rem a normal system CMake is installed under Program Files.
set "OPENSPM_NATIVE_CMAKE_BIN="
if defined VSROOT (
    set "VSCMAKE=%VSROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"
    if exist "%VSROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" set "OPENSPM_NATIVE_CMAKE_BIN=%VSROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"
)
if not defined OPENSPM_NATIVE_CMAKE_BIN if exist "%ProgramFiles%\CMake\bin\cmake.exe" set "OPENSPM_NATIVE_CMAKE_BIN=%ProgramFiles%\CMake\bin"
if defined OPENSPM_NATIVE_CMAKE_BIN set "PATH=%OPENSPM_NATIVE_CMAKE_BIN%;%PATH%"

rem Prefer Visual Studio's native Ninja too when that optional component exists.
if defined VSROOT (
    set "VSNINJA=%VSROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja"
    if exist "%VSROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe" set "PATH=%VSROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
)

where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo [OpenSPM] powershell.exe was not found.
    pause
    exit /b 1
)

rem PowerShell 5 promotes native stderr records to terminating errors when the
rem home-test script uses ErrorActionPreference=Stop. Aurora logs normal info to
rem stderr, so always let PowerShell stop after candidate construction and use
rem the cmd.exe launch wrapper for the actual runtime. Preserve the caller's
rem -NoLaunch choice and -Graphics selection.
set "OPENSPM_HOME_NO_LAUNCH=0"
set "OPENSPM_HOME_GRAPHICS=AUTO"
call :ParseArgs %*

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN-M14-HOME-TEST.ps1" %* -NoLaunch
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" goto :finish
if "%OPENSPM_HOME_NO_LAUNCH%"=="1" goto :finish

echo.
echo [OpenSPM] Candidate built successfully. Launching through cmd.exe so Aurora stderr is logged normally.
call "%~dp0LAUNCH-M14-CANDIDATE.cmd" "%OPENSPM_HOME_GRAPHICS%"
set "RC=%ERRORLEVEL%"

goto :finish

:ParseArgs
if "%~1"=="" exit /b 0
if /I "%~1"=="-NoLaunch" set "OPENSPM_HOME_NO_LAUNCH=1"
if /I "%~1"=="-Graphics" (
    if not "%~2"=="" set "OPENSPM_HOME_GRAPHICS=%~2"
    shift
)
shift
goto :ParseArgs

:finish
echo.
if "%RC%"=="0" (
    echo [OpenSPM] Home-test script finished successfully.
) else (
    echo [OpenSPM] Home-test script exited with code %RC%.
    echo If last-run.txt or launch.log exists, send it.
    echo If neither exists, the failure happened before candidate launch; send the
    echo console output beginning at the first FAILED: or error line instead.
)
pause
exit /b %RC%
