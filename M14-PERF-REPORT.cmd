@echo off
setlocal
set "ROOT=%~dp0"
set "PY="
for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PY set "PY=%%I"
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"

rem Keep analyzer execution outside parenthesized command blocks. cmd.exe expands
rem %%ERRORLEVEL%% when a block is parsed, which can otherwise return the status
rem from the preceding command instead of the Python analyzer itself.
if not defined PY goto :try_py_launcher
"%PY%" "%ROOT%tools\native_bridge\analyze_m14_perf.py" %*
exit /b %errorlevel%

:try_py_launcher
where py.exe >nul 2>nul
if errorlevel 1 goto :missing_python
py.exe -3 "%ROOT%tools\native_bridge\analyze_m14_perf.py" %*
exit /b %errorlevel%

:missing_python
echo [OpenSPM] Python 3 was not found.
exit /b 1
