@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\run_task.ps1" -Job news_context -Root "%~dp0"
exit /b %ERRORLEVEL%
