@echo off
REM GameWalk PC helper.
REM
REM   run.bat              hold real keys (what you want while playing)
REM   run.bat --dry-run    log keystrokes instead of pressing them
REM   run.bat --pair       open a pairing window for a new phone

setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python was not found on your PATH.
    echo Install Python 3.10 or newer from https://python.org and try again.
    pause
    exit /b 1
)

python -m pc %*
set EXITCODE=%ERRORLEVEL%

REM Only pause on failure. Pausing after a normal Ctrl-C quit means an extra
REM keypress every single time you stop playing.
if not "%EXITCODE%"=="0" (
    echo.
    echo Helper exited with code %EXITCODE%.
    pause
)
exit /b %EXITCODE%
