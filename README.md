# Video Editor

A fast, snappy PyQt6 video editor for trimming, cropping, speed control, color
adjustment, audio normalization, and batch export — backed by FFmpeg.

**Windows 10/11 x64 only at the moment.**

---

## Features

- **Trim** with frame-precise dual-handle slider
- **Crop** with screen-fixed rectangle (video moves, crop stays put)
- **Aspect ratio presets** (9:16, 1:1, 16:9, etc.) + free-form crop
- **Speed control** with natural pitch shift (vinyl-record audio), with
  optional keyframed automation
- **Color** brightness + exposure (preview frames are real ffmpeg-decoded)
- **Audio normalization** to a configurable LUFS target (EBU R128 two-pass)
- **Batch export queue** — set up many clips, walk away
- **NVIDIA NVENC** GPU encoding when available, automatic libx264/libx265
  software fallback otherwise
- **Standalone player mode** with hover-thumbnail seek bar, A-B loop,
  next-file preload (no black-flash between clips)
- **Auto-archive** edited originals into year/month folder structure
- **Rebindable shortcuts** for everything — keyboard and mouse
- **Themes** — dark (default) and light, with UI scale 70-120%

---

## Install

1. Download `VideoEditor-Setup-X.Y.Z.exe` from the project's Releases page.
2. Double-click the installer.
3. (First time only) Windows SmartScreen may show "Windows protected your PC"
   because the build isn't code-signed. Click **More info → Run anyway**.
4. Choose **Install for me only** (default, no admin rights, installs to
   `%LOCALAPPDATA%\Programs\Video Editor`) or **Install for all users**
   (admin, installs to Program Files; each user's settings then live in
   `%LOCALAPPDATA%\Video Editor\config`).

The portable zip (`Video-Editor-vX.Y.Z-win64.zip`) needs no installer: extract
it anywhere you can write to and run `Video Editor.exe`. Each download has a
`.sha256` file next to it for checking the file.

The installer bundles its own FFmpeg — nothing else to install.

To uninstall: **Settings → Apps → Video Editor → Uninstall** (or use the
Start Menu's "Uninstall Video Editor" shortcut).

---

## Quickstart

1. **Open a video** — drag a file onto the window, or `Ctrl+O`.
2. **Trim** — drag the dual-handle slider beneath the video. The orange band
   is the kept range.
3. **Crop** — pick a preset from the Aspect dropdown, or drag the crop
   rectangle directly on the video.
4. **Speed** — slider in the right panel. Audio pitch shifts naturally.
5. **Color** — brightness/exposure sliders.
6. **Export** — `Ctrl+E`. The output goes next to the source by default with
   `_edited` suffix; change in **Settings → Output**.

To process many clips: open them all, set up each one, then **Export All
Edited**.

---

## Keyboard shortcuts

The full list (and all rebindable) lives in **Settings → Shortcuts**. Highlights:

| Shortcut          | Action                  |
|-------------------|-------------------------|
| `Space`           | Play / pause            |
| `←` / `→`         | Frame step              |
| `Shift+←` / `→`   | Seek 5s                 |
| `Ctrl+←` / `→`    | Previous / next clip    |
| `Ctrl+E`          | Export current          |
| `Ctrl+Z`          | Undo                    |
| `F5` / `F6`       | Editor / Player mode    |
| `F11`             | Toggle fullscreen       |

---

## Troubleshooting

**Exports are slow.** If your machine doesn't have an NVIDIA GPU (or NVENC
is disabled in the driver), the editor falls back to software encoding,
which is 5-10× slower. The status bar will tell you why on launch.
Workaround: lower CRF (lower quality, faster) or use a higher-speed software
preset (Settings → Output → Preset).

**Tiny crops fail to export.** NVENC has a hardware minimum frame size
(~145×49 for h264_nvenc, larger for hevc_nvenc). Crops smaller than that
silently fail. Workaround: keep crops at least 160×160, or switch the codec
to software h264 in Settings → Output for tiny exports.

**App won't launch (nothing happens).** A second launch exits silently
while another copy is running. Check Task Manager for an orphaned
`Video Editor.exe` and end it. The single-instance
lock is released when that process exits, so no file needs deleting.

**A clip won't play but plays in VLC.** The Qt media stack on Windows uses
Media Foundation, which has narrower codec coverage than VLC. Convert with
`ffmpeg -i bad.mp4 -c:v libx264 -c:a aac fixed.mp4` and try the converted
file.

**Found a bug.** **Help → Report a Bug** copies a sanitized log + version
info to your clipboard and opens the project's issue tracker. Your Windows
username and home directory are removed from the log before it's copied.

---

## Privacy

This app collects **nothing by default**. Optional features (update check,
crash reporting, bug-report) are documented in [PRIVACY.md](PRIVACY.md).
Each one is OFF or under explicit user control.

---

## Licenses

- **App:** GPL v3 — see [LICENSE](LICENSE)
- **Bundled FFmpeg:** GPL version 3 or later — see `ffmpeg/licenses/` in the
  installed app. Its complete source is the `ffmpeg-<version>-source.tar`
  asset on each release; see [docs/FFMPEG_SOURCE_OFFER.md](docs/FFMPEG_SOURCE_OFFER.md)
- **Third-party components:** see [NOTICES.md](NOTICES.md)

---

## Contributing & building from source

See [docs/BUILD.md](docs/BUILD.md) for prerequisites, build commands,
optional code signing, and CI integration.

To report bugs or request features: open an issue at
<https://github.com/trustxix/video-editor/issues>.
