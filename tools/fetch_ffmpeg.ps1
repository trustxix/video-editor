# Download the BtbN FFmpeg-Builds win64-gpl release, verify what we got,
# extract ffmpeg.exe + ffprobe.exe + LICENSE files into dist\Video Editor\ffmpeg\
#
# Honors:
#   $env:FORCE_FFMPEG_REFRESH = '1'  → re-download even if already present
#   $env:GITHUB_TOKEN                → avoid GitHub API rate limit (60/hr unauth)
#
# Exits non-zero on any failure so build.bat / release.ps1 can chain && safely.

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot   = Split-Path -Parent $PSScriptRoot
$distFfmpegDir = Join-Path $projectRoot "dist\Video Editor\ffmpeg"
$tempZip       = Join-Path $env:TEMP    "ffmpeg-release.zip"
$tempDir       = Join-Path $env:TEMP    "ffmpeg-extract"

# Skip if already bundled
if ((Test-Path (Join-Path $distFfmpegDir "ffmpeg.exe")) -and -not $env:FORCE_FFMPEG_REFRESH) {
    Write-Host "[fetch_ffmpeg] FFmpeg already present at $distFfmpegDir"
    Write-Host "[fetch_ffmpeg] Set `$env:FORCE_FFMPEG_REFRESH = '1' to redownload."
    exit 0
}

Write-Host "[fetch_ffmpeg] Querying GitHub for latest BtbN FFmpeg release..."

$apiUrl = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
$headers = @{ "User-Agent" = "VideoEditor-Build" }
if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "token $env:GITHUB_TOKEN" }

try {
    $rel = Invoke-RestMethod -Uri $apiUrl -Headers $headers
} catch {
    Write-Error "[fetch_ffmpeg] GitHub API call failed: $_"
    Write-Error "[fetch_ffmpeg] Hint: set `$env:GITHUB_TOKEN to bypass the 60/hour anonymous rate limit."
    exit 1
}

# Pick the win64-gpl static build (matches the gyan.dev dev install for parity).
# Filename pattern: ffmpeg-master-latest-win64-gpl.zip
$asset = $rel.assets | Where-Object {
    $_.name -like "ffmpeg-master-latest-win64-gpl*.zip" -and
    $_.name -notlike "*shared*"
} | Select-Object -First 1

if (-not $asset) {
    Write-Error "[fetch_ffmpeg] Could not find win64-gpl release asset in latest release '$($rel.name)'."
    Write-Error "[fetch_ffmpeg] Available assets:"
    $rel.assets | ForEach-Object { Write-Error "  - $($_.name)" }
    exit 1
}

$sizeMB = [math]::Round($asset.size / 1MB, 1)
Write-Host "[fetch_ffmpeg] Downloading $($asset.name) ($sizeMB MB)..."

# Use BITS for resumable download if available, else fall back to Invoke-WebRequest
if (Test-Path $tempZip) { Remove-Item -Force $tempZip }
try {
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tempZip -UseBasicParsing -Headers $headers
} catch {
    Write-Error "[fetch_ffmpeg] Download failed: $_"
    exit 1
}

# Record SHA256 of what we got. BtbN does not publish per-asset hashes in the
# release JSON, but the asset URL is HTTPS+TLS-pinned by github.com so the
# integrity is bounded by GitHub's TLS chain. We log the hash for audit/debug.
# Get-FileHash has shown up missing in some -NoProfile invocations; fall back
# to a .NET hash so a stripped-down PS environment doesn't fail the build.
$sha = $null
try {
    $sha = (Get-FileHash -Path $tempZip -Algorithm SHA256).Hash
} catch {
    Write-Host "[fetch_ffmpeg] Get-FileHash unavailable; using .NET SHA256 fallback"
    try {
        $stream = [System.IO.File]::OpenRead($tempZip)
        $hasher = [System.Security.Cryptography.SHA256]::Create()
        $sha = -join ($hasher.ComputeHash($stream) | ForEach-Object { $_.ToString("X2") })
    } finally {
        if ($stream) { $stream.Dispose() }
        if ($hasher) { $hasher.Dispose() }
    }
}
if (-not $sha) { $sha = "(unavailable)" }
Write-Host "[fetch_ffmpeg] SHA256: $sha"

# Extract
if (Test-Path $tempDir) { Remove-Item -Recurse -Force $tempDir }
Expand-Archive -Path $tempZip -DestinationPath $tempDir

$exeSrc   = Get-ChildItem -Path $tempDir -Recurse -Filter "ffmpeg.exe"  | Select-Object -First 1
$probeSrc = Get-ChildItem -Path $tempDir -Recurse -Filter "ffprobe.exe" | Select-Object -First 1

if (-not $exeSrc -or -not $probeSrc) {
    Write-Error "[fetch_ffmpeg] ffmpeg.exe / ffprobe.exe not found inside the archive."
    exit 1
}

# Place into dist
New-Item -ItemType Directory -Force -Path $distFfmpegDir | Out-Null
Copy-Item -Path $exeSrc.FullName   -Destination (Join-Path $distFfmpegDir "ffmpeg.exe")  -Force
Copy-Item -Path $probeSrc.FullName -Destination (Join-Path $distFfmpegDir "ffprobe.exe") -Force

# Copy ALL license files from the archive's root and any subfolders.
# BtbN ships: LICENSE.txt, COPYING.GPLv2, COPYING.GPLv3, COPYING.LGPLv2.1, etc.
$licDir = Join-Path $distFfmpegDir "licenses"
if (Test-Path $licDir) { Remove-Item -Recurse -Force $licDir }
New-Item -ItemType Directory -Force -Path $licDir | Out-Null

$licenseFiles = Get-ChildItem -Path $tempDir -Recurse -File | Where-Object {
    $_.Name -match "^(LICENSE|COPYING|README)" -or $_.Name -like "*.txt"
} | Where-Object { $_.Name -notlike "ffmpeg-all*" }  # exclude doc dumps

# Avoid duplicates (multiple identical LICENSE.txt files in nested folders).
$seen = @{}
foreach ($lic in $licenseFiles) {
    if ($seen.ContainsKey($lic.Name)) { continue }
    $seen[$lic.Name] = $true
    Copy-Item -Path $lic.FullName -Destination $licDir -Force
}

# Record the asset metadata so the FFmpeg source-offer document can cite it.
$infoPath = Join-Path $distFfmpegDir "BUNDLE_INFO.txt"
@"
FFmpeg bundle info (recorded at build time)
============================================
Source release: $($rel.name)
Asset filename: $($asset.name)
Asset URL:      $($asset.browser_download_url)
Asset SHA256:   $sha
Asset size:     $sizeMB MB
Bundled at:     $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')

Per the GPL, the source for this exact build is available from BtbN at:
  https://github.com/BtbN/FFmpeg-Builds
For the upstream FFmpeg source:
  https://ffmpeg.org/download.html
"@ | Set-Content -Path $infoPath -Encoding UTF8

# Cleanup temp
Remove-Item -Force $tempZip
Remove-Item -Recurse -Force $tempDir

# Final verification — bundled binary actually launches?
$bundledFfmpeg = Join-Path $distFfmpegDir "ffmpeg.exe"
$verResult = & $bundledFfmpeg -hide_banner -version 2>&1 | Select-Object -First 1
if ($LASTEXITCODE -ne 0) {
    Write-Error "[fetch_ffmpeg] Bundled ffmpeg.exe failed to run: $verResult"
    exit 1
}

# The formant-shift feature requires librubberband ('rubberband' filter). Fail
# the build loudly if the bundled binary lacks it rather than silently shipping
# an app where the formant slider does nothing. BtbN win64-gpl builds include
# --enable-librubberband; this guards against an upstream build-flag change.
# Out-String collapses the multi-line filter list into ONE string so
# -notmatch is a scalar boolean. On the raw array, -notmatch returns the
# non-matching lines (almost always non-empty), which would always abort.
$filters = (& $bundledFfmpeg -hide_banner -filters 2>&1 | Out-String)
if ($filters -notmatch "rubberband") {
    Write-Error "[fetch_ffmpeg] Bundled ffmpeg has no 'rubberband' filter; formant shift would be disabled. Aborting."
    exit 1
}
Write-Host "[fetch_ffmpeg] rubberband filter present (formant shift OK)."

Write-Host "[fetch_ffmpeg] Done."
Write-Host "[fetch_ffmpeg]   $bundledFfmpeg"
Write-Host "[fetch_ffmpeg]   $verResult"
