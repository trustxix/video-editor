import hashlib
import os
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


def installation_id() -> str:
    """Stable per-user installation id, persisted in config dir.

    Used to salt the single-instance mutex name so a malicious local process
    can't pre-grab a predictable mutex and DoS the launcher.
    """
    cfg = get_config_dir()
    id_file = cfg / ".installation_id"
    if id_file.exists():
        try:
            existing = id_file.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass
    seed = f"{os.environ.get('USERNAME', '')}:{os.environ.get('APPDATA', '')}"
    iid = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    try:
        id_file.write_text(iid, encoding="utf-8")
    except OSError:
        pass
    return iid


# Backwards-compat alias used by Phase 1.5 log_setup.
def config_dir() -> Path:
    return get_config_dir()
