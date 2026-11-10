# First-time setup: checks Docker Desktop, creates .env with generated secrets, builds images.
# Run: right-click > "Run with PowerShell", or: powershell -ExecutionPolicy Bypass -File .\INSTALL.ps1
& (Join-Path $PSScriptRoot "scripts\windows\acc.ps1") install
exit $LASTEXITCODE
