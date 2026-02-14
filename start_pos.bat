@echo off
cd /d "%~dp0"
python -m pip install -r backend/requirements.txt
set FLASK_APP=backend/app.py
start "POS Backend" cmd /c python -m flask run --host=127.0.0.1 --port=5000
timeout /t 3 > nul
start "" http://127.0.0.1:5000/
exit /b
