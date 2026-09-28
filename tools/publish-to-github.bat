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
echo   Publish video-editor to GitHub (public repo, GPL v3)
echo ============================================================
echo.

where gh >nul 2>&1
set HAS_GH=0
if not errorlevel 1 set HAS_GH=1

REM ─── Step 1: Reach a state where the repo exists on GitHub ─────────
REM   Path A (preferred): gh CLI is installed AND auth works → we drive
REM   the whole thing.
REM   Path B (fallback):  gh missing or auth dead → open github.com/new
REM   in the browser pre-filled, ask the user to click "Create
REM   repository", then continue with plain git push.

set REPO_EXISTS=0
if "%HAS_GH%"=="1" (
    gh auth status >nul 2>&1
    if errorlevel 1 (
        echo [1/3] GitHub CLI present but not authenticated. Opening browser...
        echo.
        gh auth login --hostname github.com --web --git-protocol https
        if errorlevel 1 (
            echo.
            echo   Browser auth failed or cancelled. Falling back to manual flow.
            goto :MANUAL_CREATE
        )
        echo   Auth complete.
    ) else (
        echo [1/3] GitHub CLI auth: OK
    )
    REM Probe — does the repo already exist?
    gh api repos/trustxix/video-editor >nul 2>&1
    if not errorlevel 1 (
        echo [2/3] Repo trustxix/video-editor already exists.
        set REPO_EXISTS=1
    ) else (
        echo [2/3] Creating public repo via gh CLI (GPL v3 — source must be available)...
        gh repo create trustxix/video-editor --public ^
            --description "PyQt6 video editor with crop, trim, speed control, and batch export via FFmpeg"
        if errorlevel 1 (
            echo.
            echo   gh repo create failed. Falling back to manual flow.
            goto :MANUAL_CREATE
        )
        set REPO_EXISTS=1
        echo   Repo created.
    )
    goto :PUSH
)

:MANUAL_CREATE
echo.
echo ============================================================
echo   MANUAL repo creation step (one-time, ~20 seconds)
echo ============================================================
echo.
echo   1. Your browser will open to GitHub's "New repository" page,
echo      pre-filled with the repo name set to PUBLIC (GPL v3).
echo   2. Sign in if prompted, then click the green
echo      "Create repository" button.
echo   3. DO NOT add a README, .gitignore, or license — leave
echo      "Initialize this repository with:" all unchecked.
echo   4. Come back to this window and press any key to continue.
echo.
start "" "https://github.com/new?name=video-editor&visibility=public&description=PyQt6+video+editor+with+crop%%2C+trim%%2C+speed+control%%2C+and+batch+export+via+FFmpeg"
pause

REM ─── Step 3: Configure local remote + push master ──────────
:PUSH
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
    echo   Push failed. The most common causes:
    echo     a^) The repo was not actually created on GitHub yet — go
    echo        back to the browser and click "Create repository".
    echo     b^) Your git credential helper has no GitHub token cached.
    echo        Try: git credential-manager configure
    echo        or:  re-run this script after creating the repo.
    echo.
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
