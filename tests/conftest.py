"""Pytest fixtures shared across the test suite.

Session-scoped fixtures generate tiny test videos via FFmpeg's lavfi inputs
(testsrc for video, sine for audio) — no real-world media files committed
to the repo.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _add_bundled_ffmpeg_to_path() -> None:
    """Put the FFmpeg bundled next to the built app on PATH when the system has
    none.

    Runs at conftest import time, before the test modules are collected, because
    several of them decide `@pytest.mark.skipif(not _have_ffmpeg())` at their own
    import time. Without this, every export/format/formant E2E test silently
    skips on a machine where FFmpeg was never installed system-wide — a green
    run that verified nothing."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    bundled = Path(__file__).parent.parent / "dist" / "Video Editor" / "ffmpeg"
    if (bundled / "ffmpeg.exe").exists() and (bundled / "ffprobe.exe").exists():
        os.environ["PATH"] = f"{bundled}{os.pathsep}{os.environ.get('PATH', '')}"


_add_bundled_ffmpeg_to_path()


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="session", autouse=True)
def _ensure_offscreen_qt():
    """Force Qt offscreen so any test that touches a QWidget doesn't pop a window."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def _isolated_config_dir(tmp_path_factory):
    """Give the whole run its own app base dir, so the config dir (settings.json,
    editor.log, export_error.log, crashes/) is a temp folder: tests neither read
    the developer's settings nor overwrite them. In source mode get_base_dir()
    only locates config and a bundled ffmpeg, and the repo root has no ffmpeg,
    so FFmpeg still comes from PATH. Per-test monkeypatches still override this."""
    from src.core import paths
    base = tmp_path_factory.mktemp("app-base")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(paths, "get_base_dir", lambda: base)
        # Collection may already have resolved the real repo config dir.
        paths._resolve_config_dir.cache_clear()
        yield base / "config"
    paths._resolve_config_dir.cache_clear()


@pytest.fixture(scope="session")
def fixture_video() -> Path:
    """Generate a tiny 5-second 320x240 test video with H.264 + AAC.

    Produced via FFmpeg lavfi (no real media files in the repo). Cached in
    tests/fixtures/ so subsequent runs reuse the same file."""
    if not _have_ffmpeg():
        pytest.skip("FFmpeg not on PATH (install via choco/winget for E2E tests)")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURES_DIR / "test_5s_320x240.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5:sample_rate=48000",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        pytest.fail(f"Failed to generate fixture video:\n{result.stderr}")
    assert out.exists() and out.stat().st_size > 0, "fixture video produced but empty"
    return out


@pytest.fixture(scope="session")
def fixture_video_silent() -> Path:
    """5s test video with no audio track — for codepaths that must not crash on silent input."""
    if not _have_ffmpeg():
        pytest.skip("FFmpeg not on PATH")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    out = FIXTURES_DIR / "test_5s_silent.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        pytest.fail(f"Failed to generate silent fixture:\n{result.stderr}")
    return out
