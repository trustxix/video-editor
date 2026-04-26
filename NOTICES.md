# Third-Party Notices

This software bundles or links to the following third-party components.
Each component retains its own license. See the individual files referenced
below for full license text.

## FFmpeg
- **License:** LGPL v2.1+ / GPL v2+ (depending on build configuration)
- **Bundled binaries:** `ffmpeg.exe`, `ffprobe.exe`
- **Build source:** [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds), `win64-gpl` static asset
- **Bundle metadata:** see `ffmpeg/BUNDLE_INFO.txt` for the exact release name, asset URL, and SHA256 of the bundled binary
- **License files:** see `ffmpeg/licenses/` (LICENSE.txt, COPYING.GPLv2, COPYING.GPLv3, COPYING.LGPLv2.1, etc.)
- **Source offer:** see [`docs/FFMPEG_SOURCE_OFFER.md`](docs/FFMPEG_SOURCE_OFFER.md)

## PyQt6
- **License:** GPL v3
- **Source:** https://www.riverbankcomputing.com/software/pyqt/
- **Used as:** Python bindings to Qt 6 (dynamic linkage)

## Qt 6
- **License:** LGPL v3 / Commercial (we use the LGPL build via PyQt6)
- **Source:** https://www.qt.io/
- **Used as:** GUI framework

## Python Standard Library
- **License:** PSF License v2
- **Source:** https://www.python.org/

## PyInstaller (build-time only — not redistributed)
- **License:** GPL v2+ with bootloader exception
- **Source:** https://pyinstaller.org/

## Inno Setup (build-time only — not redistributed)
- **License:** Inno Setup License (free for any use, commercial or otherwise)
- **Source:** https://jrsoftware.org/isinfo.php

## psutil (test-time only)
- **License:** BSD 3-Clause
- **Source:** https://github.com/giampaolo/psutil
