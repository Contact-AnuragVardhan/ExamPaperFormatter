@echo off
cd /d "%~dp0"

echo Creating a local Python environment...
py -3 -m venv .venv
if errorlevel 1 (
  echo Could not create .venv.
  echo Install Python 3 from python.org, then run this again.
  pause
  exit /b 1
)

echo.
echo Installing packages. This folder is on Google Drive, so this step
echo often sits on "Installing collected packages" for several minutes.
echo Leave this window open until you see "Setup complete."
echo.
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Package install failed. The messages above are the reason.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -c "import flask, docx, openai, dotenv, requests"
if errorlevel 1 (
  echo.
  echo Packages were installed, but Python could not import them.
  pause
  exit /b 1
)

echo.
echo Setup complete. Use START_BROWSER.bat to open the local page.
pause
