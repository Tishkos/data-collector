# One-click: first run installs everything, every run starts the collector + dashboard.
# Usage:  START.bat                 -> simulator (config\site.yaml) unless site.plant.yaml is activated
#         START.bat -Plant          -> real plant config (config\site.plant.yaml)
#         START.bat -NoBrowser      -> don't open the browser
param(
    [switch]$Plant,
    [switch]$NoBrowser,
    [int]$Port = 8080,
    [int]$Interval = 30
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Say($m) { Write-Host "[collector] $m" -ForegroundColor Cyan }

# ---- 1. Python ---------------------------------------------------------------
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py -or ((& python --version 2>&1) -notmatch "Python 3\.(1[0-9]|[9])")) {
    Say "Python 3 not found. Installing from python.org (one time, ~1 minute)..."
    $url = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
    $exe = Join-Path $env:TEMP "python-installer.exe"
    Invoke-WebRequest $url -OutFile $exe
    Start-Process $exe -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0" -Wait
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python install failed - install it from python.org and tick 'Add to PATH'" }
}
Say ("Python: " + (& python --version))

# ---- 2. Virtual environment + packages (first run only) --------------------------
if (-not (Test-Path ".venv")) {
    Say "Creating virtual environment..."
    & python -m venv .venv
}
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
$stamp = Join-Path $root ".venv\.installed"
if (-not (Test-Path $stamp) -or ((Get-Item requirements.txt).LastWriteTime -gt (Get-Item $stamp).LastWriteTime)) {
    Say "Installing packages (first run only)..."
    & $venvPy -m pip install --quiet --upgrade pip
    & $venvPy -m pip install --quiet -r requirements.txt
    New-Item -ItemType File -Force $stamp | Out-Null
}

# ---- 3. Config -------------------------------------------------------------------
$cfg = "config\site.yaml"
if ($Plant) { $cfg = "config\site.plant.yaml" }
if (-not (Test-Path $cfg)) { throw "config not found: $cfg" }
Say "Config: $cfg"
New-Item -ItemType Directory -Force "data" | Out-Null

# ---- 4. Run -----------------------------------------------------------------------
$args = @("-m", "qs_hybrid.cli", "--config", $cfg, "--data", "data", "local", "--port", $Port, "--interval", $Interval)
if (-not $NoBrowser) { $args += "--open" }
Say "Starting collector + dashboard on http://localhost:$Port  (Ctrl+C to stop)"
& $venvPy @args
