@echo off
REM BlackBox one-click start for Windows. Needs Python 3.10+ on PATH.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  python -m venv .venv || goto :nopython
)
echo Installing requirements (first run takes a minute)...
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r backend\requirements.txt || goto :error
if not exist .env copy .env.example .env >nul
echo.
echo BlackBox is starting at http://127.0.0.1:8001  (press Ctrl+C to stop)
start "" http://127.0.0.1:8001
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8001
goto :eof
:nopython
echo Python was not found. Install Python 3.10+ from python.org and tick "Add python.exe to PATH".
pause
goto :eof
:error
echo Installing requirements failed. Check your internet connection and try again.
pause
