# Third-Party Notices

This software bundles or links to the following third-party components.
Each component retains its own license. See the individual files referenced
below for full license text.

## FFmpeg
- **License:** GPL version 3 or later (configured with `--enable-gpl --enable-version3`)
- **Bundled binaries:** `ffmpeg.exe`, `ffprobe.exe`, statically linked
- **Build:** [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds), target win64, variant gpl
- **Bundle metadata:** `ffmpeg/BUNDLE_INFO.txt` names the exact version, the FFmpeg and BtbN commits, and the SHA256 of the release archive
- **License text:** `ffmpeg/licenses/LICENSE.txt` (GPL v3)
- **Source:** `ffmpeg-<version>-source.tar` on the GitHub release; see [`docs/FFMPEG_SOURCE_OFFER.md`](docs/FFMPEG_SOURCE_OFFER.md)

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
