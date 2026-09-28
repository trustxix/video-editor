# Single-command release pipeline:
#   tests -> PyInstaller build (tools\build.bat, pinned FFmpeg) -> portable
#   zip -> Inno Setup installer -> SHA256 files
#
# The version comes from src\core\version.py (VERSION = "x.y.z"). Bump it
# there and nowhere else.
#
# Outputs, in dist\:
#   Video-Editor-v<ver>-win64.zip          (+ .sha256)
#   installer\VideoEditor-Setup-<ver>.exe  (+ .sha256)
# Requires dist\ffmpeg-<ffmpeg version>-source.tar (+ .sha256) from
# tools/make_ffmpeg_source.sh; every release must carry it.
# Earlier versions' zips and installers in dist\ are left alone.
#
# Usage:
#   .\tools\release.ps1

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot

# sha256sum format ("<hash>  <name>"), so `sha256sum -c` can check it. .NET
# directly because Get-FileHash has been missing in some -NoProfile sessions.
function Write-Sha256File([string]$path) {
    $stream = [System.IO.File]::OpenRead($path)
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = -join ($hasher.ComputeHash($stream) | ForEach-Object { $_.ToString("X2") })
    } finally {
        $stream.Dispose()
        $hasher.Dispose()
    }
    $line = "$hash  $(Split-Path -Leaf $path)`n"
    [System.IO.File]::WriteAllText("$path.sha256", $line, (New-Object System.Text.UTF8Encoding $false))
    return $hash
}

try {
    $versionPy = Get-Content -Raw "src\core\version.py"
    if (-not ($versionPy -match '(?m)^VERSION\s*=\s*"(\d+\.\d+\.\d+)"')) {
        throw 'Could not read VERSION = "x.y.z" from src\core\version.py'
    }
    $Version = $Matches[1]
    # A tagged version is published: rebuilding it from newer code would put
    # different binaries under the same name and overwrite dist's copies.
    $tagged = & git tag --list "v$Version"
    if ($LASTEXITCODE -ne 0) { throw "git tag --list failed (exit $LASTEXITCODE)" }
    if ($tagged) {
        throw "v$Version is already tagged. Bump VERSION in src\core\version.py first."
    }
    $bundle  = Join-Path $projectRoot "dist\Video Editor"
    # GPLv3 6(d): the FFmpeg source must be offered in the same place as the
    # binaries, so every release carries it. Checked first: it takes a while
    # to build.
    $ffLock   = Get-Content -Raw "tools\ffmpeg.lock.json" | ConvertFrom-Json
    $ffSource = Join-Path $projectRoot "dist\ffmpeg-$($ffLock.version)-source.tar"
    if (-not (Test-Path $ffSource) -or -not (Test-Path "$ffSource.sha256")) {
        throw "Missing $ffSource (+ .sha256). Build it in Git Bash: tools/make_ffmpeg_source.sh"
    }
    Write-Host "=== Releasing v$Version ===" -ForegroundColor Cyan

    Write-Host ""
    Write-Host "=== Cleaning previous build ===" -ForegroundColor Cyan
    Remove-Item -Recurse -Force "build"   -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $bundle   -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host "=== Running tests (regression gate) ===" -ForegroundColor Cyan
    & python -m pytest tests/ -q --ignore=tests/test_player_polish.py
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed (exit $LASTEXITCODE) -- aborting release"
    }

    Write-Host ""
    Write-Host "=== Building Video Editor (PyInstaller) ===" -ForegroundColor Cyan
    & cmd /c "tools\build.bat"
    if ($LASTEXITCODE -ne 0) { throw "build.bat failed (exit $LASTEXITCODE)" }

    $exePath    = Join-Path $bundle "Video Editor.exe"
    $ffmpegPath = Join-Path $bundle "ffmpeg\ffmpeg.exe"
    if (-not (Test-Path $exePath))    { throw "Build artifact missing: $exePath" }
    if (-not (Test-Path $ffmpegPath)) { throw "FFmpeg missing in dist: $ffmpegPath" }
    # A bundle that was launched after the build holds that user's settings
    # and logs in config\. Never package them.
    $configFiles = @(Get-ChildItem -Force -Recurse (Join-Path $bundle "config") -ErrorAction SilentlyContinue)
    if ($configFiles.Count -gt 0) {
        throw "dist\Video Editor\config is not empty ($($configFiles.Count) items); rebuild before packaging"
    }

    Write-Host ""
    Write-Host "=== Packaging portable zip ===" -ForegroundColor Cyan
    $zip = Join-Path $projectRoot "dist\Video-Editor-v$Version-win64.zip"
    if (Test-Path $zip) { Remove-Item -Force $zip }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    # includeBaseDirectory: entries start with "Video Editor/", as in v0.1.1.
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $bundle, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)
    $zipHash = Write-Sha256File $zip

    Write-Host ""
    Write-Host "=== Compiling installer (Inno Setup) ===" -ForegroundColor Cyan
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
    $installerHash = Write-Sha256File $installer

    Write-Host ""
    Write-Host "=== Release v$Version ready ===" -ForegroundColor Green
    foreach ($artifact in @(@($zip, $zipHash), @($installer, $installerHash))) {
        $sizeMB = [math]::Round((Get-Item $artifact[0]).Length / 1MB, 1)
        Write-Host ("  {0} ({1} MB)" -f $artifact[0], $sizeMB)
        Write-Host ("    SHA256 {0}" -f $artifact[1])
    }
    Write-Host ("  {0} (FFmpeg corresponding source)" -f $ffSource)
} finally {
    Pop-Location
}
