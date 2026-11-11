@echo off
rem The two checks that leave this computer: the Manager on a real model, workers on your Arena Local Bridge.
rem Asks before anything is sent. Uses a separate test database; your data is not touched.
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\acc.ps1" realtests
set "code=%errorlevel%"
if not "%code%"=="0" echo. & echo Something went wrong. See the logs folder for details.
pause
exit /b %code%
