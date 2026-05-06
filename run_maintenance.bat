@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\run_task.ps1" -Job maintenance -Root "%~dp0"
exit /b %ERRORLEVEL%
