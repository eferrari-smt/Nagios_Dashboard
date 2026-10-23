$python = "C:\Users\Enzo Ferrari\AppData\Local\Programs\Python\Python312\python.exe"
$workDir = "C:\Users\Enzo Ferrari\OneDrive - smartmation.com\Documents\Nagios Dashboard\backend"
$logsDir = "$workDir\logs"

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 2) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$schedulerCmd = "Set-Location '$workDir'; & '$python' run_scheduler.py *>> '$logsDir\scheduler.log'"
$actionScheduler = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -NoProfile -Command `"$schedulerCmd`"" -WorkingDirectory $workDir
Register-ScheduledTask -TaskName "NagiosDashboard-Scheduler" -Action $actionScheduler -Trigger $trigger -Settings $settings -Principal $principal -Force

$apiCmd = "Set-Location '$workDir'; & '$python' -m uvicorn app.api:app --host 0.0.0.0 --port 8000 *>> '$logsDir\api.log'"
$actionApi = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -NoProfile -Command `"$apiCmd`"" -WorkingDirectory $workDir
Register-ScheduledTask -TaskName "NagiosDashboard-API" -Action $actionApi -Trigger $trigger -Settings $settings -Principal $principal -Force

$watchdogAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$workDir\watchdog.ps1`"" -WorkingDirectory $workDir
$watchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$watchdogSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "NagiosDashboard-Watchdog" -Action $watchdogAction -Trigger $watchdogTrigger -Settings $watchdogSettings -Principal $principal -Force

Write-Output "Listo. Las 3 tareas quedaron con LogonType S4U (independientes de tu sesion)."
Start-ScheduledTask -TaskName "NagiosDashboard-API"
Start-ScheduledTask -TaskName "NagiosDashboard-Scheduler"
Start-Sleep -Seconds 5
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, StartTime
