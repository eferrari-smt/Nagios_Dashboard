# Revisa que el scheduler y la API sigan corriendo; si alguno se cayó
# (por el motivo que sea), lo reinicia vía su tarea programada. Pensado
# para correr cada pocos minutos como su propia tarea programada.

$logPath = "C:\Users\Enzo Ferrari\OneDrive - smartmation.com\Documents\Nagios Dashboard\backend\logs\watchdog.log"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

$apiRunning = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*uvicorn*app.api*' }
if (-not $apiRunning) {
    Add-Content -Path $logPath -Value "$timestamp API caida, reiniciando..."
    Start-ScheduledTask -TaskName "NagiosDashboard-API"
}

$schedulerRunning = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*run_scheduler.py*' }
if (-not $schedulerRunning) {
    Add-Content -Path $logPath -Value "$timestamp Scheduler caido, reiniciando..."
    Start-ScheduledTask -TaskName "NagiosDashboard-Scheduler"
}
