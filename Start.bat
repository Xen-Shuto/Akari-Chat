@echo off
setlocal
chcp 65001 >nul
title Akari-Chat
set PYTHONIOENCODING=utf-8
rem Y/y skips saved model auto-start; N uses the saved auto-start settings.
set "NO_AUTO_START=N"
rem Browser UI port (default: 8765).
set "PORT=8765"

set "AUTO_START_OPTION="
if /I "%NO_AUTO_START%"=="Y" set "AUTO_START_OPTION=--no-auto-start"
if not defined PORT set "PORT=8765"

cd /d "%~dp0"
if exist "app\runtime\python.exe" (
  "app\runtime\python.exe" -u "app\launch.py" --startup-splash --port "%PORT%" %AUTO_START_OPTION% %*
) else (
  python -u "app\launch.py" --startup-splash --port "%PORT%" %AUTO_START_OPTION% %*
)
if errorlevel 1 pause
