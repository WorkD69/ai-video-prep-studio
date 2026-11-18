# AI Video Prep Studio launcher (Windows).
# Makes sure Docker Desktop and the compose stack are up, waits for /health,
# then opens the UI in the default browser. Started silently by launch.vbs.
# Errors -> message box; details -> %LOCALAPPDATA%\ai-video-prep-studio\launcher.log

$ErrorActionPreference = 'Stop'
$Url = 'http://127.0.0.1:8000'
$Title = 'AI Video Prep Studio'
$RepoDir = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $env:LOCALAPPDATA 'ai-video-prep-studio'
$LogFile = Join-Path $LogDir 'launcher.log'
$DockerDesktopExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# One launcher at a time: a double click must not run two `compose up` in parallel
$script:Mutex = New-Object System.Threading.Mutex($false, 'Local\ai-video-prep-studio-launcher')
if (-not $script:Mutex.WaitOne(0)) { exit 0 }

# Keep the log small: rotate once it passes 1 MB
if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 1MB) {
    Move-Item -Force $LogFile "$LogFile.1"
}

function Write-Log([string]$msg) {
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Show-Error([string]$msg) {
    Write-Log "ERROR: $msg"
    # DefaultDesktopOnly: show on top even after minutes of waiting in the background
    [System.Windows.Forms.MessageBox]::Show(
        "$msg`n`nЛог: $LogFile", $Title, 'OK', 'Error', 'Button1', 'DefaultDesktopOnly') | Out-Null
}

$script:Tray = $null
function Show-Progress([string]$msg) {
    Write-Log $msg
    if ($null -eq $script:Tray) {
        $script:Tray = New-Object System.Windows.Forms.NotifyIcon
        $icoPath = Join-Path $PSScriptRoot 'app.ico'
        if (Test-Path $icoPath) { $script:Tray.Icon = New-Object System.Drawing.Icon($icoPath) }
        else { $script:Tray.Icon = [System.Drawing.SystemIcons]::Information }
        $script:Tray.Text = $Title
        $script:Tray.Visible = $true
    }
    $script:Tray.ShowBalloonTip(5000, $Title, $msg, 'Info')
}

function Close-Progress {
    if ($null -ne $script:Tray) { $script:Tray.Visible = $false; $script:Tray.Dispose(); $script:Tray = $null }
}

function Test-Healthy {
    try {
        $r = Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 3
        return ($r.status -eq 'ok' -and $r.db -eq 'ok' -and $r.redis -eq 'ok')
    } catch { return $false }
}

function Test-DockerEngine {
    # `docker info` can hang while Docker Desktop is starting: cap it at 10 s
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = (Get-Command docker).Source
        $psi.Arguments = 'info --format "{{.ServerVersion}}"'
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $p = [System.Diagnostics.Process]::Start($psi)
        if (-not $p.WaitForExit(10000)) {
            try { $p.Kill() } catch {}
            return $false
        }
        return ($p.ExitCode -eq 0)
    } catch { return $false }
}

function Wait-Until([scriptblock]$check, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (& $check) { return $true }
        Start-Sleep -Seconds 2
    }
    return (& $check)
}

$startedDocker = $false
try {
    Write-Log "launch from $RepoDir"

    # 1. Already running (the usual case: Docker Desktop autostarts the stack)
    if (-not (Test-Healthy)) {

        # 2. Docker engine
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            Show-Error 'Не найден Docker. Установи Docker Desktop и запусти ярлык снова.'
            exit 1
        }
        if (-not (Test-DockerEngine)) {
            if (-not (Test-Path $DockerDesktopExe)) {
                Show-Error "Docker Desktop не найден по пути:`n$DockerDesktopExe"
                exit 1
            }
            Show-Progress 'Запускаю Docker Desktop… Это может занять минуту-две.'
            Start-Process -FilePath $DockerDesktopExe
            $startedDocker = $true
            if (-not (Wait-Until { Test-DockerEngine } 240)) {
                Show-Error 'Docker Desktop не запустился за 4 минуты. Открой его вручную и проверь, что он работает.'
                exit 1
            }
        }

        # 3. Stack. After a fresh Docker start the restart policy usually brings it
        # up by itself, so give it a moment first; up -d is idempotent anyway.
        $graceSeconds = if ($startedDocker) { 20 } else { 0 }
        if (-not (Wait-Until { Test-Healthy } $graceSeconds)) {
            Show-Progress 'Поднимаю сервис…'
            $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
            $out = & docker compose --project-directory $RepoDir -f (Join-Path $RepoDir 'docker-compose.yml') up -d 2>&1
            $code = $LASTEXITCODE
            $ErrorActionPreference = $prev
            # PS 5.1 wraps native stderr lines in ErrorRecords; log plain text
            $text = ($out | ForEach-Object { "$_" }) -join "`n"
            Write-Log "docker compose up -d (exit $code):`n$text"
            if ($code -ne 0) {
                Show-Error 'Не получилось поднять сервис (docker compose up -d). Подробности в логе.'
                exit 1
            }
            if (-not (Wait-Until { Test-Healthy } 180)) {
                Show-Error "Сервис запущен, но $Url/health не отвечает ok за 3 минуты."
                exit 1
            }
        }
    }

    Write-Log 'healthy, opening browser'
    Start-Process $Url
} catch {
    Show-Error ("Неожиданная ошибка: " + $_.Exception.Message)
    exit 1
} finally {
    Close-Progress
}
