@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 (
  echo Could not create .venv. Install Python 3, then run this again.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
python -m pip install -r requirements.txt
echo Setup complete. Use START_BROWSER.bat to open the local page.
pause
