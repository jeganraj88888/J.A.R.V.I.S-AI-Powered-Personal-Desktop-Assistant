\
@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo   J.A.R.V.I.S - RESTORE ORIGINAL HUD
 echo ================================================
if exist app.py copy /Y app.py app.py.before_restore.bak >nul
if exist index.html copy /Y index.html index.html.before_restore.bak >nul
if not exist index.html (
  echo ERROR: index.html is missing.
  pause
  exit /b 1
)
copy /Y "%~dp0index.html" "%~dp0index.html" >nul
echo Original HUD files are ready.
echo astra.db is untouched.
pause
