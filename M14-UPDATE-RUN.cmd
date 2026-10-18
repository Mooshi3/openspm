@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Run from a temporary worker copy so `git pull` can safely update this file
rem without changing the batch program that cmd.exe is currently executing.
if defined OPENSPM_M14_WORKER goto :worker
set "OPENSPM_M14_ROOT=%~dp0"
set "OPENSPM_M14_WORKER=1"
set "OPENSPM_M14_TEMP=%TEMP%\OpenSPM-M14-Update-Run-%RANDOM%.cmd"
copy /y "%~f0" "%OPENSPM_M14_TEMP%" >nul
if errorlevel 1 (
    echo [OpenSPM] Could not create temporary update-run worker.
    pause
    exit /b 1
)
call "%OPENSPM_M14_TEMP%" %*
set "OPENSPM_M14_RC=%ERRORLEVEL%"
del /q "%OPENSPM_M14_TEMP%" >nul 2>nul
exit /b %OPENSPM_M14_RC%

:worker
cd /d "%OPENSPM_M14_ROOT%"

title OpenSPM M14 Update + Run

set "ROOT=%CD%"
set "CANDIDATE=%CD%\.openspm\m14\windows-candidate"
set "LOCAL_ROOT=%CD%\.openspm\m14\local-module-build"
set "LOCAL_BUILD=!LOCAL_ROOT!\module"
set "STAGED_GENERATED=!LOCAL_ROOT!\stage\generated"
set "STAGED_CPU_RUNTIME=!LOCAL_ROOT!\stage\cpu_runtime"
set "LOCAL_DLL=!LOCAL_BUILD!\openspm_local_native.dll"
set "STAGED_DLL=%CD%\.openspm\m14\windows-candidate\User-OpenSPM\native\openspm_local_native.dll"
set "GRAPHICS=AUTO"
if not "%~1"=="" set "GRAPHICS=%~1"

echo ============================================================
echo OpenSPM M14 - update, incremental build, test, and launch
echo ============================================================
echo Repo: !ROOT!
echo.

set "VSROOT="
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
set "PREFER_VS2022=0"
if exist "!CANDIDATE!\CMakeCache.txt" (
    findstr /I /C:"2022" "!CANDIDATE!\CMakeCache.txt" >nul
    if not errorlevel 1 set "PREFER_VS2022=1"
)
if "!PREFER_VS2022!"=="1" (
    set "VSBASE=%ProgramFiles(x86)%\Microsoft Visual Studio\2022"
    if exist "!VSBASE!\BuildTools\Common7\Tools\VsDevCmd.bat" set "VSROOT=!VSBASE!\BuildTools"
    if not defined VSROOT if exist "!VSBASE!\Community\Common7\Tools\VsDevCmd.bat" set "VSROOT=!VSBASE!\Community"
    if not defined VSROOT if exist "!VSBASE!\Professional\Common7\Tools\VsDevCmd.bat" set "VSROOT=!VSBASE!\Professional"
    if not defined VSROOT if exist "!VSBASE!\Enterprise\Common7\Tools\VsDevCmd.bat" set "VSROOT=!VSBASE!\Enterprise"
)
if not defined VSROOT (
    if exist "!VSWHERE!" (
        set "VSWHERE_OUT=%TEMP%\openspm-vswhere-%RANDOM%.txt"
        "!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath > "!VSWHERE_OUT!"
        set /p "VSROOT=" < "!VSWHERE_OUT!"
        del /q "!VSWHERE_OUT!" >nul 2>nul
    )
)
if not defined VSROOT (
    set "VSBASE=%ProgramFiles(x86)%\Microsoft Visual Studio\2022"
    if exist "!VSBASE!\BuildTools\Common7\Tools\VsDevCmd.bat" set "VSROOT=!VSBASE!\BuildTools"
    if not defined VSROOT if exist "!VSBASE!\Community\Common7\Tools\VsDevCmd.bat" set "VSROOT=!VSBASE!\Community"
    if not defined VSROOT if exist "!VSBASE!\Professional\Common7\Tools\VsDevCmd.bat" set "VSROOT=!VSBASE!\Professional"
    if not defined VSROOT if exist "!VSBASE!\Enterprise\Common7\Tools\VsDevCmd.bat" set "VSROOT=!VSBASE!\Enterprise"
)
if not defined VSROOT (
    echo [OpenSPM] Could not locate Visual Studio C++ Build Tools.
    goto :fail
)
if not exist "!VSROOT!\Common7\Tools\VsDevCmd.bat" (
    echo [OpenSPM] Missing VsDevCmd.bat under:
    echo   !VSROOT!
    goto :fail
)

set "VSCMD_VER="
set "VSCMD_ARG_TGT_ARCH="
set "VSCMD_ARG_HOST_ARCH="
set "VCToolsInstallDir="
set "VCINSTALLDIR="
set "VCToolsVersion="
set "VisualStudioVersion="
set "VSINSTALLDIR="
set "INCLUDE="
set "LIB="
set "LIBPATH="

echo [1/5] Activating consistent Visual Studio x64 environment...
call "!VSROOT!\Common7\Tools\VsDevCmd.bat" -no_logo -arch=x64 -host_arch=x64 >nul
if errorlevel 1 goto :fail

echo [OpenSPM] Visual Studio: !VSINSTALLDIR!
echo [OpenSPM] MSVC tools: !VCToolsInstallDir!
set "ACTIVE_CL="
for /f "delims=" %%I in ('where cl.exe 2^>nul') do if not defined ACTIVE_CL set "ACTIVE_CL=%%I"
if not defined ACTIVE_CL (
    echo [OpenSPM] cl.exe was not found after Visual Studio activation.
    goto :fail
)
echo [OpenSPM] Compiler: !ACTIVE_CL!

set "VCMAKE=!VSROOT!\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
set "VCTEST=!VSROOT!\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\ctest.exe"
set "VSNINJA=!VSROOT!\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja"
if exist "!VSNINJA!\ninja.exe" set "PATH=!VSNINJA!;!PATH!"
if not exist "!VCMAKE!" goto :fail
if not exist "!VCTEST!" goto :fail

where git.exe >nul 2>nul
if errorlevel 1 (
    if exist "%ProgramFiles%\Git\cmd\git.exe" (
        set "PATH=%ProgramFiles%\Git\cmd;!PATH!"
    ) else (
        echo [OpenSPM] git.exe was not found on PATH.
        goto :fail
    )
)

for /f "delims=" %%I in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "BRANCH=%%I"
if /I not "!BRANCH!"=="m14-native-execution" (
    echo [OpenSPM] Wrong branch: !BRANCH!
    echo Expected: m14-native-execution
    goto :fail
)

echo.
if defined OPENSPM_M14_POSTPULL (
    echo [2/5] Latest M14 changes already pulled; using refreshed runner.
) else (
    echo [2/5] Pulling latest M14 changes...
    git pull --ff-only
    if errorlevel 1 goto :fail
    set "OPENSPM_M14_POSTPULL=1"
    set "OPENSPM_M14_REFRESH=%TEMP%\OpenSPM-M14-PostPull-%RANDOM%.cmd"
    copy /y "!ROOT!\M14-UPDATE-RUN.cmd" "!OPENSPM_M14_REFRESH!" >nul
    if errorlevel 1 goto :fail
    echo [OpenSPM] Continuing with freshly pulled runner...
    call "!OPENSPM_M14_REFRESH!" %*
    set "RC=!ERRORLEVEL!"
    del /q "!OPENSPM_M14_REFRESH!" >nul 2>nul
    exit /b !RC!
)

if not exist "!CANDIDATE!\build.ninja" (
    echo [OpenSPM] The local M14 candidate has not been configured yet.
    echo Run RUN-M14-HOME-TEST.cmd once to create it.
    goto :fail
)

echo.
echo [3/5] Incrementally rebuilding native launcher + backend + renderer + M14 contract tests...
"!VCMAKE!" --build "!CANDIDATE!" --target OpenSPMNative openspm_native_backend openspm_renderer m14_wii_platform_test m14_gx_platform_test m14_gx_renderer_service_test m14_exi_diagnostic_test -j2
if errorlevel 1 goto :fail
"!VCTEST!" --test-dir "!CANDIDATE!" --output-on-failure -R "^m14_(wii_platform|gx_(platform|renderer_service)|exi_diagnostic)$"
if errorlevel 1 goto :fail

echo.
echo [4/5] Normalizing + checking player-local native module...
if not exist "!LOCAL_BUILD!\build.ninja" (
    echo [OpenSPM] No incremental local-module build tree was found.
    echo Run RUN-M14-HOME-TEST.cmd once to recreate the player-local build cache.
    goto :fail
)
if not exist "!STAGED_GENERATED!\generated.h" (
    echo [OpenSPM] Cached player-local generated source is missing:
    echo   !STAGED_GENERATED!
    goto :fail
)

set "PYTHON_EXE="
set "PYTHON_ARGS="
for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE if exist "C:\Python314\python.exe" set "PYTHON_EXE=C:\Python314\python.exe"
if not defined PYTHON_EXE (
    where py.exe >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_EXE=py.exe"
        set "PYTHON_ARGS=-3"
    )
)
if not defined PYTHON_EXE (
    echo [OpenSPM] Python 3 is required to refresh cached host boundaries.
    goto :fail
)

"!PYTHON_EXE!" !PYTHON_ARGS! "!ROOT!\tools\native_bridge\decode_exi_diagnostic.py" --self-test
if errorlevel 1 goto :fail
"!PYTHON_EXE!" !PYTHON_ARGS! "!ROOT!\tools\native_bridge\test_windows_debug_heap.py"
if errorlevel 1 goto :fail
"!PYTHON_EXE!" !PYTHON_ARGS! "!ROOT!\tools\native_bridge\test_exi_diagnostic_contract.py"
if errorlevel 1 goto :fail
"!PYTHON_EXE!" !PYTHON_ARGS! "!ROOT!\tools\native_bridge\test_host_target_sync.py"
if errorlevel 1 goto :fail
"!PYTHON_EXE!" !PYTHON_ARGS! "!ROOT!\tools\native_bridge\test_display_list_native_calls.py"
if errorlevel 1 goto :fail
"!PYTHON_EXE!" !PYTHON_ARGS! "!ROOT!\tools\native_bridge\test_direct_calls.py"
if errorlevel 1 goto :fail
"!PYTHON_EXE!" !PYTHON_ARGS! "!ROOT!\tools\native_bridge\renormalize_staged.py" --generated "!STAGED_GENERATED!"
if errorlevel 1 goto :fail

rem Migrate old incremental caches away from compiling the user's DolRecomp
rem runtime in place. Refresh a private cpu/common copy, apply the exact WPAR
rem direct-store hook plus generated-chunk inline MEM1/MEM2/PSQ0 header path,
rem then repoint the existing CMake tree. The user's DolRecomp checkout remains
rem immutable.
"!PYTHON_EXE!" !PYTHON_ARGS! "!ROOT!\tools\native_bridge\refresh_staged_cpu_runtime.py" --cmake-cache "!LOCAL_BUILD!\CMakeCache.txt" --destination "!STAGED_CPU_RUNTIME!"
if errorlevel 1 goto :fail
"!VCMAKE!" -S "!ROOT!\tools\native_bridge" -B "!LOCAL_BUILD!" -DOPENSPM_CPU_RUNTIME_DIR="!STAGED_CPU_RUNTIME!"
if errorlevel 1 goto :fail

"!VCMAKE!" --build "!LOCAL_BUILD!" --target openspm_local_native -j2
if errorlevel 1 goto :fail
if not exist "!LOCAL_DLL!" goto :fail
if not exist "!CANDIDATE!\User-OpenSPM\native" mkdir "!CANDIDATE!\User-OpenSPM\native"
copy /y "!LOCAL_DLL!" "!STAGED_DLL!" >nul
if errorlevel 1 goto :fail
echo [OpenSPM] Player-local module is normalized, current, and staged.

echo.
echo [5/5] Launching strict-native candidate...
set "OPENSPM_REPO_REVISION=unknown"
for /f "delims=" %%I in ('git rev-parse --short=12 HEAD 2^>nul') do set "OPENSPM_REPO_REVISION=%%I"
echo [OpenSPM] Candidate revision: !OPENSPM_REPO_REVISION!
call "!ROOT!\LAUNCH-M14-CANDIDATE.cmd" "!GRAPHICS!"
set "RC=!ERRORLEVEL!"
echo.
if exist "!ROOT!\.openspm\m14\home-test\launch.log" (
    "!PYTHON_EXE!" !PYTHON_ARGS! "!ROOT!\tools\native_bridge\decode_exi_diagnostic.py" --log "!ROOT!\.openspm\m14\home-test\launch.log"
    echo.
)
if "!RC!"=="0" (
    echo [OpenSPM] Candidate exited successfully.
) else (
    echo [OpenSPM] Candidate exited with code !RC!.
    echo The runtime log is:
    echo   !ROOT!\.openspm\m14\home-test\launch.log
)
goto :finish

:fail
set "RC=1"
echo.
echo [OpenSPM] Update/run stopped before launch.

:finish
echo.
echo You can close this window. Press any key to leave it open long enough to read.
pause >nul
exit /b !RC!
