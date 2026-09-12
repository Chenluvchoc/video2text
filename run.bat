@echo off
chcp 65001 >nul
cd /d %~dp0

if not exist venv (
  echo 首次运行：创建虚拟环境并安装依赖…
  python -m venv venv
  call venv\Scripts\pip install -U pip -q
  call venv\Scripts\pip install -r requirements.txt -q
  echo 安装浏览器内核（用于自动刷新 cookies）…
  call venv\Scripts\playwright install chromium
)

echo 启动服务…
venv\Scripts\python app.py
pause
