# Build from Source

## Prerequisites

- Windows 10/11 x64
- Python 3.14 (`winget install Python.Python.3.14` or python.org)
- Inno Setup 6 (free):
  ```powershell
  winget install JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements
  ```
- (Optional, for code signing) Authenticode certificate + Windows 10/11 SDK
  (provides `signtool.exe`)
- (Optional, for crash-report integration) a Sentry account

The build downloads FFmpeg automatically from the BtbN/FFmpeg-Builds GitHub
release, so you do not need FFmpeg pre-installed on your dev machine —
though having it on PATH is required for the test suite (E2E export tests
generate fixture videos via `ffmpeg lavfi`).

---

## Quick build

```powershell
git clone <repo> video-editor
cd video-editor

# Pinned dependencies (exact versions for reproducibility)
pip install -r requirements.txt

# Single-command release: tests → PyInstaller → FFmpeg bundle → sign hook → installer
.\tools\release.ps1 -Version 0.1.0
```

Output: `dist\installer\VideoEditor-Setup-0.1.0.exe` (~140 MB compressed)

The build is gated by `pytest` — any test failure aborts the release. If
you need to skip the test gate (not recommended for releases), run the
build steps individually:

```powershell
.\tools\build.bat
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" "/DMyAppVersion=0.1.0" tools\installer.iss
```

---

## Code signing (when you have a cert)

Without code signing, Windows SmartScreen warns "Unknown publisher" on
every download. To enable signing, set these env vars before re-running
the release:

```powershell
$env:SIGNCERT_PATH      = "C:\path\to\cert.pfx"
$env:SIGNCERT_PASSWORD  = "<your password>"
$env:SIGNCERT_TIMESTAMP = "http://timestamp.digicert.com"  # optional
.\tools\release.ps1 -Version 0.1.0
```

`tools\sign.ps1` locates `signtool.exe` automatically from the latest
Windows 10/11 SDK install. If signtool isn't found, install the SDK from
the Visual Studio Installer (workload: "Desktop development with C++",
component: "Windows 10 SDK" or newer).

For Microsoft SmartScreen reputation: an EV (Extended Validation)
certificate gets you immediate reputation. A standard OV/IV certificate
takes weeks/months of downloads to build reputation.

---

## Crash reporting integration (when you have a Sentry account)

Official builds don't include `sentry-sdk`, so they never send anything.
A build sends crash reports only when ALL of these hold at crash time:

1. `sentry-sdk` was installed when the exe was built (PyInstaller bundles
   it because `crash_reporter` imports it);
2. `SENTRY_DSN` is set in the environment the app **runs** in (it is read
   at runtime, not baked in at build time);
3. the user's `crash_reports` setting is JSON `true` (default `false`,
   applied by `MainWindow` via `crash_reporter.set_remote_reporting`).
   A crash before the settings load is never sent.

Crashes are scrubbed through `log_setup.sanitize_path` before transmission
so the user's Windows username and home directory don't end up in upstream
events.

Crash reports must remain opt-in per `PRIVACY.md`. Don't ship a build that
hard-codes opt-in.

---

## Testing

```powershell
# Default fast suite (~3 seconds)
python -m pytest tests/

# Include slow tests (memory leak harness, ~5 seconds extra)
python -m pytest tests/ -m slow

# Explicit selection
python -m pytest tests/test_export_e2e.py -v
```

E2E export tests need FFmpeg on PATH (any reasonably recent build, e.g.
`winget install Gyan.FFmpeg`). Other tests are pure Python or use Qt
offscreen.

---

## Project layout

```
main.py                          # Entry: stderr-suppression, mutex,
                                 # log_setup.init, crash_reporter install
src/core/
    paths.py                     # Frozen vs source path resolution,
                                 # config dir + read-only-install fallback
    ffmpeg_runner.py             # Command builder, run_export,
                                 # NVENC fallback, loudnorm, traversal guard
    presets.py                   # Aspect ratio math
    video_item.py                # Per-clip dataclass
    archive.py                   # Auto-archive year/month folders
    keybinds.py                  # Rebindable action registry
    speed_curve.py               # Pure-Python keyframe interpolation
    auto_presets.py              # Optimizer-generated encoder presets
    log_setup.py                 # Rotating logger + path sanitization
    crash_reporter.py            # Excepthook → JSON dump + Sentry hook
    settings_migration.py        # Versioned settings file migration
    version.py                   # VERSION + update check
    bug_report.py                # Sanitized log → clipboard + issue URL
src/ui/
    main_window.py               # Main editor window
    video_player.py              # Playback engine
    crop_overlay.py              # Screen-fixed crop rectangle
    trim_controls.py             # Dual-handle slider
    automation_lane.py           # Speed keyframes
    seek_bar.py                  # Player mode hover-thumb seek bar
    thumbnail_worker.py          # Background thumb extraction
    player_mode.py               # Standalone player UI
    widgets.py                   # CompactVolumeControl, ClickSlider
    settings_dialog.py           # Settings UI
    keybind_editor.py            # Click-to-capture keybind editor
    themes.py                    # QSS + scale system
tools/
    build.bat                    # PyInstaller + license copy + ffmpeg fetch + sign
    fetch_ffmpeg.ps1             # Download BtbN FFmpeg release
    sign.ps1                     # Authenticode sign hook (no-op without cert)
    installer.iss                # Inno Setup script
    release.ps1                  # Single-command full pipeline
tests/
    conftest.py                  # FFmpeg-generated fixture videos
    test_*.py                    # 130+ tests across 11 files
.github/workflows/ci.yml         # GitHub Actions: tests + build smoke
```

---

## CI

`.github/workflows/ci.yml` runs on push and PR:

1. **test** job: install FFmpeg via Chocolatey, install pinned deps, run
   pytest with `QT_QPA_PLATFORM=offscreen`. ~2 min.
2. **build_smoke** job: PyInstaller dry-run (no FFmpeg fetch — skipped for
   CI speed), uploads the bundle as a 7-day artifact. ~5 min.

Free for private repos within GitHub's monthly minute allowance.

---

## Troubleshooting

- **`fetch_ffmpeg.ps1` fails with HTTP 403** → GitHub API rate limit (60/hr
  unauthenticated). Set `$env:GITHUB_TOKEN` (any personal access token
  works, no scopes needed).
- **PyInstaller misses a hidden import** → add `--hidden-import <name>` to
  `tools/build.bat`. Verify by running the installed exe and watching for
  ImportError in `editor.log` (in `<install dir>/config`, or
  `%LOCALAPPDATA%/Video Editor/config` when the install dir is read-only).
- **`ISCC.exe` not found on PATH** → Inno Setup ships it under
  `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`. `release.ps1` checks
  that path automatically — if it's elsewhere, edit the candidate list in
  `release.ps1`.
- **Build fails with "test suite failed"** → some tests need FFmpeg on PATH
  (E2E exports). Install via `winget install Gyan.FFmpeg` or set
  `$env:PYTEST_ADDOPTS = "--ignore=tests/test_export_e2e.py"` to skip them
  for a quick local build.
