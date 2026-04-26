@echo off
setlocal enableextensions enabledelayedexpansion
title Release video-editor

REM ============================================================
REM   One-click release pipeline for video-editor
REM
REM   Run me to:
REM     1. Read the current VERSION from src\core\version.py
REM     2. Run tools\release.ps1 (clean, pytest, build, ffmpeg,
REM        sign-if-cert, Inno Setup compile)
REM     3. Optionally tag v<VERSION> and upload to GitHub Release
REM
REM   The installer ends up at:
REM     dist\installer\VideoEditor-Setup-<VERSION>.exe
REM ============================================================

cd /d "%~dp0\.."

echo.
echo ============================================================
echo   video-editor — release pipeline
echo ============================================================
echo.

REM ─── Detect version from src\core\version.py ───────────────
REM Looks for `VERSION = "X.Y.Z"`, strips quotes and spaces.
for /f "tokens=2 delims==" %%v in ('findstr /b /c:"VERSION = " src\core\version.py') do (
    set VER=%%v
)
set VER=!VER:"=!
set VER=!VER: =!
if "!VER!"=="" (
    echo ERROR: Could not parse VERSION from src\core\version.py
    pause
    exit /b 1
)
echo Detected VERSION: !VER!

REM ─── Run the release pipeline ──────────────────────────────
echo.
echo [1/3] Running release.ps1 -Version !VER!...
echo.
powershell -ExecutionPolicy Bypass -NoProfile -File "tools\release.ps1" -Version !VER!
if errorlevel 1 (
    echo.
    echo ERROR: release.ps1 reported failure. See output above.
    pause
    exit /b 1
)

set INSTALLER=dist\installer\VideoEditor-Setup-!VER!.exe
if not exist "!INSTALLER!" (
    echo.
    echo ERROR: expected installer not found at !INSTALLER!
    pause
    exit /b 1
)
echo.
echo   Installer ready: !INSTALLER!

REM ─── Optionally publish a GitHub Release ───────────────────
echo.
echo [2/3] GitHub Release upload?
where gh >nul 2>&1
if errorlevel 1 (
    echo   gh CLI not on PATH; skipping GitHub Release step.
    goto :done
)
gh auth status >nul 2>&1
if errorlevel 1 (
    echo   Not authenticated to GitHub; skipping GitHub Release step.
    echo   ^(Run "Publish to GitHub.lnk" first to authenticate.^)
    goto :done
)

set /p GHRELEASE="   Tag v!VER! and upload to GitHub Releases? [y/N]: "
if /i "!GHRELEASE!"=="y" goto :do_release
goto :done

:do_release
echo.
echo [3/3] Tagging v!VER! and uploading installer...
git tag v!VER! 2>nul
git push origin v!VER!
if errorlevel 1 (
    echo   git push tag failed; continuing anyway.
)
gh release create v!VER! "!INSTALLER!" --title "v!VER!" --generate-notes
if errorlevel 1 (
    echo.
    echo ERROR: gh release create failed. See output above.
    pause
    exit /b 1
)
echo.
echo   Release published: https://github.com/trustxix/video-editor/releases/tag/v!VER!

:done
echo.
echo ============================================================
echo   Release pipeline complete.
echo.
echo   Installer:  !INSTALLER!
echo   Run-from-source: dist\Video Editor\Video Editor.exe
echo ============================================================
echo.
pause
endlocal
