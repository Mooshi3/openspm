@echo off
setlocal
set "ROOT=%~dp0"

echo ============================================================
echo OpenSPM M14 - Yold Town live one-NPC performance sweep
echo ============================================================
echo Requires the dev30 live-console backend once.
echo After that, this sweep itself needs NO rebuild between actors.
echo.
echo Stand in Yold Town / he2_03 and leave Mario mostly still.
echo The script checks the exact seven-townsperson population before starting.
echo.

call "%ROOT%M14-DEBUG-CONSOLE.cmd" --script "%ROOT%tools\native_bridge\experiments\yold_npc_active_sweep.spmdbg"
if errorlevel 1 (
    echo.
    echo [OpenSPM] NPC sweep failed or was stopped; report not generated.
    exit /b %errorlevel%
)

echo.
echo [OpenSPM] Yold one-NPC sweep report:
set "PY="
for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PY set "PY=%%I"
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"

if defined PY (
    "%PY%" "%ROOT%tools\native_bridge\analyze_yold_npc_sweep.py"
    exit /b %errorlevel%
)

where py.exe >nul 2>nul
if not errorlevel 1 (
    py.exe -3 "%ROOT%tools\native_bridge\analyze_yold_npc_sweep.py"
    exit /b %errorlevel%
)

echo [OpenSPM] Python 3 was not found for the sweep report.
exit /b 1
