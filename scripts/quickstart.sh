#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f configs/config.yaml ]; then
  cp configs/config.example.yaml configs/config.yaml
fi

# Build frontend
echo "Building frontend..."
cd "frontend"
set +e  # 暂时关闭错误立即退出
npm install && npm run build
if [ $? -ne 0 ]; then
  echo "Frontend build failed. Continuing with backend startup."
fi
set -e  # 重新启用错误立即退出
cd ".."

uvicorn backend.main:app --host 0.0.0.0 --port 8000
