@echo off
REM ====================================================================
REM   No-Server Mode — Discord screen-watcher launcher
REM ====================================================================
setlocal
cd /d "%~dp0"
echo [setup] Making sure uiautomation is installed...
python -m pip install --quiet --disable-pip-version-check uiautomation
echo.
echo Starting the watcher. First run will ask for your token.
echo (Get it from the booth: open your No-Server session, click "Connect watcher".)
echo.
python dj-watcher.py
pause
