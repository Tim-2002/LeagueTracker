@echo off
chcp 65001 >nul
title LeagueTracker Web-App Server
cd /d "%~dp0"

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [FEHLER] Python wurde nicht gefunden!
    echo Bitte installiere Python 3.9+ von https://www.python.org/downloads/
    echo und setze bei der Installation den Haken bei "Add Python to PATH".
    pause
    exit /b 1
)

python -c "import fastapi, uvicorn, requests, pydantic" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installiere benoetigte Python-Pakete (einmalig beim ersten Start^)...
    python -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [FEHLER] Installation der Pakete fehlgeschlagen.
        pause
        exit /b 1
    )
)

echo Starte LeagueTracker Server auf http://127.0.0.1:8000 ...
start "" http://127.0.0.1:8000
python backend\app.py
pause
