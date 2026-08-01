@echo off
setlocal
cd /d "%~dp0"
title TonerSaverPDF Installer

echo Checking Python...
set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if defined PYTHON_CMD goto :python_found

where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"
if defined PYTHON_CMD goto :python_found

echo.
echo ERROR: Python 3 was not found.
echo Install Python 3.11 or newer and enable "Add Python to PATH".
pause
exit /b 1

:python_found
echo Creating virtual environment...
%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto :error

echo Installing required packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Installation completed successfully.
echo Double-click run.bat to start TonerSaverPDF.
pause
exit /b 0

:error
echo.
echo ERROR: Installation failed.
echo Check your internet connection and Python installation.
echo You can copy the error text and send it for troubleshooting.
pause
exit /b 1
