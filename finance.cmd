@echo off
REM One entry point. Runs against the system Python (all deps already present).
REM Usage: finance run  |  finance init  |  finance period "Feb 2026"
python -m finance %*
