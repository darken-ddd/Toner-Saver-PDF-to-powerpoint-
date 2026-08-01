@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo TonerSaverPDF is not installed yet.
    echo Run install.bat first.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "%~dp0app.py"
exit /b 0
