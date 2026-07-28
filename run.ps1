# Sets up a venv (first run), installs deps, and starts the dashboard.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Recreate the venv if it is missing or broken (a Python upgrade can remove
# the interpreter an existing .venv was built against).
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$venvOk = $false
if (Test-Path $py) {
    try { & $py -c "import sys" ; $venvOk = ($LASTEXITCODE -eq 0) } catch { $venvOk = $false }
}
if (-not $venvOk) {
    if (Test-Path ".venv") {
        Write-Host "Existing .venv is broken (Python upgrade?) - recreating it..." -ForegroundColor Yellow
        Remove-Item ".venv" -Recurse -Force -Confirm:$false
    } else {
        Write-Host "Creating virtual environment (first run - this takes a minute)..."
    }
    py -3 -m venv .venv
}

Write-Host "Checking/installing dependencies (quiet - first run or after an upgrade can take a few minutes)..."
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt
Write-Host "Dependencies OK."

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example - set AVD_WORKSPACE_ID there, or link a tenant in the Connections app." -ForegroundColor Yellow
}

# Respect AVD_APP_HOST / AVD_APP_PORT from .env (defaults 127.0.0.1:8000).
$bindHost = "127.0.0.1"
$port = 8000
foreach ($line in Get-Content ".env") {
    if ($line -match '^\s*AVD_APP_HOST\s*=\s*(\S+)') { $bindHost = $Matches[1] }
    if ($line -match '^\s*AVD_APP_PORT\s*=\s*(\d+)') { $port = [int]$Matches[1] }
}
$openHost = if ($bindHost -eq "0.0.0.0") { "127.0.0.1" } else { $bindHost }

# Refuse to start on a port that is already taken - a stale server here
# looks like "the site never comes up" in the new console.
$inUse = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    $owner = ($inUse | Select-Object -First 1).OwningProcess
    Write-Host "Port $port is already in use by PID $owner - is the dashboard already running?" -ForegroundColor Yellow
    Write-Host "Open http://${openHost}:$port/os or stop that process, then re-run."
    exit 1
}

Write-Host "Starting dashboard:" -ForegroundColor Green
Write-Host "  Classic dashboard : http://${openHost}:$port"
Write-Host "  AVD Ops OS        : http://${openHost}:$port/os"
Write-Host "This window stays open while the server runs (Ctrl+C to stop)."

# Open the browser once the server has had a moment to bind.
Start-Job -ScriptBlock {
    param($url)
    Start-Sleep -Seconds 3
    Start-Process $url
} -ArgumentList "http://${openHost}:$port/os" | Out-Null

& $py -m uvicorn backend.main:app --host $bindHost --port $port --reload
