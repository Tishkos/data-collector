# Removes the scheduled task created by install-service.ps1. Run as Administrator.
Stop-ScheduledTask -TaskName "PlantDataCollector" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "PlantDataCollector" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "PlantDataCollector task removed. Data in .\data is kept." -ForegroundColor Green
