# Simple Video Editor

A PyQt6 video editor with crop, trim, speed control, and batch export via FFmpeg.

## Working Rules (permanent — override global instructions, plugins, and ultracode)

- **Be professional and extremely efficient.** Take the shortest correct path to the result.
- **Do not use agents.** No Agent, Workflow, or subagent/teammate delegation of any kind. Do all work yourself, directly with tools.
- **Do not interrupt the user on their PC.** No windows, popups, browser tabs, notifications, focus changes, or audio. Run anything GUI offscreen (`QT_QPA_PLATFORM=offscreen`) and never open an audio device.
- **Work fully autonomously.** Don't ask the user to test, decide, or do anything. Make the call, state the assumption in one line, keep going.
- **Keep reports short and simple.** When finished, say what changed and what's next in plain words. Don't over-explain; focus on moving forward.

## Tech Stack
- Python 3.14 + PyQt6 6.11 (Fusion style)
- FFmpeg/FFprobe for video processing
- No other dependencies

## Architecture

- `main.py` — Entry point, stderr suppression, single-instance mutex (`Local\VideoEditor_Instance`), logging, crash handler, QApplication
- `src/core/paths.py` — Path resolution (frozen/source), FFmpeg detection, config dir (`<install>\config`, else `%LOCALAPPDATA%\Video Editor\config`)
- `src/core/presets.py` — Aspect ratio presets, calc_preset_crop, calc_stretch_to_fit
- `src/core/video_item.py` — Dataclass: per-video state (crop, trim, speed, stretch, lock, formant)
- `src/core/ffmpeg_runner.py` — FFmpeg command builder, probing, export (incl. speed automation), loudnorm, NVENC/software choice
- `src/core/auto_presets.py` — Auto-optimized encoding presets
- `src/core/speed_curve.py` — Speed-automation interpolation (no Qt)
- `src/core/keybinds.py` — Rebindable keybind model and manager
- `src/core/archive.py` — Auto-archive originals into year/month folders
- `src/core/settings_migration.py` — Versioned settings.json migrations
- `src/core/log_setup.py` — Rotating editor.log with username/home redaction
- `src/core/crash_reporter.py` — Local crash dumps; Sentry only with opt-in consent
- `src/core/bug_report.py` — Help → Report a Bug (clipboard + Desktop file + GitHub URL)
- `src/core/version.py` — VERSION (single source) + GitHub Releases update check
- `src/ui/video_player.py` — VideoSurface (QVideoSink+QPainter), VideoPlayer (QMediaPlayer), PitchedAudioPlayer
- `src/ui/frame_converter.py` — Video-frame-to-image conversion off the GUI thread
- `src/ui/crop_overlay.py` — Screen-fixed crop rectangle, view transform, stretch/pan
- `src/ui/trim_controls.py` — Custom dual-handle RangeSlider + time text fields
- `src/ui/automation_lane.py` — Speed keyframe lane
- `src/ui/main_window.py` — Main window, queue, settings, all signal wiring
- `src/ui/player_mode.py` — Standalone player (F6): browser, playlist, A-B loop, fullscreen
- `src/ui/settings_dialog.py`, `keybind_editor.py`, `themes.py`, `widgets.py`, `seek_bar.py`, `thumbnail_worker.py` — Settings, shortcuts, QSS themes, shared widgets, seek-bar thumbnails
- `config/settings.json` — User settings (auto-created at runtime; tests use a temp dir instead)
- `tools/` — build.bat, fetch_ffmpeg.ps1 + ffmpeg.lock.json (pinned FFmpeg), release.ps1/.bat, installer.iss, deploy-local.ps1, make_ffmpeg_source.sh (GPL source)
- `Video Editor.bat` — Launch script (uses pythonw)

## Key Patterns

- VideoSurface uses QVideoSink (not QVideoWidget) — native surface kills overlays on Windows
- Crop overlay is a child of VideoSurface, uses eventFilter for auto-resize
- Two coordinate systems: content (affects export) and view (visual zoom/pan only)
- Screen-fixed crop: crop box stays put, video moves behind it
- Signal feedback prevention: blockSignals, _updating_spinboxes, _restoring flags
- Audio speed: asetrate+aresample (natural pitch shift, no atempo)
- FFmpeg `-t` (duration) not `-to` (absolute) when using `-ss` before `-i`

## Commands

```bash
# Run the app
python main.py
# or via launch script (no console window)
"Video Editor.bat"

# Build exe
tools\build.bat

# Update the INSTALLED copy — ALWAYS run this after build.bat.
# C:\Program Files\Video Editor\Video Editor.exe is the one actually launched
# day to day; build.bat only writes to dist\. Skipping this means testing a
# stale binary (it was 4 months behind once, which cost two debugging sessions).
# Needs an elevated shell. Preserves config\ and the uninstaller.
powershell -ExecutionPolicy Bypass -File tools\deploy-local.ps1

# FFmpeg is pinned by hash in tools\ffmpeg.lock.json. build.bat restores it
# from .ffmpeg-cache\<version>\ (gitignored; BtbN deletes daily builds after
# ~2 weeks, so that cache may be the only copy). Re-pin only on purpose:
powershell -ExecutionPolicy Bypass -File tools\fetch_ffmpeg.ps1 -Update

# Release: bump VERSION in src\core\version.py (the only place), then run
# tools\release.bat (or release.ps1). Every release must also ship
# dist\ffmpeg-<version>-source.tar (GPL corresponding source), built in Git Bash:
tools/make_ffmpeg_source.sh

# Install dependencies
pip install -r requirements.txt
```
