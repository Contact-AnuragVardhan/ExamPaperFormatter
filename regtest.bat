@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 regtest.py
) else (
  python regtest.py
)
set EXITCODE=%ERRORLEVEL%
pause
exit /b %EXITCODE%
