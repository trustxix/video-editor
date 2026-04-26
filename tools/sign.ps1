# Authenticode-sign the built exe IF code-signing env vars are set.
# This is a NO-OP without SIGNCERT_PATH — explains what to set when ready.
#
# When the user buys an Authenticode certificate, signing becomes one
# env-var away — no code edits, no rebuild discipline drift:
#
#   $env:SIGNCERT_PATH      = 'C:\path\to\cert.pfx'
#   $env:SIGNCERT_PASSWORD  = '<password>'
#   $env:SIGNCERT_TIMESTAMP = 'http://timestamp.digicert.com'  (optional)
#   .\tools\release.ps1 -Version X.Y.Z
#
# Without a cert, Windows shows "Unknown publisher" SmartScreen warnings on
# every download, which kills install conversion. Until a cert is acquired,
# the build still produces a working unsigned exe.

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$exePath     = Join-Path $projectRoot "dist\Video Editor\Video Editor.exe"

if (-not (Test-Path $exePath)) {
    Write-Error "  sign: Build artifact not found at: $exePath"
    Write-Error "  sign: Run tools\build.bat first."
    exit 1
}

if (-not $env:SIGNCERT_PATH) {
    Write-Host "[sign] SIGNCERT_PATH not set - skipping code signing." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "[sign] To enable signing, set these env vars before re-running:"
    Write-Host "[sign]   SIGNCERT_PATH       full path to your .pfx cert"
    Write-Host "[sign]   SIGNCERT_PASSWORD   the cert password"
    Write-Host "[sign]   SIGNCERT_TIMESTAMP  (optional) RFC3161 timestamp URL"
    Write-Host "[sign] Default timestamp server: http://timestamp.digicert.com"
    exit 0
}

if (-not $env:SIGNCERT_PASSWORD) {
    Write-Error "  sign: SIGNCERT_PATH set but SIGNCERT_PASSWORD missing."
    exit 1
}

if (-not (Test-Path $env:SIGNCERT_PATH)) {
    Write-Error "  sign: Cert file not found at: $env:SIGNCERT_PATH"
    exit 1
}

# Locate signtool.exe (ships with Windows 10/11 SDK)
$signtool = Get-ChildItem `
    "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" `
    -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1

if (-not $signtool) {
    # Fallback: try PATH
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        $signtool = Get-Item $cmd.Source
    }
}

if (-not $signtool) {
    Write-Error "  sign: signtool.exe not found."
    Write-Error "  sign: Install: 'Windows 10 SDK' from Visual Studio Installer (~200 MB)."
    Write-Error "  sign: Or grab the standalone Windows SDK from microsoft.com."
    exit 1
}

$timestamp = if ($env:SIGNCERT_TIMESTAMP) {
    $env:SIGNCERT_TIMESTAMP
} else {
    "http://timestamp.digicert.com"
}

Write-Host "  sign: Signing $exePath..."
Write-Host "  sign:   signtool: $($signtool.FullName)"
Write-Host "  sign:   timestamp: $timestamp"

& $signtool.FullName sign `
    /f $env:SIGNCERT_PATH `
    /p $env:SIGNCERT_PASSWORD `
    /tr $timestamp /td sha256 /fd sha256 `
    /d "Video Editor" `
    $exePath

if ($LASTEXITCODE -ne 0) {
    Write-Error "  sign: signtool failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

# Verify the signature took
& $signtool.FullName verify /pa /q $exePath
if ($LASTEXITCODE -ne 0) {
    Write-Error "  sign: Signature verification failed (signtool verify exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-Host "  sign: Signed and verified." -ForegroundColor Green
