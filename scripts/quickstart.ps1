# Quickstart script for trash-trade (Windows PowerShell)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Split-Path -Parent $root
Set-Location $project

if (-not (Test-Path ".venv")) {
  python -m venv .venv
}

. .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if (-not (Test-Path "configs\config.yaml")) {
  Copy-Item "configs\config.example.yaml" "configs\config.yaml"
}

Write-Host "Starting FastAPI server + runtime..."
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
