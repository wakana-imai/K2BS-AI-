@echo off
setlocal
cd /d "%~dp0"
py -3 --version >nul 2>&1
if errorlevel 1 (
 echo Python is not installed. Install Python 3.12 or newer from https://www.python.org/downloads/windows/
 echo Then reopen this file.
 pause
 exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
 py -3 -m venv .venv
 if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
if not exist "data\password.hash" (
 .venv\Scripts\python.exe app.py set-password
 if errorlevel 1 goto failed
)
.venv\Scripts\python.exe windows_launcher.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Keep this window open and share the error message without passwords or patient information.
pause
exit /b 1
