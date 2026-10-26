@echo off
rem Starts LocalFlow without a console window. Use LocalFlow-debug.bat to see logs live.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m localflow
