@echo off
rem Start the trainer app without a console window.
cd /d "%~dp0"
start "" .venv\Scripts\pythonw.exe -m trainer
