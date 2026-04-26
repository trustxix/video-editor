@echo off
setlocal enableextensions
title Publish video-editor to GitHub

REM ============================================================
REM   One-click publisher for trustxix/video-editor
REM
REM   Run me ONCE to:
REM     1. Authenticate the GitHub CLI (browser flow, ~30 sec)
REM     2. Create the private repo on GitHub if missing
REM     3. Push the master branch
REM
REM   Re-running is safe — every step is idempotent.
REM ============================================================

cd /d "%~dp0\.."

echo.
echo ============================================================
echo   Publish video-editor to GitHub (private repo)
echo ============================================================
echo.

where gh >nul 2>&1
if errorlevel 1 (
    echo ERROR: GitHub CLI ^(gh^) not found on PATH.
    echo Install with: winget install GitHub.cli
    pause
    exit /b 1
)

REM ─── Step 1: Authentication ────────────────────────────────
echo [1/3] Checking GitHub CLI auth...
gh auth status >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Auth required. Your default browser will open the GitHub
    echo   sign-in page. Approve the request, then come back here.
    echo.
    gh auth login --hostname github.com --web --git-protocol https
    if errorlevel 1 (
        echo.
        echo ERROR: GitHub authentication failed. Re-run this script.
        pause
        exit /b 1
    )
    echo   Auth complete.
) else (
    echo   Already authenticated.
)

REM ─── Step 2: Create the private repo if missing ────────────
echo.
echo [2/3] Checking remote repository state...
gh api repos/trustxix/video-editor >nul 2>&1
if errorlevel 1 (
    echo   Repo does not exist yet. Creating private repo...
    gh repo create trustxix/video-editor --private ^
        --description "PyQt6 video editor with crop, trim, speed control, and batch export via FFmpeg"
    if errorlevel 1 (
        echo.
        echo ERROR: Repo creation failed. See message above.
        pause
        exit /b 1
    )
    echo   Repo created.
) else (
    echo   Repo trustxix/video-editor already exists ^(skipping creation^).
)

REM ─── Step 3: Configure local remote + push master ──────────
echo.
echo [3/3] Configuring local remote and pushing master...

REM Reset origin to the canonical URL (idempotent — safe even if already correct)
git remote remove origin >nul 2>&1
git remote add origin https://github.com/trustxix/video-editor.git

git rev-parse --verify master >nul 2>&1
if errorlevel 1 (
    echo ERROR: Local 'master' branch not found.
    pause
    exit /b 1
)

git push -u origin master
if errorlevel 1 (
    echo.
    echo ERROR: Push failed. See message above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Done! Code is live at:
echo     https://github.com/trustxix/video-editor
echo.
echo   CI workflow:
echo     https://github.com/trustxix/video-editor/actions
echo.
echo   Issues page (used by the in-app bug reporter):
echo     https://github.com/trustxix/video-editor/issues
echo ============================================================
echo.
pause
endlocal
