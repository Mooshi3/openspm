@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Experimental LLVM-native A/B runner. This is deliberately separate from
rem M14-UPDATE-RUN.cmd: the known-good C player module remains the default and
rem this script restores it after every LLVM launch, including crash exits.
if defined OPENSPM_M14_LLVM_WORKER goto :worker
set "OPENSPM_M14_LLVM_ROOT=%~dp0"
set "OPENSPM_M14_LLVM_WORKER=1"
set "OPENSPM_M14_LLVM_TEMP=%TEMP%\OpenSPM-M14-LLVM-Update-Run-%RANDOM%.cmd"
copy /y "%~f0" "%OPENSPM_M14_LLVM_TEMP%" >nul
if errorlevel 1 (
    echo [OpenSPM] Could not create temporary LLVM update-run worker.
    pause
    exit /b 1
)
call "%OPENSPM_M14_LLVM_TEMP%" %*
set "OPENSPM_M14_LLVM_RC=%ERRORLEVEL%"
del /q "%OPENSPM_M14_LLVM_TEMP%" >nul 2>nul
exit /b %OPENSPM_M14_LLVM_RC%

:worker
cd /d "%OPENSPM_M14_LLVM_ROOT%"
title OpenSPM M14 LLVM Native Candidate

set "ROOT=%CD%"
set "GRAPHICS=AUTO"
if not "%~1"=="" set "GRAPHICS=%~1"
set "LLVM_CHUNK=256"
if not "%~2"=="" set "LLVM_CHUNK=%~2"
if /I not "!LLVM_CHUNK!"=="128" if /I not "!LLVM_CHUNK!"=="256" if /I not "!LLVM_CHUNK!"=="512" (
    echo [OpenSPM] Invalid LLVM chunk size: !LLVM_CHUNK!
    echo [OpenSPM] Supported A/B values are 128, 256, and experimental 512.
    goto :fail
)
set "LLVM_STATE_MEM=1"
if not "%~3"=="" set "LLVM_STATE_MEM=%~3"
if /I not "!LLVM_STATE_MEM!"=="0" if /I not "!LLVM_STATE_MEM!"=="1" (
    echo [OpenSPM] Invalid LLVM state-memory mode: !LLVM_STATE_MEM!
    echo [OpenSPM] Supported A/B values are 0 ^(off^) and 1 ^(on^).
    goto :fail
)
set "LLVM_HEAVY_DIAG=0"
if not "%~4"=="" set "LLVM_HEAVY_DIAG=%~4"
if /I not "!LLVM_HEAVY_DIAG!"=="0" if /I not "!LLVM_HEAVY_DIAG!"=="1" (
    echo [OpenSPM] Invalid LLVM heavy-diagnostics mode: !LLVM_HEAVY_DIAG!
    echo [OpenSPM] Supported values are 0 ^(normal/release^) and 1 ^(profiling^).
    goto :fail
)
set "LLVM_TOOLCHAIN=msvc"
if not "%~5"=="" set "LLVM_TOOLCHAIN=%~5"
if /I not "!LLVM_TOOLCHAIN!"=="msvc" if /I not "!LLVM_TOOLCHAIN!"=="clang" if /I not "!LLVM_TOOLCHAIN!"=="clang-freestanding" (
    echo [OpenSPM] Invalid native-module toolchain: !LLVM_TOOLCHAIN!
    echo [OpenSPM] Supported values are msvc ^(default^), clang and clang-freestanding.
    goto :fail
)
echo ============================================================
echo OpenSPM M14 - LLVM-native transactional A/B
echo ============================================================
echo Repo: !ROOT!
echo Runtime: OpenSPM standalone native only
echo Compiler: DolRecomp LLVM ^(build-time only^)
echo Safety: known-good C DLL restored after every launch
echo LLVM chunk ceiling: !LLVM_CHUNK! instructions
echo LLVM state-in-memory: !LLVM_STATE_MEM!
echo LLVM heavy diagnostics: !LLVM_HEAVY_DIAG!
echo Native module toolchain: !LLVM_TOOLCHAIN!
echo.

set "VSROOT="
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
set "CANDIDATE=!ROOT!\.openspm\m14\windows-candidate"
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
if not defined VSROOT if exist "!VSWHERE!" (
    set "VSWHERE_OUT=%TEMP%\openspm-llvm-vswhere-%RANDOM%.txt"
    "!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath > "!VSWHERE_OUT!"
    set /p "VSROOT=" < "!VSWHERE_OUT!"
    del /q "!VSWHERE_OUT!" >nul 2>nul
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
if not exist "!VSROOT!\Common7\Tools\VsDevCmd.bat" goto :fail

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

echo [1/4] Activating Visual Studio x64 environment...
call "!VSROOT!\Common7\Tools\VsDevCmd.bat" -no_logo -arch=x64 -host_arch=x64 >nul
if errorlevel 1 goto :fail
where cl.exe >nul 2>nul
if errorlevel 1 (
    echo [OpenSPM] cl.exe was not found after Visual Studio activation.
    goto :fail
)

set "VCMAKE=!VSROOT!\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
set "VCTEST=!VSROOT!\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\ctest.exe"
set "VSNINJA=!VSROOT!\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja"
if exist "!VSNINJA!\ninja.exe" set "PATH=!VSNINJA!;!PATH!"
if not exist "!VCMAKE!" (
    echo [OpenSPM] Visual Studio CMake was not found: !VCMAKE!
    goto :fail
)
if not exist "!VCTEST!" (
    echo [OpenSPM] Visual Studio CTest was not found: !VCTEST!
    goto :fail
)

where git.exe >nul 2>nul
if errorlevel 1 (
    if exist "%ProgramFiles%\Git\cmd\git.exe" (
        set "PATH=%ProgramFiles%\Git\cmd;!PATH!"
    ) else (
        echo [OpenSPM] git.exe was not found on PATH.
        goto :fail
    )
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
    echo [OpenSPM] Python 3 is required for the LLVM-native local build.
    goto :fail
)

for /f "delims=" %%I in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "BRANCH=%%I"
if /I not "!BRANCH!"=="m14-native-execution" (
    echo [OpenSPM] Wrong branch: !BRANCH!
    echo Expected: m14-native-execution
    goto :fail
)

echo.
echo [2/4] Synchronizing exact m14-native-execution remote head...
set "HEAD_BEFORE="
set "HEAD_AFTER="
set "REMOTE_HEAD="
for /f "delims=" %%I in ('git rev-parse HEAD 2^>nul') do set "HEAD_BEFORE=%%I"
if not defined HEAD_BEFORE (
    echo [OpenSPM] Could not resolve local HEAD before synchronization.
    goto :fail
)
git fetch origin m14-native-execution
if errorlevel 1 goto :fail
for /f "delims=" %%I in ('git rev-parse origin/m14-native-execution 2^>nul') do set "REMOTE_HEAD=%%I"
if not defined REMOTE_HEAD (
    echo [OpenSPM] Could not resolve origin/m14-native-execution after fetch.
    goto :fail
)
git merge --ff-only origin/m14-native-execution
if errorlevel 1 goto :fail
for /f "delims=" %%I in ('git rev-parse HEAD 2^>nul') do set "HEAD_AFTER=%%I"
if not defined HEAD_AFTER (
    echo [OpenSPM] Could not resolve local HEAD after synchronization.
    goto :fail
)
if /I not "!HEAD_AFTER!"=="!REMOTE_HEAD!" (
    echo [OpenSPM] Refusing stale LLVM run: local HEAD does not equal fetched remote head.
    echo Local:  !HEAD_AFTER!
    echo Remote: !REMOTE_HEAD!
    goto :fail
)
echo [OpenSPM] LLVM source checkout verified at !HEAD_AFTER!.

rem This worker is a temporary copy made before synchronization. If Git advanced
rem the checkout, restart exactly once from the newly fetched script. Do not use
rem a persistent environment 'already pulled' flag: an interrupted Ctrl+C run can
rem leave that flag alive in the parent shell and silently launch stale bridge code.
if /I not "!HEAD_BEFORE!"=="!HEAD_AFTER!" (
    set "OPENSPM_M14_LLVM_REFRESH=%TEMP%\OpenSPM-M14-LLVM-PostPull-%RANDOM%.cmd"
    copy /y "!ROOT!\M14-LLVM-UPDATE-RUN.cmd" "!OPENSPM_M14_LLVM_REFRESH!" >nul
    if errorlevel 1 goto :fail
    echo [OpenSPM] Checkout advanced; continuing with freshly fetched LLVM runner...
    call "!OPENSPM_M14_LLVM_REFRESH!" %*
    set "RC=!ERRORLEVEL!"
    del /q "!OPENSPM_M14_LLVM_REFRESH!" >nul 2>nul
    exit /b !RC!
)

if not exist "!CANDIDATE!\build.ninja" (
    echo [OpenSPM] The normal M14 candidate cache is missing.
    echo [OpenSPM] Run RUN-M14-HOME-TEST.cmd once before the LLVM A/B.
    goto :fail
)

echo.
echo [3/4] Building isolated LLVM-native player module and running one-shot A/B...
"!PYTHON_EXE!" !PYTHON_ARGS! "!ROOT!\tools\native_bridge\run_m14_llvm_candidate.py" ^
    --root "!ROOT!" ^
    --cmake "!VCMAKE!" ^
    --ctest "!VCTEST!" ^
    --git git.exe ^
    --graphics "!GRAPHICS!" ^
    --chunk-instructions "!LLVM_CHUNK!" ^
    --state-in-memory-mode "!LLVM_STATE_MEM!" ^
    --heavy-diagnostics-mode "!LLVM_HEAVY_DIAG!" ^
    --toolchain "!LLVM_TOOLCHAIN!"
set "RC=!ERRORLEVEL!"

echo.
echo [4/4] Transaction finished.
if "!RC!"=="0" (
    echo [OpenSPM] LLVM candidate exited normally. The known-good C DLL has been restored.
) else (
    echo [OpenSPM] LLVM candidate/build exited with code !RC!.
    echo [OpenSPM] The known-good C DLL is still restored if staging was reached.
)
if exist "!ROOT!\.openspm\m14\home-test\launch.log" (
    echo [OpenSPM] Runtime log:
    echo   !ROOT!\.openspm\m14\home-test\launch.log
)
goto :finish

:fail
set "RC=1"
echo.
echo [OpenSPM] LLVM update/run stopped before launch.

goto :finish

:finish
echo.
echo You can close this window. Press any key to leave it open long enough to read.
pause >nul
exit /b !RC!
