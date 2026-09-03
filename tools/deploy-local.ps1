# Sync a freshly built dist\Video Editor into the INSTALLED copy.
#
# Why this exists: the installed copy at "C:\Program Files\Video Editor" is the
# one actually launched day to day, but tools\build.bat only ever writes to
# dist\. For months that meant new builds were being tested against a stale
# installed exe — a 2026-04-26 binary was still in place on 2026-09-03, so
# fixes shipped in August looked like they had not worked.
#
# Run this after EVERY build. build.bat does not call it because writing to
# Program Files needs elevation and a failed copy must not fail the build.
#
#   powershell -ExecutionPolicy Bypass -File tools\deploy-local.ps1
#
# Never touches:
#   config\      - user settings, keybinds, logs, crash reports
#   unins000.*   - the Inno Setup uninstaller and its manifest

[CmdletBinding()]
param(
    [string]$InstallDir = "C:\Program Files\Video Editor",
    [string]$SourceDir,
    [switch]$Force   # stop a running instance instead of refusing
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $SourceDir) { $SourceDir = Join-Path $repo "dist\Video Editor" }

function Fail($msg) { Write-Error "[deploy-local] $msg"; exit 1 }

if (-not (Test-Path (Join-Path $SourceDir "Video Editor.exe"))) {
    Fail "No build found at '$SourceDir'. Run tools\build.bat first."
}
if (-not (Test-Path $InstallDir)) {
    Write-Host "[deploy-local] '$InstallDir' does not exist - nothing installed, skipping."
    exit 0
}

# Elevation: Program Files is not user-writable.
$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "Needs an elevated shell to write to '$InstallDir'."
}

# A running instance holds its exe and DLLs open; copying over them fails
# halfway and leaves a half-updated install, which is worse than not trying.
# Only an instance running FROM THIS DIRECTORY matters — the dist\ build and
# the installed build are different files and can run side by side.
$installFull = (Resolve-Path $InstallDir).Path.TrimEnd('\')
$running = @(
    Get-Process -Name "Video Editor" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($installFull, 'OrdinalIgnoreCase') }
)
if ($running.Count -gt 0) {
    if (-not $Force) {
        Fail ("'Video Editor.exe' is running from '$installFull' " +
              "(PID $($running.Id -join ', ')). Close it, or pass -Force.")
    }
    Write-Host "[deploy-local] Stopping running instance..."
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

$srcExe = Get-Item (Join-Path $SourceDir "Video Editor.exe")
$dstExePath = Join-Path $InstallDir "Video Editor.exe"

# Back up the exe being replaced so there is always one step back.
$backupDir = Join-Path $repo ("backups\" + (Get-Date -Format "yyyy-MM-dd"))
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
if (Test-Path $dstExePath) {
    $old = Get-Item $dstExePath
    $stamp = $old.LastWriteTime.ToString("yyyy-MM-dd_HHmmss")
    Copy-Item $dstExePath (Join-Path $backupDir "Video Editor.exe.$stamp") -Force
    Write-Host "[deploy-local] Backed up installed exe ($($old.LastWriteTime)) to $backupDir"
}

# lib\ and ffmpeg\ are mirrored: a PyInstaller onedir build pairs the exe with
# an exact lib\, so leftovers from an older build can shadow the new ones.
foreach ($sub in @("lib", "ffmpeg")) {
    $s = Join-Path $SourceDir $sub
    if (-not (Test-Path $s)) { continue }
    Write-Host "[deploy-local] Mirroring $sub ..."
    & robocopy $s (Join-Path $InstallDir $sub) /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "robocopy failed for '$sub' (exit $LASTEXITCODE)." }
}

Copy-Item $srcExe.FullName $InstallDir -Force
foreach ($f in @("LICENSE.txt", "NOTICES.md", "PRIVACY.md", "FFMPEG_SOURCE_OFFER.md")) {
    $p = Join-Path $SourceDir $f
    if (Test-Path $p) { Copy-Item $p $InstallDir -Force }
}

$dst = Get-Item $dstExePath
if ($dst.LastWriteTime -ne $srcExe.LastWriteTime) {
    Fail "Copy did not take: installed exe is $($dst.LastWriteTime), build is $($srcExe.LastWriteTime)."
}
Write-Host "[deploy-local] Installed exe is now $($dst.LastWriteTime)"
Write-Host "[deploy-local] config\ and unins000.* left untouched."
