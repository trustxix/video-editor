# Basic Video Editor — Design Spec

## Overview
A PyQt-based desktop video editor for Windows that supports **cropping** and **trimming** video files. Uses FFmpeg 8.1 for the actual processing. Single-window UI with embedded video preview.

## Architecture

```
video-editor/
├── main.py                  # Entry point
├── src/
│   ├── core/
│   │   ├── ffmpeg_runner.py # Builds + runs ffmpeg commands
│   │   └── presets.py       # Crop preset definitions
│   └── ui/
│       ├── main_window.py   # Main window layout
│       ├── video_player.py  # Video preview + playback
│       ├── crop_overlay.py  # Draggable crop rectangle
│       └── trim_controls.py # Timeline slider + time inputs
```

## Components

### Video Player (`ui/video_player.py`)
- PyQt6 QMediaPlayer + QVideoWidget for playback
- Play/pause button, current time display
- Seek via clicking the trim timeline

### Crop Overlay (`ui/crop_overlay.py`)
- Transparent widget layered on top of the video preview
- Draggable, resizable rectangle with handles at corners and edges
- Darkens area outside the crop region
- Syncs bidirectionally with manual input fields
- Supports aspect ratio lock from presets

### Trim Controls (`ui/trim_controls.py`)
- Dual-handle range slider for selecting start/end times
- Manual time input fields (HH:MM:SS.ms format)
- Displays total selected duration
- Snaps playback to trim region for preview

### Crop Input Panel (part of main_window)
- X, Y, Width, Height spin boxes
- Preset dropdown: Free, 16:9, 1:1, 4:3, 9:16, 4:5
- "Reset" button to restore full frame

### FFmpeg Runner (`core/ffmpeg_runner.py`)
- Builds ffmpeg command from trim start/end + crop x/y/w/h
- Runs as subprocess, captures progress via stderr
- Progress bar in UI during export
- Output file: same directory as input, with `_edited` suffix

### Presets (`core/presets.py`)
- Named aspect ratios with display labels
- Calculates crop dimensions centered on the video for each ratio

## Data Flow
1. User opens a video file (File > Open or drag-and-drop)
2. Video loads into the player; crop overlay and trim slider initialize to full extent
3. User adjusts crop rectangle (visually or via fields) and/or trim range
4. User clicks "Export" → ffmpeg_runner builds command → runs ffmpeg → progress bar → done

## Constraints
- PyQt6 for the GUI
- FFmpeg 8.1 (already installed)
- Python 3.x
- Windows 11
- No server, no network, fully local
