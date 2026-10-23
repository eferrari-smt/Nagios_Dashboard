# Habilita el acceso al Nagios Dashboard (puerto 8000) desde otras PCs de la
# misma red local (oficina). No lo expone a internet: la regla solo permite
# conexiones desde el mismo subnet local.
#
# Correr como Administrador (clic derecho > "Ejecutar con PowerShell" en una
# consola elevada, o ver instrucciones del asistente).

$ruleName = "Nagios Dashboard (LAN)"

if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
    Write-Host "La regla '$ruleName' ya existe. Nada que hacer." -ForegroundColor Yellow
} else {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -RemoteAddress LocalSubnet -Profile Any
    Write-Host "Regla de firewall creada: puerto 8000 TCP permitido desde la red local." -ForegroundColor Green
}

Write-Host ""
Write-Host "IPs locales de esta PC (compartile una de estas + el puerto a tu compañero):"
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.InterfaceAlias -notlike "*Tailscale*" } | ForEach-Object {
    Write-Host ("  http://{0}:8000" -f $_.IPAddress) -ForegroundColor Cyan
}
