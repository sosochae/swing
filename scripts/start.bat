@echo off
cd /d C:\MCP\Swing
set PYTHONPATH=C:\MCP\Swing

call .venv\Scripts\activate

python scripts/health_check.py --setup

start /B python orchestrator\event_watcher.py

echo System started. File watcher running.
pause