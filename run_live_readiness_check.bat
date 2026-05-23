@echo off
cd /d "%~dp0"
python src\live_readiness_check.py %*
