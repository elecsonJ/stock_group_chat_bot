@echo off
cd /d "%~dp0"
python src\reconciliation_job.py %*
