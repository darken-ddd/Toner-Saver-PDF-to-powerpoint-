@echo off
setlocal
cd /d "%~dp0"
title TonerSaverPDF EXE Builder

set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if defined PYTHON_CMD goto :python_found
where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"
if not defined PYTHON_CMD goto :no_python

:python_found
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto :error
)

echo Installing build packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto :error

echo Building EXE...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "TonerSaverPDF" ^
  --collect-all pymupdf ^
  app.py
if errorlevel 1 goto :error

echo.
echo EXE created successfully:
echo dist\TonerSaverPDF.exe
pause
exit /b 0

:no_python
echo ERROR: Python 3 was not found.
echo Install Python 3.11 or newer and enable "Add Python to PATH".
pause
exit /b 1

:error
echo.
echo ERROR: The EXE build failed.
pause
exit /b 1
