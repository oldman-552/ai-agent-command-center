@echo off
rem DELETE ALL DATA of this installation (asks you to type RESET first).
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\acc.ps1" reset
set "code=%errorlevel%"
if not "%code%"=="0" echo. & echo Something went wrong. See the logs folder for details.
pause
exit /b %code%
