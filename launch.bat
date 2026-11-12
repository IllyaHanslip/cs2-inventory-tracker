@echo off
rem ---------------------------------------------------------------------------
rem  CS2 Inventory Intelligence - one-click launcher (used by the desktop shortcut)
rem
rem  * First run: creates a private Python environment (.venv) and installs the
rem    packages from requirements.txt. Re-installs automatically if that file changes.
rem  * Starts the local server in its own minimised window and opens the dashboard.
rem    Close that window to stop the app.
rem  * Extra arguments go to app.py, e.g.   launch.bat --demo
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title CS2 Inventory Intelligence - starting
set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo First run: creating a private Python environment in .venv ...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
    if not exist "%VENV_PY%" goto :no_python
)

rem Install packages when requirements.txt differs from the copy saved at the last install.
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if errorlevel 1 (
    echo Installing the required Python packages - this can take a minute or two...
    call :notice "Setting up for first use - installing Python packages. The dashboard opens by itself when it's ready (usually 1-2 minutes)."
    "%VENV_PY%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :install_failed
    copy /y requirements.txt ".venv\requirements.installed" >nul
)

start "CS2 Inventory Intelligence - close this window to stop" /min "%VENV_PY%" app.py --open %*
exit /b 0

:no_python
call :problem "Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run the shortcut again."
exit /b 1

:install_failed
call :problem "Installing the required Python packages failed. Check your internet connection, then run the shortcut again."
exit /b 1

rem The shortcut starts this window minimised, so messages are also shown as dialog boxes.
:notice
set "CS2_MSG=%~1"
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show($env:CS2_MSG, 'CS2 Inventory Intelligence')"
exit /b

:problem
echo.
echo %~1
echo.
set "CS2_MSG=%~1"
powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show($env:CS2_MSG, 'CS2 Inventory Intelligence', 'OK', 'Error')"
exit /b
