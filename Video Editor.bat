@echo off
cd /d "%~dp0"

REM --- Pre-launch cleanup so the user never has to manually kill anything ---

REM 1. Kill any stale Video Editor pythonw still hanging around from a previous run.
REM    Matches by command line containing "video-editor" + main.py, so other
REM    pythonw processes on the machine are left alone.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'video-editor.*main\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

REM 2. Also sweep any orphaned ffmpeg spawned by the editor for audio extraction
REM    or export. Narrow match: only those whose parent was pythonw.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" | Where-Object { (Get-CimInstance Win32_Process -Filter (\"ProcessId=\" + $_.ParentProcessId) -ErrorAction SilentlyContinue).Name -eq 'pythonw.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

REM 3. Clear stale bytecode so code changes always take effect. pythonw has bitten
REM    us before by serving old .pyc files even after the .py was updated.
for /d /r "%~dp0src" %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
if exist "%~dp0__pycache__" rd /s /q "%~dp0__pycache__" 2>nul

REM --- Launch ---
start "" /b pythonw main.py
