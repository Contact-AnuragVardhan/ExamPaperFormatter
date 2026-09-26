@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo No .venv found. Run SETUP_LOCAL.bat first, then start this again.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
echo Starting Exam Format at http://127.0.0.1:5000
echo Press Ctrl+C in this window to stop.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:5000"
python web\app.py
pause
