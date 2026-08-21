#requires -Version 5.1
param(
  [Parameter(Position = 0)]
  [string]$Action = "start"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
  throw "scripts/god.ps1 is for native Windows PowerShell. Use ./scripts/god.sh on macOS/Linux."
}

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$EnvFile = if ($env:GOD_ENV_FILE) { $env:GOD_ENV_FILE } else { Join-Path $RootDir ".env" }
$BackendRoot = if ($env:BACKEND_ROOT) { $env:BACKEND_ROOT } else { Join-Path $RootDir "agentsociety" }
$FrontendRoot = Join-Path $BackendRoot "frontend"

$StateDir = Join-Path $RootDir ".god"
$LogDir = Join-Path $StateDir "logs"
$PidDir = Join-Path $StateDir "pids"
$TownDir = Join-Path $StateDir "town"
foreach ($dir in @($LogDir, $PidDir, $TownDir)) {
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

$BackendPidFile = Join-Path $PidDir "backend.pid"
$FrontendPidFile = Join-Path $PidDir "frontend.pid"
$BackendLog = Join-Path $LogDir "Backend.log"
$FrontendLog = Join-Path $LogDir "Control-room.log"

function Write-GodLog {
  param([string]$Message)
  Write-Host "[GOD] $Message"
}

function Stop-God {
  param([string]$Message)
  throw "[GOD] error: $Message"
}

function Test-CommandExists {
  param([string]$Name)
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Show-Usage {
  @"
Usage: .\scripts\god.ps1 [start|restart|stop|status|tail|setup|reset]

start    Start the backend and the control room, then print the URL.
restart  Stop everything cleanly, then start.
stop     Stop GOD and release its ports.
status   Print URLs, ports, and how many AI residents are configured.
tail     Follow the backend and control-room logs.
setup    Install or refresh Python and Node dependencies only.
reset    Stop, then remove every saved AI resident (.god\town\agents.json).
"@ | Write-Host
}

$script:GodConfig = @{}

function Import-GodEnv {
  $config = @{
    GOD_BACKEND_HOST = "127.0.0.1"
    GOD_BACKEND_PORT = "8001"
    GOD_FRONTEND_PORT = "5174"
    GOD_SKIP_SETUP = "0"
    GOD_FORCE_SETUP = "0"
    BACKEND_LOG_LEVEL = "info"
  }
  if (Test-Path $EnvFile) {
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
      $trimmed = $line.Trim()
      if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
        continue
      }
      $parts = $trimmed.Split("=", 2)
      $config[$parts[0].Trim()] = $parts[1].Trim()
    }
  }
  foreach ($key in @($config.Keys)) {
    $fromShell = [System.Environment]::GetEnvironmentVariable($key)
    if ($fromShell) {
      $config[$key] = $fromShell
    }
  }
  $script:GodConfig = $config
}

function Get-GodValue {
  param([string]$Key, [string]$Fallback = "")
  if ($script:GodConfig.ContainsKey($Key) -and $script:GodConfig[$Key]) {
    return $script:GodConfig[$Key]
  }
  return $Fallback
}

function Get-BackendUrl {
  return "http://$(Get-GodValue 'GOD_BACKEND_HOST' '127.0.0.1'):$(Get-GodValue 'GOD_BACKEND_PORT' '8001')"
}

function Get-FrontendUrl {
  return "http://127.0.0.1:$(Get-GodValue 'GOD_FRONTEND_PORT' '5174')"
}

function Confirm-EnvFile {
  if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $RootDir ".env.example") $EnvFile
    Write-GodLog "Created .env from .env.example"
  }
  Import-GodEnv
}

function Update-SessionPath {
  # A freshly installed tool is not on this session's PATH yet; re-read it.
  $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
  $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
  $extra = Join-Path $env:USERPROFILE ".local\bin"
  $env:Path = (@($machine, $user, $extra) | Where-Object { $_ }) -join ";"
}

function Install-WithWinget {
  param([string]$PackageId, [string]$Label)
  if (-not (Test-CommandExists "winget")) {
    return $false
  }
  Write-GodLog "Installing $Label via winget"
  # Out-Host keeps winget's chatter on screen instead of in the return value.
  & winget install --id $PackageId -e --accept-package-agreements --accept-source-agreements | Out-Host
  Update-SessionPath
  return $true
}

function Install-Uv {
  # Prefer winget; fall back to the official astral install script.
  $viaWinget = Install-WithWinget -PackageId "astral-sh.uv" -Label "uv"
  if ($viaWinget -and (Test-CommandExists "uv")) {
    return
  }
  Write-GodLog "Installing uv via the official install script"
  $previous = $ProgressPreference
  $ProgressPreference = "SilentlyContinue"
  try {
    # PowerShell 5.1 may still default to TLS 1.0; astral.sh requires 1.2+.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-Expression (Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1")
  } catch {
    Stop-God ("Failed to install uv automatically: {0}. Install it manually from https://docs.astral.sh/uv/getting-started/installation/ , then reopen PowerShell and retry." -f $_.Exception.Message)
  } finally {
    $ProgressPreference = $previous
  }
  Update-SessionPath
}

function Confirm-Toolchain {
  # Install whatever is missing. uv ships its own Python, so python is not checked.
  if (-not (Test-CommandExists "git")) {
    Install-WithWinget -PackageId "Git.Git" -Label "Git" | Out-Null
  }
  if (-not (Test-CommandExists "npm")) {
    Install-WithWinget -PackageId "OpenJS.NodeJS.LTS" -Label "Node.js LTS" | Out-Null
  }
  if (-not (Test-CommandExists "uv")) {
    Install-Uv
  }

  $missing = @()
  foreach ($tool in @("uv", "npm")) {
    if (-not (Test-CommandExists $tool)) {
      $missing += $tool
    }
  }
  if ($missing.Count -gt 0) {
    Stop-God ("Could not install: {0}. Install them manually, then reopen PowerShell and retry. uv: https://docs.astral.sh/uv/getting-started/installation/ , Node.js: https://nodejs.org/" -f ($missing -join ", "))
  }
}

function Test-PortOpen {
  param([int]$Port)
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $connect = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
    if (-not $connect.AsyncWaitHandle.WaitOne(400)) {
      return $false
    }
    $client.EndConnect($connect)
    return $true
  } catch {
    return $false
  } finally {
    $client.Close()
  }
}

function Wait-ForPort {
  param([int]$Port, [string]$Label, [int]$TimeoutSeconds = 120, [string]$LogPath = "")
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-PortOpen -Port $Port) {
      Write-GodLog "$Label ready on port $Port"
      return
    }
    Start-Sleep -Seconds 1
  }
  if ($LogPath -and (Test-Path $LogPath)) {
    Write-GodLog "Last 30 log lines for ${Label}:"
    Get-Content -LiteralPath $LogPath -Tail 30 | Write-Host
  }
  Stop-God "Timed out waiting for $Label on port $Port"
}

function Start-GodService {
  param([string]$PidFile, [string]$WorkingDirectory, [string]$Command, [string]$LogPath)
  Set-Content -LiteralPath $LogPath -Value "" -Encoding UTF8
  $process = Start-Process -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "$Command *>> `"$LogPath`"") `
    -WorkingDirectory $WorkingDirectory `
    -WindowStyle Hidden `
    -PassThru
  Set-Content -LiteralPath $PidFile -Value $process.Id -Encoding ASCII
}

function Stop-PidFile {
  param([string]$PidFile, [string]$Label)
  if (-not (Test-Path $PidFile)) {
    return
  }
  $pidValue = (Get-Content -LiteralPath $PidFile -Raw).Trim()
  if ($pidValue) {
    $process = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
    if ($process) {
      Write-GodLog "Stopping $Label pid=$pidValue"
      Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
  }
  Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Clear-PortListeners {
  param([int]$Port)
  try {
    $owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty OwningProcess -Unique
  } catch {
    $owners = @()
  }
  foreach ($owner in $owners) {
    if ($owner -and $owner -ne 0) {
      Write-GodLog "Clearing port $Port (pid=$owner)"
      Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
    }
  }
}

function Install-GodDependencies {
  Confirm-Toolchain
  if ((Get-GodValue "GOD_SKIP_SETUP" "0") -eq "1") {
    Write-GodLog "Skipping dependency setup (GOD_SKIP_SETUP=1)"
    return
  }
  $force = (Get-GodValue "GOD_FORCE_SETUP" "0") -eq "1"

  if ($force -or -not (Test-Path (Join-Path $BackendRoot ".venv"))) {
    Write-GodLog "Syncing backend Python dependencies"
    Push-Location $BackendRoot
    try { & uv sync } finally { Pop-Location }
  }

  if ($force -or -not (Test-Path (Join-Path $FrontendRoot "node_modules"))) {
    Write-GodLog "Installing control-room dependencies"
    & npm install --no-audit --no-fund --loglevel=error --prefix $FrontendRoot
  }
}

function Start-GodBackend {
  Confirm-EnvFile
  $port = [int](Get-GodValue "GOD_BACKEND_PORT" "8001")
  $backendUrl = Get-BackendUrl
  if (Test-PortOpen -Port $port) {
    try {
      Invoke-WebRequest -Uri "$backendUrl/health" -UseBasicParsing -TimeoutSec 5 | Out-Null
      Write-GodLog "Backend already up"
      return
    } catch {
      # Port taken by something that is not a healthy backend; fall through and restart.
    }
  }

  Write-GodLog "Starting backend"
  $logLevel = Get-GodValue "BACKEND_LOG_LEVEL" "info"
  $env:GOD_ROOT = $RootDir
  $env:GOD_ENV_FILE = $EnvFile
  $env:GOD_STATE_DIR = $StateDir
  $env:BACKEND_HOST = Get-GodValue "GOD_BACKEND_HOST" "127.0.0.1"
  $env:BACKEND_PORT = "$port"
  $env:BACKEND_LOG_LEVEL = $logLevel
  Start-GodService -PidFile $BackendPidFile -WorkingDirectory $BackendRoot `
    -Command "uv run python -m agentsociety2.backend.run --log-level $logLevel" `
    -LogPath $BackendLog
  Wait-ForPort -Port $port -Label "Backend" -TimeoutSeconds 120 -LogPath $BackendLog

  $deadline = (Get-Date).AddSeconds(30)
  while ((Get-Date) -lt $deadline) {
    try {
      Invoke-WebRequest -Uri "$backendUrl/health" -UseBasicParsing -TimeoutSec 5 | Out-Null
      return
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  Stop-God "Backend port is open but /health did not respond"
}

function Start-GodFrontend {
  $port = [int](Get-GodValue "GOD_FRONTEND_PORT" "5174")
  if (Test-PortOpen -Port $port) {
    Write-GodLog "Control room already up"
    return
  }
  Write-GodLog "Starting control room"
  $env:VITE_BASE = "/"
  $env:VITE_HOST = "127.0.0.1"
  $env:GOD_BACKEND_PORT = Get-GodValue "GOD_BACKEND_PORT" "8001"
  Start-GodService -PidFile $FrontendPidFile -WorkingDirectory $FrontendRoot `
    -Command "npm run dev -- --host 127.0.0.1 --port $port --base /" `
    -LogPath $FrontendLog
  Wait-ForPort -Port $port -Label "Control room" -TimeoutSeconds 120 -LogPath $FrontendLog
}

function Stop-GodAll {
  Import-GodEnv
  Stop-PidFile -PidFile $FrontendPidFile -Label "control room"
  Stop-PidFile -PidFile $BackendPidFile -Label "backend"
  Clear-PortListeners -Port ([int](Get-GodValue "GOD_FRONTEND_PORT" "5174"))
  Clear-PortListeners -Port ([int](Get-GodValue "GOD_BACKEND_PORT" "8001"))
  Write-GodLog "Stopped"
}

function Get-AgentCount {
  $path = Join-Path $TownDir "agents.json"
  if (-not (Test-Path $path)) {
    return 0
  }
  try {
    $data = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    if ($data -is [array]) {
      return $data.Count
    }
    return 0
  } catch {
    return 0
  }
}

function Show-GodStatus {
  Import-GodEnv
  $backendPort = [int](Get-GodValue "GOD_BACKEND_PORT" "8001")
  $frontendPort = [int](Get-GodValue "GOD_FRONTEND_PORT" "5174")
  Write-GodLog "Control room: $(Get-FrontendUrl)"
  Write-GodLog "Backend:      $(Get-BackendUrl)"
  $backendState = if (Test-PortOpen -Port $backendPort) { "up" } else { "down" }
  $frontendState = if (Test-PortOpen -Port $frontendPort) { "up" } else { "down" }
  Write-GodLog "Ports: backend $backendPort ($backendState), control room $frontendPort ($frontendState)"
  Write-GodLog "Saved AI residents: $(Get-AgentCount)"
}

function Start-GodAll {
  Confirm-EnvFile
  Install-GodDependencies
  Start-GodBackend
  Start-GodFrontend
  Write-GodLog "GOD is up. Open $(Get-FrontendUrl)"
}

function Reset-GodTown {
  Stop-GodAll
  Remove-Item -LiteralPath (Join-Path $TownDir "agents.json") -Force -ErrorAction SilentlyContinue
  Write-GodLog "Removed every saved AI resident"
}

switch ($Action.ToLowerInvariant()) {
  "start" { Start-GodAll }
  "restart" { Stop-GodAll; Start-GodAll }
  "stop" { Stop-GodAll }
  "status" { Show-GodStatus }
  "tail" {
    Import-GodEnv
    Get-Content -LiteralPath $BackendLog, $FrontendLog -Tail 80 -Wait
  }
  "setup" { Import-GodEnv; Install-GodDependencies }
  "reset" { Reset-GodTown }
  default { Show-Usage; exit 1 }
}
