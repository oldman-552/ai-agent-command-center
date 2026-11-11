@echo off
rem First-time setup (double-click): checks Docker Desktop, creates .env with generated secrets, builds the images.
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\acc.ps1" install
set "code=%errorlevel%"
if not "%code%"=="0" echo. & echo Something went wrong. See the logs folder for details.
pause
exit /b %code%
