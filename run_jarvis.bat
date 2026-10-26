@echo off
cd /d "%~dp0"
py -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installation failed. Fix the error above, then run this file again.
  pause
  exit /b 1
)
echo.
echo Starting J.A.R.V.I.S...
echo Open http://127.0.0.1:5000
py app.py
pause
