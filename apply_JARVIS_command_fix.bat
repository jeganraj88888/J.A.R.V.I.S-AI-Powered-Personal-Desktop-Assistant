@echo off
setlocal
cd /d "%~dp0"
echo ==========================================
echo J.A.R.V.I.S Command Bug Fix
echo ==========================================
if not exist "app.py" (
  echo ERROR: app.py not found.
  pause
  exit /b 1
)
if not exist "index.html" (
  echo ERROR: index.html not found.
  pause
  exit /b 1
)

copy /Y "app.py" "app_before_command_fix.py" >nul
copy /Y "index.html" "index_before_command_fix.html" >nul

echo.
echo This helper cannot safely perform arbitrary source replacement by itself.
echo Use the two fixed files supplied with this package:
echo   app(3)_COMMAND_FIXED.py
echo   index(9)_COMMAND_FIXED.html
echo.
echo Copy them into this folder as:
echo   app.py
echo   index.html
echo.
echo Backups were created:
echo   app_before_command_fix.py
echo   index_before_command_fix.html
echo.
pause
