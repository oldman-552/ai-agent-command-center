# AI Agent Command Center — Windows launcher logic (INSTALL/START/STOP/UPDATE/HEALTH-CHECK/RESET/REAL-TESTS).
# Called by the .bat files in the project folder. Uses Docker Desktop only; installs nothing without your consent.
# Written for Windows PowerShell 5.1 (the one every Windows 10/11 has). This file is UTF-8 with a byte-order mark:
# keep the mark, or PowerShell 5.1 reads the non-ASCII characters below as quotation marks and stops.
param([Parameter(Mandatory = $true)][ValidateSet("start", "stop", "update", "reset", "install", "health", "realtests")][string]$Action)

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
function Warn([string]$m) { Write-Host "WARNING: $m" -ForegroundColor Yellow; Add-Content -LiteralPath $Log -Value "WARNING: $m" -Encoding UTF8 }

# Runs a program, appends everything it prints to the log and returns its exit code.
# Windows PowerShell 5.1 turns each redirected error-stream line of a program into an error record, and with
# $ErrorActionPreference = "Stop" the first such line ends the script. docker and git print their normal progress
# on that stream, so the preference is relaxed while the program runs; the exit code decides, not the stream.
function Invoke-Logged([string]$Exe, [string[]]$A) {
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { & $Exe @A 2>&1 | ForEach-Object { "$_" } | Add-Content -LiteralPath $Log -Encoding UTF8 }
  finally { $ErrorActionPreference = $old }
  return $LASTEXITCODE
}
# The same without keeping the output: for yes/no questions such as "is Docker running?".
function Invoke-Quiet([string]$Exe, [string[]]$A) {
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { & $Exe @A 2>&1 | Out-Null }
  finally { $ErrorActionPreference = $old }
  return $LASTEXITCODE
}
# The same, and every line is shown on the screen as it arrives: for runs you want to watch.
function Invoke-Shown([string]$Exe, [string[]]$A) {
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { & $Exe @A 2>&1 | ForEach-Object { $line = "$_"; Write-Host $line; Add-Content -LiteralPath $Log -Value $line -Encoding UTF8 } }
  finally { $ErrorActionPreference = $old }
  return $LASTEXITCODE
}
function Invoke-Docker([string[]]$a) { return (Invoke-Logged "docker.exe" $a) }

# Asks for a secret without showing it. The value lives in this window's memory only: never in a file or the log.
function Read-Secret([string]$Prompt) {
  $sec = Read-Host -AsSecureString $Prompt
  return [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}

function Require-Docker {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker Desktop is not installed. Download it from https://www.docker.com/products/docker-desktop/ , install it, start it, then run START.bat again."
  }
  if ((Invoke-Quiet "docker.exe" @("info")) -ne 0) { Fail "Docker Desktop is installed but not running. Start Docker Desktop, wait until it says 'Engine running', then try again." }
  if ((Invoke-Quiet "docker.exe" @("compose", "version")) -ne 0) { Fail "Docker Compose v2 is missing. Update Docker Desktop." }
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
  foreach ($k in "database", "rabbitmq", "redis") { Say ("  {0,-19} {1}" -f $k, $h.services.$k) }
  Say ("  {0,-19} {1} ({2} running)" -f "workers", $h.services.workers.status, @($h.services.workers.instances).Count)
  Say ("  {0,-19} {1} (optional, experimental)" -f "browser_bridge", $h.services.browser_bridge.status)
  $ab = $h.services.arena_local_bridge
  if ($ab) {
    if ($ab.status -eq "not_configured") { Say ("  {0,-19} {1}" -f "arena_local_bridge", "not configured (optional: Human-Assisted workers need none)") }
    else { Say ("  {0,-19} {1} ({2} configured, {3} ready when last checked)" -f "arena_local_bridge", $ab.status, $ab.configured, $ab.ready) }
  }
  return ($h.status -eq "ok")
}

# Arena Local Bridge runs on this computer; the platform runs in containers. Only a check made INSIDE a container
# says whether the workers can reach the bridge: the address that opens it in your browser (http://127.0.0.1:20140)
# is not the one a container uses (http://host.docker.internal:20140). The check sends nothing to Arena and prints
# no key and no account address.
function Invoke-BridgeProbe([string]$Service) {
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { $out = & docker.exe @Compose exec -T $Service python -m app.connectivity.arena_bridge.probe 2>&1 | ForEach-Object { "$_" } }
  finally { $ErrorActionPreference = $old }
  return @{ Code = $LASTEXITCODE; Lines = @($out) }
}

# The bridge is optional, so its result is reported and never fails the health check.
# Probe exit codes: 0 = nothing configured or everything ready, 10 = a configured bridge cannot be used, anything
# else = the check itself did not run (for example the api service is stopped).
function Test-ArenaBridge {
  Say ""
  $api = Invoke-BridgeProbe "api"
  foreach ($line in $api.Lines) { Say $line }
  if ($api.Code -eq 0 -or $api.Code -eq 10) {
    $worker = Invoke-BridgeProbe "worker"   # the worker is the process that sends the requests
    if ($worker.Code -eq $api.Code) { Say "  The worker container reports the same result." }
    else {
      Warn "The worker container reports a different result than the API container:"
      foreach ($line in $worker.Lines) { Say $line }
    }
  }
  if ($api.Code -eq 10) {
    Warn "An Arena Local Bridge is configured but cannot be used right now. Automatic workers hand their tasks to people until it is fixed. See docs\ARENA_LOCAL_BRIDGE.md."
  } elseif ($api.Code -ne 0) {
    Warn "The Arena Local Bridge check could not run inside the API container. Is the platform started (START.bat)?"
  }
}

switch ($Action) {
  "install" {
    Require-Docker; Ensure-Env
    Say "Building images (first time can take several minutes)…"
    if ((Invoke-Docker ($Compose + @("build"))) -ne 0) { Fail "Image build failed." }
    if ((Invoke-Quiet "docker.exe" @("image", "inspect", "acc/sandbox:python")) -ne 0) {
      Say "Building the agent sandbox image…"
      if ((Invoke-Docker @("build", "-t", "acc/sandbox:python", (Join-Path $Root "docker\sandbox"))) -ne 0) { Say "WARNING: sandbox image build failed (see log)." }
    }
    Say "Installed. Double-click START.bat."
    Say "AI Workforce: after signing in, open 'AI Workforce' and choose the Manager's model. The four workers start as"
    Say "Human-Assisted, so nothing is sent anywhere until you configure it. Arena Local Bridge is optional and is a"
    Say "separate program on this computer: see docs\ARENA_LOCAL_BRIDGE.md."
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
        if ((Invoke-Logged "git.exe" @("pull", "--ff-only")) -ne 0) { Fail "git pull failed (see log)." }
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
    Write-Host "RESET deletes ALL data of this installation:" -ForegroundColor Yellow
    Write-Host "  - the database: users, agents, tasks, AI Workforce objectives and results, bridge settings and stored keys" -ForegroundColor Yellow
    Write-Host "  - repositories and workspaces, files attached to results, browser profiles" -ForegroundColor Yellow
    Write-Host "Your .env (passwords, encryption key) is kept. Arena Local Bridge is a separate program: it and its" -ForegroundColor Yellow
    Write-Host "accounts are not touched. This cannot be undone." -ForegroundColor Yellow
    $answer = Read-Host "Type RESET to continue"
    if ($answer -cne "RESET") { Say "Cancelled. Nothing was deleted."; exit 0 }
    if ((Invoke-Docker ($Compose + @("down", "--volumes", "--remove-orphans"))) -ne 0) { Fail "docker compose down failed." }
    Say "All data deleted. Double-click START.bat to start fresh."
  }
  "realtests" {
    # The two checks that leave this computer (requirements WF-20 and WF-21). They run in a throwaway container, on a
    # separate test database; the installation's data, broker and Redis are not touched.
    Require-Docker; Ensure-Env
    Write-Host "REAL TESTS: the two checks that nobody could run during development."
    Write-Host "  1  The Manager on a real Claude model    needs your API key; makes about 5 to 8 requests that you pay for"
    Write-Host "  2  Four workers on Arena Local Bridge    sends 5 short prompts to Arena through your four accounts"
    $choice = Read-Host "Type 1 or 2 (anything else cancels)"
    $names = "REAL_TIER_REQUIRED", "REAL_MANAGER_API_KEY", "REAL_MANAGER_MODEL", "REAL_PROVIDER_WORKERS", "REAL_ARENA_BRIDGE_KEY", "REAL_ARENA_BRIDGE_URL", "REAL_ARENA_ACCEPT_TERMS", "REAL_ARENA_SESSIONS"
    $code = -1
    try {
      if ($choice -eq "1") {
        $model = (Read-Host "Model id, exactly as the provider names it (the Claude model you want for the Manager)").Trim()
        if (-not $model) { Say "Cancelled. Nothing was sent."; exit 0 }
        $key = Read-Secret "API key (not shown, not stored)"
        if ($key.Length -lt 8) { Say "Cancelled: no key. Nothing was sent."; exit 0 }
        $env:REAL_TIER_REQUIRED = "real_provider"; $env:REAL_MANAGER_MODEL = $model; $env:REAL_MANAGER_API_KEY = $key
        $more = Read-Host "Also let the four workers answer on the same model? This makes more requests. Type YES, or press Enter for no"
        if ($more -ceq "YES") { $env:REAL_PROVIDER_WORKERS = "1" }
        $pytest = @("-m", "real_provider", "tests/test_real_provider.py", "-v", "-s")
      } elseif ($choice -eq "2") {
        Write-Host "Before you continue:" -ForegroundColor Yellow
        Write-Host "  - Arena Local Bridge is running on this computer, with four Arena accounts signed in and verified." -ForegroundColor Yellow
        Write-Host "  - Each of the four accounts has one conversation of its own in the bridge." -ForegroundColor Yellow
        Write-Host "  - The bridge is not an official Arena API. Arena's terms of use apply to you, and this test sends" -ForegroundColor Yellow
        Write-Host "    5 short prompts to Arena through your accounts." -ForegroundColor Yellow
        $answer = Read-Host "Type YES to acknowledge this and continue"
        if ($answer -cne "YES") { Say "Cancelled. Nothing was sent."; exit 0 }
        $key = Read-Secret "Bridge key (ARENA_AGENT_BRIDGE_KEY, shown in the bridge window; not shown here, not stored)"
        if ($key.Length -lt 8) { Say "Cancelled: no key. Nothing was sent."; exit 0 }
        $url = (Read-Host "Bridge address as a container sees it; press Enter for http://host.docker.internal:20140").Trim()
        $env:REAL_TIER_REQUIRED = "real_arena"; $env:REAL_ARENA_ACCEPT_TERMS = "yes"; $env:REAL_ARENA_BRIDGE_KEY = $key
        if ($url) { $env:REAL_ARENA_BRIDGE_URL = $url }
        $pytest = @("-m", "real_arena", "tests/test_real_arena.py", "-v", "-s")
      } else { Say "Cancelled. Nothing was sent."; exit 0 }
      Say "Running. The first run builds the image and downloads the test tools; this can take a few minutes."
      $code = Invoke-Shown "docker.exe" ($Compose + @("run", "--rm", "-T", "realtests") + $pytest)
    } finally {
      foreach ($n in $names) { Remove-Item -LiteralPath "Env:\$n" -ErrorAction SilentlyContinue }
      $key = $null
    }
    if ($code -eq 0) { Say "PASSED. The log is your evidence (it contains no key): $Log" }
    else { Fail "The real test did not pass (exit code $code). The lines above and the log show the step that failed." }
  }
  "health" {
    Require-Docker
    & docker.exe @Compose ps
    $ok = Show-Health
    Test-ArenaBridge
    if ($ok) { exit 0 } else { exit 1 }
  }
}
