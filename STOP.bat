@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Stop-ScheduledTask -TaskName PlantDataCollector -ErrorAction SilentlyContinue; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*qs_hybrid.cli*local*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Host 'collector stopped'"
pause
