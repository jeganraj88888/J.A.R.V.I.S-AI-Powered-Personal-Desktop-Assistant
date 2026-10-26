@echo off
setlocal
cd /d "%~dp0"

echo ===============================================
echo   J.A.R.V.I.S - Gemini AI installer
 echo ===============================================

echo.
echo [1/2] Installing Python packages...
py -m pip install -U -r requirements_JARVIS_GEMINI_FAST_NEURAL.txt
if errorlevel 1 (
  echo.
  echo Package installation failed. Check Python/pip and try again.
  pause
  exit /b 1
)

echo.
echo [2/2] Checking Gemini API key environment variable...
if "%GEMINI_API_KEY%"=="" (
  echo GEMINI_API_KEY is not visible in this terminal.
  echo Set it in Windows Environment Variables, then open a NEW terminal.
  echo Do NOT put the key inside the HTML or Python file.
) else (
  echo Gemini API key detected.
)

echo.
echo Starting JARVIS...
py app_JARVIS_GEMINI_FAST_NEURAL.py
pause
