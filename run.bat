@echo off
rem Chay app tren Windows:  run.bat  |  run.bat 9000 (doi cong)  |  run.bat --update (cap nhat yt-dlp)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe call setup.bat
set PORT=8790
if "%~1"=="--update" (
  .venv\Scripts\python.exe -m pip install --upgrade "yt-dlp[default]" deno
) else if not "%~1"=="" (
  set PORT=%~1
)
start "" http://127.0.0.1:%PORT%
.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port %PORT%
