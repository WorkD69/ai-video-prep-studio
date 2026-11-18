' Silent entry point for the desktop / Start menu shortcut: runs launch.ps1
' with no console window. Keep this file ASCII-only (VBScript reads ANSI).
Option Explicit
Dim fso, shell, here, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "\launch.ps1"""
shell.Run cmd, 0, False
