import sys
from pathlib import Path


def get_base_dir() -> Path:
    """Root directory — works for both source and PyInstaller frozen modes."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


def get_config_dir() -> Path:
    d = get_base_dir() / "config"
    d.mkdir(exist_ok=True)
    return d


def get_ffmpeg() -> str:
    """Return ffmpeg path, preferring a bundled copy next to the app."""
    bundled = get_base_dir() / "ffmpeg" / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    # Try ffmpeg folder directly in base (flat layout)
    flat = get_base_dir() / "ffmpeg.exe"
    if flat.exists():
        return str(flat)
    return "ffmpeg"


def get_ffprobe() -> str:
    """Return ffprobe path, preferring a bundled copy next to the app."""
    bundled = get_base_dir() / "ffmpeg" / "ffprobe.exe"
    if bundled.exists():
        return str(bundled)
    flat = get_base_dir() / "ffprobe.exe"
    if flat.exists():
        return str(flat)
    return "ffprobe"
