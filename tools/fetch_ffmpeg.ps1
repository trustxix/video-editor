# Put the PINNED FFmpeg build into dist\Video Editor\ffmpeg\.
#
# The pin is tools\ffmpeg.lock.json: the version plus the SHA256 of ffmpeg.exe
# and ffprobe.exe. A verified copy is cached in .ffmpeg-cache\<version>\
# (gitignored) because PyInstaller --noconfirm wipes dist\Video Editor on
# every build, and BtbN deletes its daily builds after about two weeks.
#
# Sources, first match wins; every one is hash-checked against the lock:
#   1. dist already holds the pinned binaries
#   2. .ffmpeg-cache\<version>\
#   3. the lock's asset_url (download, check the zip SHA256, extract)
#
#   -Update   Re-pin to BtbN's newest win64-gpl build: download it, check it
#             against the SHA256 digest GitHub publishes for the asset, check
#             the rubberband filter, then rewrite the lock and fill the cache
#             and dist. Commit the new lock only after the build is tested.
#
# Honors $env:GITHUB_TOKEN (GitHub API rate limit, 60/hour anonymous).
# Exits non-zero on any failure so build.bat / release.ps1 stop.

param([switch]$Update)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"   # PS 5.1's progress bar slows downloads ~10x
Set-StrictMode -Version Latest

$projectRoot   = Split-Path -Parent $PSScriptRoot
$lockPath      = Join-Path $PSScriptRoot "ffmpeg.lock.json"
$distFfmpegDir = Join-Path $projectRoot "dist\Video Editor\ffmpeg"
$cacheRoot     = Join-Path $projectRoot ".ffmpeg-cache"
$headers       = @{ "User-Agent" = "VideoEditor-Build" }
if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "token $env:GITHUB_TOKEN" }

function Fail([string]$msg) {
    Write-Host "[fetch_ffmpeg] ERROR: $msg" -ForegroundColor Red
    exit 1
}

function Get-Sha256([string]$path) {
    # Get-FileHash has been missing in some -NoProfile sessions; fall back to .NET.
    try {
        return (Get-FileHash -Path $path -Algorithm SHA256).Hash.ToUpperInvariant()
    } catch {
        $stream = [System.IO.File]::OpenRead($path)
        $hasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            return -join ($hasher.ComputeHash($stream) | ForEach-Object { $_.ToString("X2") })
        } finally {
            $stream.Dispose()
            $hasher.Dispose()
        }
    }
}

function Test-MatchesLock([string]$dir, $lock) {
    foreach ($name in "ffmpeg", "ffprobe") {
        $exe = Join-Path $dir "$name.exe"
        if (-not (Test-Path $exe)) { return $false }
        if ((Get-Sha256 $exe) -ne $lock."${name}_sha256".ToUpperInvariant()) { return $false }
    }
    return (Test-Path (Join-Path $dir "licenses"))
}

function Copy-Bundle([string]$from, [string]$to) {
    if (Test-Path $to) { Remove-Item -Recurse -Force $to }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $to) | Out-Null
    Copy-Item -Recurse -Path $from -Destination $to
}

function Test-Runs([string]$dir) {
    $exe = Join-Path $dir "ffmpeg.exe"
    $ver = & $exe -hide_banner -version 2>&1 | Select-Object -First 1
    if ($LASTEXITCODE -ne 0) { Fail "ffmpeg.exe failed to run: $ver" }
    # The formant-shift feature needs librubberband. Out-String makes the
    # filter list ONE string, so -notmatch is a boolean, not a filtered array.
    $filters = (& $exe -hide_banner -filters 2>&1 | Out-String)
    if ($filters -notmatch "rubberband") {
        Fail "ffmpeg has no 'rubberband' filter; formant shift would be disabled."
    }
    return "$ver"
}

# Download a BtbN zip, verify it, and stage ffmpeg.exe, ffprobe.exe and the
# license files into $stage. Returns nothing; fails the script on any error.
function Expand-Release([string]$url, [string]$expectedZipSha, [string]$stage) {
    $zip     = Join-Path $env:TEMP "ffmpeg-release.zip"
    $extract = Join-Path $env:TEMP "ffmpeg-extract"
    try {
        Write-Host "[fetch_ffmpeg] Downloading $url"
        try {
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -Headers $headers
        } catch {
            Fail "download failed: $_"
        }
        $zipSha = Get-Sha256 $zip
        if ($zipSha -ne $expectedZipSha.ToUpperInvariant()) {
            Fail "zip SHA256 $zipSha does not match the expected $expectedZipSha"
        }
        if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
        Expand-Archive -Path $zip -DestinationPath $extract

        $exe   = Get-ChildItem -Path $extract -Recurse -Filter "ffmpeg.exe"  | Select-Object -First 1
        $probe = Get-ChildItem -Path $extract -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
        if (-not $exe -or -not $probe) { Fail "ffmpeg.exe / ffprobe.exe not found inside the archive." }

        if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
        $licDir = Join-Path $stage "licenses"
        New-Item -ItemType Directory -Force -Path $licDir | Out-Null
        Copy-Item $exe.FullName   (Join-Path $stage "ffmpeg.exe")
        Copy-Item $probe.FullName (Join-Path $stage "ffprobe.exe")
        # BtbN ships LICENSE.txt, COPYING.GPLv2/GPLv3/LGPLv2.1 etc., sometimes
        # duplicated in nested folders; keep one of each name.
        $seen = @{}
        Get-ChildItem -Path $extract -Recurse -File | Where-Object {
            ($_.Name -match "^(LICENSE|COPYING|README)" -or $_.Name -like "*.txt") -and
            $_.Name -notlike "ffmpeg-all*"
        } | ForEach-Object {
            if (-not $seen.ContainsKey($_.Name)) {
                $seen[$_.Name] = $true
                Copy-Item $_.FullName $licDir
            }
        }
    } finally {
        if (Test-Path $zip)     { Remove-Item -Force $zip }
        if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
    }
}

function Write-BundleInfo([string]$dir, $lock) {
    @"
FFmpeg bundle info
==================
Version:        $($lock.version)
License:        GPL version 3 or later (licenses\LICENSE.txt)
FFmpeg commit:  $($lock.ffmpeg_commit)
BtbN commit:    $($lock.btbn_commit) (target win64, variant gpl)
Source release: $($lock.source_release)
Asset filename: $($lock.asset_name)
Asset SHA256:   $($lock.asset_sha256)

Complete corresponding source: ffmpeg-$($lock.version)-source.tar on the
GitHub release page (https://github.com/trustxix/video-editor/releases).
See FFMPEG_SOURCE_OFFER.md next to the app.
"@ | Set-Content -Path (Join-Path $dir "BUNDLE_INFO.txt") -Encoding UTF8
}

# --- -Update: re-pin to the newest BtbN build ---
if ($Update) {
    Write-Host "[fetch_ffmpeg] Querying the newest BtbN release..."
    try {
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest" -Headers $headers
    } catch {
        Fail "GitHub API call failed: $_ (set `$env:GITHUB_TOKEN if rate-limited)"
    }
    # The versioned asset (ffmpeg-N-<n>-g<hash>-win64-gpl.zip) lives under a
    # dated tag, so its URL stays valid; the rolling "latest" URL does not.
    $asset = $rel.assets | Where-Object { $_.name -match '^ffmpeg-N-\d+-g[0-9a-f]+-win64-gpl\.zip$' } |
        Select-Object -First 1
    if (-not $asset) { Fail "no ffmpeg-N-*-win64-gpl.zip asset in release '$($rel.tag_name)'." }
    $digest = [string]$asset.digest
    if (-not ($digest -match '^sha256:([0-9a-fA-F]{64})$')) { Fail "asset has no sha256 digest ('$digest')." }
    $zipSha = $Matches[1].ToUpperInvariant()

    $stage = Join-Path $cacheRoot "_staging"
    Expand-Release $asset.browser_download_url $zipSha $stage
    $verLine = Test-Runs $stage
    if (-not ($verLine -match '^ffmpeg version (N-\d+-g([0-9a-f]+)\S*)')) { Fail "could not parse the version from '$verLine'." }
    $ffVersion = $Matches[1]
    $ffShort   = $Matches[2]
    # Full commits for tools\make_ffmpeg_source.sh (the GPL corresponding source).
    try {
        $ffCommit   = (Invoke-RestMethod -Uri "https://api.github.com/repos/FFmpeg/FFmpeg/commits/$ffShort" -Headers $headers).sha
        $btbnCommit = (Invoke-RestMethod -Uri "https://api.github.com/repos/BtbN/FFmpeg-Builds/commits/$($rel.tag_name)" -Headers $headers).sha
    } catch {
        Fail "could not resolve the FFmpeg / BtbN commits: $_"
    }

    $lock = [ordered]@{
        version        = $ffVersion
        source_release = $rel.tag_name
        asset_name     = $asset.name
        asset_url      = $asset.browser_download_url
        asset_sha256   = $zipSha
        ffmpeg_sha256  = Get-Sha256 (Join-Path $stage "ffmpeg.exe")
        ffprobe_sha256 = Get-Sha256 (Join-Path $stage "ffprobe.exe")
        ffmpeg_commit  = $ffCommit
        btbn_commit    = $btbnCommit
    }
    Write-BundleInfo $stage ([pscustomobject]$lock)
    $cacheDir = Join-Path $cacheRoot $lock.version
    Copy-Bundle $stage $cacheDir
    Remove-Item -Recurse -Force $stage
    Copy-Bundle $cacheDir $distFfmpegDir
    [System.IO.File]::WriteAllText($lockPath, (([pscustomobject]$lock | ConvertTo-Json) + "`n"),
        (New-Object System.Text.UTF8Encoding $false))
    Write-Host "[fetch_ffmpeg] Re-pinned to $($lock.version) ($($rel.tag_name))." -ForegroundColor Yellow
    Write-Host "[fetch_ffmpeg] Test this build, then commit tools\ffmpeg.lock.json." -ForegroundColor Yellow
    exit 0
}

# --- Normal build: bundle exactly the pinned build ---
if (-not (Test-Path $lockPath)) { Fail "missing $lockPath" }
$lock     = Get-Content -Raw -Path $lockPath | ConvertFrom-Json
$cacheDir = Join-Path $cacheRoot $lock.version

if (Test-MatchesLock $distFfmpegDir $lock) {
    Write-Host "[fetch_ffmpeg] dist already holds pinned FFmpeg $($lock.version)."
} elseif (Test-MatchesLock $cacheDir $lock) {
    Write-Host "[fetch_ffmpeg] Restoring pinned FFmpeg $($lock.version) from .ffmpeg-cache."
    Copy-Bundle $cacheDir $distFfmpegDir
} elseif ($lock.asset_url) {
    $stage = Join-Path $cacheRoot "_staging"
    Expand-Release $lock.asset_url $lock.asset_sha256 $stage
    if (-not (Test-MatchesLock $stage $lock)) {
        Fail "the downloaded binaries do not match the hashes in tools\ffmpeg.lock.json."
    }
    Write-BundleInfo $stage $lock
    Copy-Bundle $stage $cacheDir
    Remove-Item -Recurse -Force $stage
    Copy-Bundle $cacheDir $distFfmpegDir
} else {
    Fail ("pinned FFmpeg $($lock.version) is not in .ffmpeg-cache and the lock has no download URL " +
          "(BtbN deleted that build). Restore .ffmpeg-cache\$($lock.version)\ from a machine that has it, " +
          "or re-pin with: powershell -File tools\fetch_ffmpeg.ps1 -Update")
}

# Always from the lock, so an older cached copy can't ship stale source info.
Write-BundleInfo $distFfmpegDir $lock
$verLine = Test-Runs $distFfmpegDir
Write-Host "[fetch_ffmpeg] OK: $verLine"
