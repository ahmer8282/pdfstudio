@echo off
rem Run PDF Studio straight from the source code (after Install has been run once)
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" main.py %*
