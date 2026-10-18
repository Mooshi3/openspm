@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"

rem Presentation-only diagnostic A/B.  Guest simulation, LLVM state-memory,
rem chunk geometry and renderer command semantics are unchanged.
set "OPENSPM_M14_VSYNC=0"

echo [OpenSPM] Launching dev116 presentation A/B with Aurora vsync OFF.
echo [OpenSPM] Pinned Aurora will prefer Mailbox on non-Metal when supported,
echo [OpenSPM] then Immediate, and falls back to Fifo if neither is available.
echo [OpenSPM] Normal M14-LLVM-UPDATE-RUN.cmd remains vsync ON.
echo.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0
exit /b %errorlevel%
