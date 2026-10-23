@echo off
rem Runs lint and the unit tests, writing everything to demo-video\rec\checks.txt.
cd /d "%~dp0.."
if not exist demo-video\rec mkdir demo-video\rec
(
echo === ruff check ===
.venv\Scripts\ruff.exe check .
echo === ruff format --check ===
.venv\Scripts\ruff.exe format --check .
echo === pytest ===
.venv\Scripts\python.exe -m pytest -q
echo === done ===
) > demo-video\rec\checks.txt 2>&1
