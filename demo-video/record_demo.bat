@echo off
rem Janus demo recording: starts the replica ward-office site and the Janus web app (recording
rem the browser Janus drives), then drives the web app. Videos land in demo-video\rec\.
cd /d "%~dp0.."
set JANUS_RECORD_DIR=%~dp0rec\agent
start "Janus replica site (close after recording)" /min .venv\Scripts\janus-bench.exe serve --site nagarpalika
start "Janus UI server (close after recording)" /min .venv\Scripts\janus.exe ui
.venv\Scripts\python.exe demo-video\demo_driver.py
echo.
echo Done. You can close this window.
