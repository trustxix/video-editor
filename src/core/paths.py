import functools
import hashlib
import os
import sys
import tempfile
from pathlib import Path

APP_DIR_NAME = "Video Editor"


def get_base_dir() -> Path:
    """Root directory — works for both source and PyInstaller frozen modes."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


def _is_writable_dir(d: Path) -> bool:
    """Create `d` if needed and prove a file can be written in it.

    A real write is the only reliable test on Windows: `os.access` ignores
    NTFS ACLs, so it reports Program Files as writable for standard users."""
    try:
        d.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".write-probe-", dir=d)
        os.close(fd)
        os.unlink(probe)
        return True
    except OSError:
        return False


def _user_config_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home() / "AppData" / "Local"
    return root / APP_DIR_NAME / "config"


@functools.cache
def _resolve_config_dir() -> Path:
    portable = get_base_dir() / "config"
    if _is_writable_dir(portable):
        return portable
    return _user_config_dir()


def get_config_dir() -> Path:
    """`<install>/config` when the install dir is writable (source checkouts,
    per-user installs, portable zips), else `%LOCALAPPDATA%/Video Editor/config`.

    An all-users install under Program Files is read-only for standard users,
    and writing there used to crash startup in `log_setup.init`."""
    d = _resolve_config_dir()
    d.mkdir(parents=True, exist_ok=True)
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
    """Stable per-install id, persisted in `<config dir>/.installation_id`.

    Used for crash-dump correlation (so we can tell two crashes from the
    same install apart from two unrelated installs). NOT used for the
    single-instance mutex — that lives in `Local\\` namespace and is
    naturally per-Windows-session, see `main._is_already_running`.
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
