@echo off
cd /d %~dp0
if not exist .venv (
  echo [1/3] Создаю venv...
  python -m venv .venv
)
if not exist logs mkdir logs
echo [2/3] Проверяю зависимости...
.venv\Scripts\pip install -q -r requirements.txt
echo [3/3] Запускаю NORMWEAR (web + боты)...
echo Открой http://127.0.0.1:8000/healthz для проверки
.venv\Scripts\python -m app.main
pause
