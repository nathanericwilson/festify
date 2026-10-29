@echo off
rem festify public page: other people paste an Instagram post link and get a playlist made on your Spotify account.
rem Starts the page and a free Cloudflare tunnel, then opens the public address. Close this window to stop it.
cd /d "%~dp0"
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
title festify public page (close to stop)
".venv\Scripts\python.exe" -u -m festify.public --tunnel
pause
