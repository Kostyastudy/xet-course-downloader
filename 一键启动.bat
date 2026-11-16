@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

where python >nul 2>nul
if errorlevel 1 (
  where py >nul 2>nul
  if errorlevel 1 (
    echo.
    echo [ERROR] Python not found.
    echo         Install Python 3.8+ from https://www.python.org/downloads/
    echo         and tick "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
  )
  py -3 start.py %*
) else (
  python start.py %*
)

echo.
pause
