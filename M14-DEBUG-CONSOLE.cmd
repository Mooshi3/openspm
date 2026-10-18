@echo off
setlocal
set "ROOT=%~dp0"
set "PY="
set "MODE=cli"

if "%~1"=="" set "MODE=gui"
if /I "%~1"=="--gui" set "MODE=gui"

for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PY set "PY=%%I"
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"

if defined PY (
    if /I "%MODE%"=="gui" (
        "%PY%" "%ROOT%tools\native_bridge\m14_debug_gui.py"
    ) else (
        "%PY%" "%ROOT%tools\native_bridge\m14_debug_console.py" %*
    )
    if errorlevel 1 exit /b 1
    exit /b 0
)

where py.exe >nul 2>nul
if not errorlevel 1 (
    if /I "%MODE%"=="gui" (
        py.exe -3 "%ROOT%tools\native_bridge\m14_debug_gui.py"
    ) else (
        py.exe -3 "%ROOT%tools\native_bridge\m14_debug_console.py" %*
    )
    if errorlevel 1 exit /b 1
    exit /b 0
)

echo [OpenSPM] Python 3 was not found.
exit /b 1
