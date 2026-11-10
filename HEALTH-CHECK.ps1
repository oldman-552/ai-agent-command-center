# Health of every service (database, RabbitMQ, Redis, workers, Browser Bridge).
# Run: right-click > "Run with PowerShell", or: powershell -ExecutionPolicy Bypass -File .\HEALTH-CHECK.ps1
& (Join-Path $PSScriptRoot "scripts\windows\acc.ps1") health
exit $LASTEXITCODE
