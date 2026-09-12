# 一键启动（Linux / macOS）
set -e
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
  echo "首次运行：创建虚拟环境并安装依赖…"
  python3 -m venv venv
  ./venv/bin/pip install -U pip -q
  ./venv/bin/pip install -r requirements.txt -q
  echo "安装浏览器内核（用于自动刷新 cookies）…"
  ./venv/bin/playwright install chromium
fi

echo "启动服务…"
./venv/bin/python app.py
