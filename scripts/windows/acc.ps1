# AI Agent Command Center — Windows launcher logic (START/STOP/UPDATE/RESET-DEV/INSTALL/HEALTH-CHECK).
# Called by the .bat files in the project folder. Uses Docker Desktop only; installs nothing without your consent.
param([Parameter(Mandatory = $true)][ValidateSet("start", "stop", "update", "reset", "install", "health")][string]$Action)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Logs = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$Log = Join-Path $Logs ("{0}-{1}.log" -f $Action, (Get-Date -Format "yyyyMMdd-HHmmss"))
$EnvFile = Join-Path $Root ".env"
$Compose = @("compose", "--project-directory", $Root, "-f", (Join-Path $Root "docker-compose.yml"))
if ($env:ACC_PROFILE -eq "production") { $Compose += @("-f", (Join-Path $Root "docker-compose.prod.yml")) }

function Say([string]$m) { Write-Host $m; Add-Content -LiteralPath $Log -Value $m -Encoding UTF8 }
function Fail([string]$m) {
  Write-Host "ERROR: $m" -ForegroundColor Red; Add-Content -LiteralPath $Log -Value "ERROR: $m" -Encoding UTF8
  Write-Host "Log: $Log"; exit 1
}
function Invoke-Docker([string[]]$a) { & docker.exe @a *>> $Log; return $LASTEXITCODE }

function Require-Docker {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker Desktop is not installed. Download it from https://www.docker.com/products/docker-desktop/ , install it, start it, then run START.bat again."
  }
  & docker info *> $null
  if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop is installed but not running. Start Docker Desktop, wait until it says 'Engine running', then try again." }
  & docker compose version *> $null
  if ($LASTEXITCODE -ne 0) { Fail "Docker Compose v2 is missing. Update Docker Desktop." }
}

function Get-EnvValue([string]$Key) {
  if (-not (Test-Path -LiteralPath $EnvFile)) { return "" }
  $line = Get-Content -LiteralPath $EnvFile -Encoding UTF8 | Where-Object { $_ -match "^$Key=" } | Select-Object -Last 1
  if ($line) { return $line.Substring($Key.Length + 1) } else { return "" }
}
function Port([string]$Key, [string]$Default) { $v = Get-EnvValue $Key; if ($v) { $v } else { $Default } }

function New-Secret([int]$Bytes) {
  $b = New-Object byte[] $Bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  return ([Convert]::ToBase64String($b)).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}
function New-FernetKey {
  $b = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  return ([Convert]::ToBase64String($b)).Replace("+", "-").Replace("/", "_")  # keeps the '=' padding Fernet needs
}
function Set-IfEmpty([string]$Key, [string]$Value) {
  $lines = Get-Content -LiteralPath $EnvFile -Encoding UTF8
  if ($lines -contains "$Key=") {
    $lines = $lines | ForEach-Object { if ($_ -eq "$Key=") { "$Key=$Value" } else { $_ } }
    [System.IO.File]::WriteAllLines($EnvFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
    return $true
  }
  if (-not ($lines | Where-Object { $_ -like "$Key=*" })) {  # missing entirely (hand-made or partial .env): add it
    $lines = @($lines) + "$Key=$Value"
    [System.IO.File]::WriteAllLines($EnvFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
    return $true
  }
  return $false
}

function Ensure-Env {
  $example = Join-Path $Root ".env.example"
  if (-not (Test-Path -LiteralPath $example)) { Fail ".env.example is missing — is this the project folder?" }
  if (-not (Test-Path -LiteralPath $EnvFile)) { Copy-Item -LiteralPath $example -Destination $EnvFile; Say "Created .env from .env.example" }
  $have = @(Get-Content -LiteralPath $EnvFile -Encoding UTF8)
  $missing = @(Get-Content -LiteralPath $example -Encoding UTF8 | Where-Object { $_ -match '^([A-Z][A-Z0-9_]*)=' -and -not ($have -like "$($Matches[1])=*") })
  if ($missing.Count -gt 0) {  # a hand-made or partial .env: add what it lacks (existing values are kept)
    [System.IO.File]::WriteAllLines($EnvFile, @($have + $missing), (New-Object System.Text.UTF8Encoding($false)))
    Say "Added $($missing.Count) missing settings to .env from .env.example"
  }
  if (Set-IfEmpty "JWT_SECRET" (New-Secret 48)) { Say "Generated JWT_SECRET" }
  if (Set-IfEmpty "APP_ENCRYPTION_KEY" (New-FernetKey)) { Say "Generated APP_ENCRYPTION_KEY (back up .env: losing it makes stored credentials unreadable)" }
  if (Set-IfEmpty "POSTGRES_PASSWORD" (New-Secret 24)) { Say "Generated POSTGRES_PASSWORD" }
  if (Set-IfEmpty "RABBITMQ_PASSWORD" (New-Secret 24)) { Say "Generated RABBITMQ_PASSWORD" }
  if (-not (Get-EnvValue "ADMIN_PASSWORD")) {
    do {
      $sec = Read-Host -AsSecureString "Choose the Super Admin password (min 12 characters)"
      $pw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    } while ($pw.Length -lt 12)
    Set-IfEmpty "ADMIN_PASSWORD" $pw | Out-Null
  }
}

function Wait-Http([string]$Url, [string]$Label, [int]$Tries) {
  for ($i = 0; $i -lt $Tries; $i++) {
    try { $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5; if ($r.StatusCode -lt 500) { Say "  ${Label}: ready"; return } } catch { }
    Start-Sleep -Seconds 2
  }
  Fail "$Label did not become ready ($Url). Run HEALTH-CHECK.bat or 'docker compose logs'."
}

function Show-Health {
  $url = "http://localhost:$(Port 'API_PORT' '8000')/api/health"
  try { $h = Invoke-RestMethod -Uri $url -TimeoutSec 10 } catch { Say "API: DOWN ($url)"; return $false }
  Say ("Overall: {0}" -f $h.status)
  foreach ($k in "database", "rabbitmq", "redis") { Say ("  {0,-15} {1}" -f $k, $h.services.$k) }
  Say ("  {0,-15} {1} ({2} running)" -f "workers", $h.services.workers.status, @($h.services.workers.instances).Count)
  Say ("  {0,-15} {1} (optional, experimental)" -f "browser_bridge", $h.services.browser_bridge.status)
  return ($h.status -eq "ok")
}

switch ($Action) {
  "install" {
    Require-Docker; Ensure-Env
    Say "Building images (first time can take several minutes)…"
    if ((Invoke-Docker ($Compose + @("build"))) -ne 0) { Fail "Image build failed." }
    & docker image inspect acc/sandbox:python *> $null
    if ($LASTEXITCODE -ne 0) {
      Say "Building the agent sandbox image…"
      if ((Invoke-Docker @("build", "-t", "acc/sandbox:python", (Join-Path $Root "docker\sandbox"))) -ne 0) { Say "WARNING: sandbox image build failed (see log)." }
    }
    Say "Installed. Double-click START.bat."
  }
  "start" {
    Require-Docker; Ensure-Env
    Say "Building and starting services…"
    if ((Invoke-Docker ($Compose + @("up", "-d", "--build"))) -ne 0) { Fail "docker compose up failed." }
    Say "Waiting for services (database migrations run automatically)…"
    Wait-Http "http://localhost:$(Port 'API_PORT' '8000')/api/health" "API" 120
    $web = "http://localhost:$(Port 'FRONTEND_PORT' '3000')"
    Wait-Http "$web/login" "Web app" 120
    Show-Health | Out-Null
    Say "AI Agent Command Center is running: $web"
    Start-Process $web
  }
  "stop" {
    Require-Docker
    if ((Invoke-Docker ($Compose + @("stop"))) -ne 0) { Fail "docker compose stop failed." }
    Say "Stopped. Your data is kept; double-click START.bat to start again."
  }
  "update" {
    Require-Docker
    if (Get-Command git -ErrorAction SilentlyContinue) {
      Push-Location $Root
      try {
        if (git status --porcelain --untracked-files=no) { Fail "You have local code changes; commit or stash them first." }
        git pull --ff-only *>> $Log; if ($LASTEXITCODE -ne 0) { Fail "git pull failed (see log)." }
      } finally { Pop-Location }
    }
    if ((Invoke-Docker ($Compose + @("build", "--pull"))) -ne 0) { Fail "Image build failed." }
    if ((Invoke-Docker ($Compose + @("up", "-d"))) -ne 0) { Fail "Restart failed." }
    Wait-Http "http://localhost:$(Port 'API_PORT' '8000')/api/health" "API" 120
    Show-Health | Out-Null
    Say "Updated."
  }
  "reset" {
    Require-Docker
    Write-Host "RESET-DEV deletes ALL data of this installation: database, task history, repositories and workspaces." -ForegroundColor Yellow
    Write-Host "Your .env (passwords, encryption key) is kept. This cannot be undone." -ForegroundColor Yellow
    $answer = Read-Host "Type RESET to continue"
    if ($answer -cne "RESET") { Say "Cancelled. Nothing was deleted."; exit 0 }
    if ((Invoke-Docker ($Compose + @("down", "--volumes", "--remove-orphans"))) -ne 0) { Fail "docker compose down failed." }
    Say "All data deleted. Double-click START.bat to start fresh."
  }
  "health" {
    Require-Docker
    & docker @Compose ps
    if (Show-Health) { exit 0 } else { exit 1 }
  }
}
