@echo off
REM GameWalk.
REM
REM   run.bat                 open the window
REM   run.bat --dry-run       open it, but never actually press keys
REM   run.bat --console       the old text-only view
REM
setlocal
cd /d "%~dp0"

REM pythonw has no console window attached, which is what makes this look like
REM an app rather than a script. Crashes are written to gamewalk-error.log and
REM shown in a dialog, so nothing is lost by hiding the console.
set LAUNCHER=pythonw
where pythonw >nul 2>&1 || set LAUNCHER=python

where %LAUNCHER% >nul 2>&1
if errorlevel 1 (
    echo Python was not found on your PATH.
    echo Install Python 3.10 or newer from https://python.org and try again.
    pause
    exit /b 1
)

REM --console runs in this window; anything else opens the GUI detached.
echo %* | find /i "--console" >nul
if not errorlevel 1 (
    python -m pc %*
    exit /b %ERRORLEVEL%
)

start "GameWalk" %LAUNCHER% -m pc --gui %*
exit /b 0
