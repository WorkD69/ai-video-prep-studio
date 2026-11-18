# Creates "AI Video Prep Studio" shortcuts on the Desktop and in the Start menu.
# Re-run after moving the project folder: shortcuts point at this checkout.
#   powershell -NoProfile -ExecutionPolicy Bypass -File launcher\install-shortcuts.ps1

$ErrorActionPreference = 'Stop'
$Name = 'AI Video Prep Studio'
$Vbs = Join-Path $PSScriptRoot 'launch.vbs'
$Ico = Join-Path $PSScriptRoot 'app.ico'
$WScript = Join-Path $env:WINDIR 'System32\wscript.exe'

$targets = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) '')
)

$shell = New-Object -ComObject WScript.Shell
foreach ($dir in $targets) {
    $lnkPath = Join-Path $dir "$Name.lnk"
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = $WScript
    $lnk.Arguments = '"' + $Vbs + '"'
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.IconLocation = "$Ico,0"
    $lnk.Description = 'Видео → ZIP для AI-агента (запускает сервис и открывает его в браузере)'
    $lnk.Save()
    Write-Output "created: $lnkPath"
}
