@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem Match the immediately preceding chunk256 presentation A/B exactly so the
rem only intended variable is LLVM generic chunk geometry.
set "OPENSPM_M14_VSYNC=0"

echo [OpenSPM] Launching RETIRED/DIAGNOSTIC LLVM chunk512 A/B.
echo [OpenSPM] Player A/B on 2026-09-20 measured chunk512 slightly worse than chunk256.
echo [OpenSPM] Use M14-LLVM-DEV117-RUN.cmd for the active callback-walker candidate.
echo [OpenSPM] This launcher remains only for reproducibility; state_mem=1, heavy_diag=0,
echo [OpenSPM] Mailbox A/B and all guest/render semantics remain unchanged.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 512 1 0
exit /b %errorlevel%
