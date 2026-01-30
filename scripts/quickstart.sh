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

uvicorn backend.main:app --host 0.0.0.0 --port 8000
