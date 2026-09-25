@echo off
title Options Quant Dashboard
cd /d "%~dp0"

rem Use "python", or the "py" launcher if "python" is not on PATH.
set PY=python
python --version >nul 2>&1 || set PY=py
%PY% --version >nul 2>&1 || (
  echo Python was not found. Install it from https://www.python.org/downloads/ and tick "Add to PATH".
  pause
  exit /b 1
)

echo Checking libraries (the first run can take a few minutes)...
%PY% -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo Installing the libraries failed. See the message above.
  pause
  exit /b 1
)

echo Starting the dashboard. It opens in your browser at http://127.0.0.1:8050
echo Close this window (or press Ctrl+C) to stop it.
%PY% run_dashboard.py %*
pause
