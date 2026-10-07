@echo off
rem Double-click to open the Festify app in your browser.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m festify.gui
pause
