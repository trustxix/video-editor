@echo off
REM Thin wrapper — all the launch logic lives in tools\launcher.ps1
REM (smart single-instance: focus if healthy, clean up only if wedged).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\launcher.ps1"
