# Single-command release pipeline.
# Cleans, builds, bundles FFmpeg, signs (if cert env vars set), compiles
# the Inno Setup installer.
#
# Usage:
#   .\tools\release.ps1                    # default version 0.1.0
#   .\tools\release.ps1 -Version 0.2.0
#   .\tools\release.ps1 -SkipFfmpegRefresh  # reuse already-bundled ffmpeg

param(
    [string]$Version = "0.1.0",
    [switch]$SkipFfmpegRefresh
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot

try {
    Write-Host ""
    Write-Host "=== Cleaning previous build ===" -ForegroundColor Cyan
    Remove-Item -Recurse -Force "build"           -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force "dist"            -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host "=== Running tests (regression gate) ===" -ForegroundColor Cyan
    & python -m pytest tests/ -q --ignore=tests/test_player_polish.py
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed (exit $LASTEXITCODE) -- aborting release"
    }

    Write-Host ""
    Write-Host "=== Building Video Editor (PyInstaller) ===" -ForegroundColor Cyan
    if ($SkipFfmpegRefresh) {
        $env:FORCE_FFMPEG_REFRESH = $null
    }
    & cmd /c "tools\build.bat"
    if ($LASTEXITCODE -ne 0) { throw "build.bat failed (exit $LASTEXITCODE)" }

    # Sanity check: the build artifact + bundled ffmpeg both exist
    $exePath = Join-Path $projectRoot "dist\Video Editor\Video Editor.exe"
    $ffmpegPath = Join-Path $projectRoot "dist\Video Editor\ffmpeg\ffmpeg.exe"
    if (-not (Test-Path $exePath))    { throw "Build artifact missing: $exePath" }
    if (-not (Test-Path $ffmpegPath)) { throw "FFmpeg missing in dist: $ffmpegPath" }

    Write-Host ""
    Write-Host "=== Compiling installer (Inno Setup) ===" -ForegroundColor Cyan

    # Locate ISCC.exe -- try winget install path first, then Program Files
    $isccCandidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    $iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) {
        $cmd = Get-Command iscc -ErrorAction SilentlyContinue
        if ($cmd) { $iscc = $cmd.Source }
    }
    if (-not $iscc) {
        throw "Inno Setup not found. Install with:`n  winget install JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements"
    }

    & $iscc "/DMyAppVersion=$Version" "tools\installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed (exit $LASTEXITCODE)" }

    $installer = Join-Path $projectRoot "dist\installer\VideoEditor-Setup-$Version.exe"
    if (-not (Test-Path $installer)) { throw "Installer not produced at expected path: $installer" }

    $installerSizeMB = [math]::Round((Get-Item $installer).Length / 1MB, 1)

    Write-Host ""
    Write-Host "=== Release ready ===" -ForegroundColor Green
    Write-Host ("  Installer: {0} ({1} MB)" -f $installer, $installerSizeMB)
    Write-Host ("  Bundle:    dist\Video Editor\")
    Write-Host ("  Version:   {0}" -f $Version)

    if (-not $env:SIGNCERT_PATH) {
        Write-Host ""
        Write-Host "  WARNING: Build is unsigned (SIGNCERT_PATH not set)." -ForegroundColor Yellow
        Write-Host "  Windows will show 'Unknown publisher' SmartScreen warning." -ForegroundColor Yellow
        Write-Host "  See tools\sign.ps1 for env vars to enable signing." -ForegroundColor Yellow
    }
} finally {
    Pop-Location
}
