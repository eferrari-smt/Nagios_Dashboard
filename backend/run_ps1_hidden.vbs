' Lanza un .ps1 sin ninguna ventana visible, ni siquiera el flash breve
' que -WindowStyle Hidden deja pasar en Windows 11 con Windows Terminal
' como host de consola por defecto. WScript.Shell.Run con windowStyle=0
' crea el proceso oculto a nivel de Windows (CreateProcess), sin pasar
' por esa capa de presentación.
'
' Uso: wscript.exe //B run_ps1_hidden.vbs "C:\ruta\al\script.ps1"

Dim shell, ps1Path, cmd
Set shell = CreateObject("WScript.Shell")
ps1Path = WScript.Arguments(0)
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1Path & """"
shell.Run cmd, 0, False
