
@echo off
cd /d "%~dp0"
echo [PriceTracker] 启动中...
start http://localhost:8000
python run.py
pause

