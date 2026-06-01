# Sets up a venv (first run), installs deps, and starts the dashboard.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    py -3 -m venv .venv
}

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example - edit it to set AVD_WORKSPACE_ID." -ForegroundColor Yellow
}

$port = 8000
Write-Host "Starting dashboard at http://127.0.0.1:$port" -ForegroundColor Green
& $py -m uvicorn backend.main:app --host 127.0.0.1 --port $port --reload
