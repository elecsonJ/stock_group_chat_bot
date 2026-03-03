@echo off
setlocal
cd /d "%~dp0"
python src\scraper_job.py --backfill 48
