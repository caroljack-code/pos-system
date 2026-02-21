@echo off
cd /d "%~dp0"
python -m pip install -r backend/requirements.txt
rem Start Flask backend directly (more reliable than flask run)
if "%POS_PORT%"=="" set POS_PORT=5000
if "%POS_BIND_HOST%"=="" set POS_BIND_HOST=127.0.0.1
start "POS Backend" powershell -NoProfile -Command " $env:POS_BIND_HOST='%POS_BIND_HOST%'; $env:POS_PORT='%POS_PORT%'; python backend/app.py "
powershell -NoProfile -Command " try{ $port=$env:POS_PORT; if(-not $port){$port=5000}; $max=30; $i=0; while($i -lt $max){ $ok = (Test-NetConnection -ComputerName 127.0.0.1 -Port $port).TcpTestSucceeded; if($ok){ break } ; Start-Sleep -Seconds 1; $i++ } }catch{} "
start "" http://127.0.0.1:%POS_PORT%/
exit /b
