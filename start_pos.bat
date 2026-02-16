@echo off
cd /d "%~dp0"
python -m pip install -r backend/requirements.txt
rem Start Flask backend directly (more reliable than flask run)
start "POS Backend" cmd /c python backend/app.py
timeout /t 3 > nul
start "" http://127.0.0.1:5000/
exit /b
