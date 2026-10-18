@echo off
setlocal
set "ROOT=%~dp0"

echo ============================================================
echo OpenSPM M14 - Yold Town live NPC group threshold sweep
echo ============================================================
echo No native rebuild is required.
echo This uses the current running game's safe-frame read32/write32 console.
echo.
echo Stand in Yold Town / he2_03 and leave Mario mostly still.
echo The room reloads between group sizes to reset NPC placement.
echo.

call "%ROOT%M14-DEBUG-CONSOLE.cmd" --script "%ROOT%tools\native_bridge\experiments\yold_npc_group_sweep.spmdbg"
if errorlevel 1 (
    echo.
    echo [OpenSPM] Group sweep failed or was stopped; report not generated.
    exit /b 1
)

echo.
echo [OpenSPM] Yold NPC group threshold report:
set "PY="
for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PY set "PY=%%I"
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"

if defined PY (
    "%PY%" "%ROOT%tools\native_bridge\analyze_yold_npc_groups.py"
    if errorlevel 1 exit /b 1
    exit /b 0
)

where py.exe >nul 2>nul
if not errorlevel 1 (
    py.exe -3 "%ROOT%tools\native_bridge\analyze_yold_npc_groups.py"
    if errorlevel 1 exit /b 1
    exit /b 0
)

echo [OpenSPM] Python 3 was not found for the group report.
exit /b 1
