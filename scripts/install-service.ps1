# Registers the collector as a Windows Scheduled Task that starts at boot and
# restarts if it crashes. No extra tools needed. Run as Administrator.
#   .\scripts\install-service.ps1            -> simulator config
#   .\scripts\install-service.ps1 -Plant     -> real plant config
param([switch]$Plant, [int]$Port = 8080, [int]$Interval = 30)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$cfg = if ($Plant) { "config\site.plant.yaml" } else { "config\site.yaml" }
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { throw "run START.bat once first (it creates the environment)" }

$name = "PlantDataCollector"
$cmdArgs = "-m qs_hybrid.cli --config `"$root\$cfg`" --data `"$root\data`" local --port $Port --interval $Interval --seed-hours 0"
$action = New-ScheduledTaskAction -Execute $venvPy -Argument $cmdArgs -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "Installed and started task '$name' ($cfg). Dashboard: http://localhost:$Port" -ForegroundColor Green
Write-Host "It starts automatically at boot. Remove with scripts\uninstall-service.ps1"
