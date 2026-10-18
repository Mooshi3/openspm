@echo off
rem OpenSPM test launcher: a small window that starts M14-VANILLA-RUN.cmd with
rem the flags and settings of each test in docs/TEST-LIST.md (details in
rem tools\test_launcher\openspm_test_launcher.py). Double-click to start.
setlocal
set "PYTHON_EXE="
for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE if exist "C:\Python314\python.exe" set "PYTHON_EXE=C:\Python314\python.exe"
if not defined PYTHON_EXE (
    where py.exe >nul 2>nul && set "PYTHON_EXE=py.exe"
)
if not defined PYTHON_EXE (
    echo [OpenSPM] Python 3 was not found. Install it from python.org ^(keep "tcl/tk" ticked^).
    pause
    exit /b 1
)
"%PYTHON_EXE%" "%~dp0tools\test_launcher\openspm_test_launcher.py"
if errorlevel 1 (
    echo.
    echo [OpenSPM] The test launcher stopped with an error ^(see above^).
    echo [OpenSPM] "No module named tkinter": re-run the Python installer, Modify, tick "tcl/tk and IDLE".
    pause
)
