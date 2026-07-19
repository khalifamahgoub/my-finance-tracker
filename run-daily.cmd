@echo off
REM Daily launcher for the scheduled task. %~dp0 = this file's own folder,
REM so it always runs from the repo root regardless of the caller's cwd.
cd /d "%~dp0"
python -m finance run
