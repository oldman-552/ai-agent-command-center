@echo off
rem Stop all services. Keeps your database, repositories and settings.
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\acc.ps1" stop
set "code=%errorlevel%"
if not "%code%"=="0" echo. & echo Something went wrong. See the logs folder for details.
pause
exit /b %code%
