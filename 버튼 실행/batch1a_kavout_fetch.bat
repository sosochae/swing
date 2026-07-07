@echo off
cd /d C:\MCP\Swing
call .venv\Scripts\activate

python scripts/fetch_kavout.py
