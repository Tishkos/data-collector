# One click: installs what is missing, registers the collector to start with Windows,
# starts it now and opens the dashboard. Safe to run again any time.
#
#   START.bat            demo plant (config\site.yaml)
#   START-PLANT.bat      real plant (config\site.plant.yaml), read-only polling
#   CHECK-PLANT.bat      one poll of the real plant, every point printed (no background task)
#   PROBE-PLANT.bat      which controllers answer on Modbus TCP at all (one read of one register each)
#   TRACE-PLANT.bat      fast read-only trace to data\trace (1 s master, 5 s SAU) in this window, Ctrl+C stops
#   RUN-IN-WINDOW.bat    run in the console instead (no Windows task); shows every message
#   STOP.bat             stop the background collector
#   UNINSTALL.bat        remove the Windows task (data stays in data\)
#
# The collector runs as a Windows scheduled task (at startup, as SYSTEM, restarts itself if it
# fails). Shutting the computer down is fine: it starts again with Windows, and the database
# in data\ keeps everything that was collected.
param(
    [switch]$Plant,
    [switch]$Check,
    [switch]$Trace,
    [switch]$Probe,
    [switch]$Foreground,
    [switch]$NoBrowser,
    [switch]$Elevated,
    [int]$Port = 8080
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$TaskName = "PlantDataCollector"

function Say($m) { Write-Host "[collector] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[collector] $m" -ForegroundColor Yellow }

# ---- 1. Python ---------------------------------------------------------------
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py -or ((& python --version 2>&1) -notmatch "Python 3\.(1[0-9]|9)")) {
    Say "Python 3 not found. Installing from python.org (one time, about a minute)..."
    $url = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
    $exe = Join-Path $env:TEMP "python-installer.exe"
    Invoke-WebRequest $url -OutFile $exe
    Start-Process $exe -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python install failed - install it from python.org and tick 'Add to PATH'" }
}
Say ("Python: " + (& python --version))

# ---- 2. Virtual environment + packages (first run only) --------------------------
if (-not (Test-Path ".venv")) {
    Say "Creating virtual environment..."
    & python -m venv .venv
}
$venvPy  = Join-Path $root ".venv\Scripts\python.exe"
$venvPyw = Join-Path $root ".venv\Scripts\pythonw.exe"
$stamp = Join-Path $root ".venv\.installed"
if (-not (Test-Path $stamp) -or ((Get-Item requirements.txt).LastWriteTime -gt (Get-Item $stamp).LastWriteTime)) {
    Say "Installing packages (first run only)..."
    & $venvPy -m pip install --quiet --upgrade pip
    & $venvPy -m pip install --quiet -r requirements.txt
    New-Item -ItemType File -Force $stamp | Out-Null
}

# ---- 3. Config -------------------------------------------------------------------
$cfg = "config\site.yaml"
if ($Plant -or $Check -or $Trace -or $Probe) { $cfg = "config\site.plant.yaml" }
if (-not (Test-Path $cfg)) { throw "config not found: $cfg" }
New-Item -ItemType Directory -Force "data" | Out-Null
Say "Config: $cfg"

# ---- 4a. CHECK: one poll, printed, then exit ----------------------------------------
if ($Check) {
    & $venvPy -m plantcollector.cli --config $cfg --data data check
    exit $LASTEXITCODE
}

# ---- 4a'. PROBE / TRACE: read-only tools, run in this window ----------------------------------
if ($Probe) {
    & $venvPy -m plantcollector.cli --config $cfg --data data probe
    exit $LASTEXITCODE
}
if ($Trace) {
    Say "Trace running in this window: 1 s master, 5 s SAU, files in data\trace. Ctrl+C stops it."
    & $venvPy -m plantcollector.cli --config $cfg --data data trace
    exit $LASTEXITCODE
}

# ---- 4b. FOREGROUND: run in this window (no Windows task) --------------------------------
$runArgs = @("-m", "plantcollector.cli", "--config", $cfg, "--data", "data", "run", "--port", $Port, "--log", "data\collector.log")
if ($Foreground) {
    Say "Running in this window (Ctrl+C to stop). Dashboard: http://localhost:$Port"
    if (-not $NoBrowser) { $runArgs += "--open" }
    & $venvPy @runArgs
    exit $LASTEXITCODE
}

# ---- 4c. BACKGROUND: Windows scheduled task, starts with Windows ------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Say "Asking for administrator rights to register the collector as a Windows task..."
    $flags = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Elevated", "-NoBrowser", "-Port", $Port)
    if ($Plant) { $flags += "-Plant" }
    try {
        Start-Process powershell -Verb RunAs -ArgumentList $flags -Wait
    } catch {
        Warn "Administrator rights refused. Running in this window instead (will not restart with Windows)."
        if (-not $NoBrowser) { $runArgs += "--open" }
        & $venvPy @runArgs
        exit $LASTEXITCODE
    }
} else {
    $action = New-ScheduledTaskAction -Execute $venvPyw -Argument ($runArgs -join " ") -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -RestartCount 100 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*plantcollector.cli*run*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    for ($i = 0; $i -lt 20; $i++) {       # wait until the previous instance has released the port
        if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Seconds 1
    }
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Say "Windows task '$TaskName' registered: starts with Windows, restarts if it fails."
    if ($Elevated) { Start-Sleep -Seconds 2 }
}

# ---- 5. Wait for the dashboard, open it --------------------------------------------------
if (-not $Elevated) {
    $url = "http://localhost:$Port"
    $urlFile = Join-Path $root "data\dashboard.url"
    $ok = $false
    if ($Plant) { Write-Host "[collector] Waiting for the collector to answer on $url " -ForegroundColor Cyan -NoNewline }
    else { Write-Host "[collector] Waiting for the collector to answer on $url (the demo first generates 3 days of pretend history) " -ForegroundColor Cyan -NoNewline }
    for ($i = 0; $i -lt 90; $i++) {
        if (Test-Path $urlFile) { $u = (Get-Content $urlFile -ErrorAction SilentlyContinue | Select-Object -First 1); if ($u) { $url = $u.Trim() } }
        try { Invoke-RestMethod "$url/api/v1/health" -TimeoutSec 2 | Out-Null; $ok = $true; break } catch { Write-Host "." -NoNewline; Start-Sleep -Seconds 1 }
    }
    Write-Host ""
    if ($ok) {
        Say "Collector is running. Dashboard: $url   (log: data\collector.log)"
        if (-not $NoBrowser) { Start-Process $url }
    } else {
        Warn "The collector did not answer on $url within 90 s. Diagnostics:"
        try {
            $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
            $info = $t | Get-ScheduledTaskInfo
            Write-Host ("  task state: {0}   last run: {1}   last result: {2}" -f $t.State, $info.LastRunTime, $info.LastTaskResult)
        } catch { Write-Host "  task '$TaskName' not found" }
        $log = Join-Path $root "data\collector.log"
        if (Test-Path $log) { Write-Host "  last lines of data\collector.log:"; Get-Content $log -Tail 15 | ForEach-Object { Write-Host "    $_" } }
        else { Write-Host "  data\collector.log was not created - the task did not start Python at all." }
        Write-Host "  To see the error directly, run:  powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Foreground" -ForegroundColor Yellow
    }
}
