"""Tests for src/core/archive.py — auto-archive year/month folder detection."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import archive as archive_mod
from src.core.archive import (
    _MAX_COLLISION_ATTEMPTS,
    _unique_destination,
    archive_original,
    detect_relative_path,
)


# ─── detect_relative_path ─────────────────────────────────────────────────

def test_detect_relative_path_finds_year():
    p = Path("D:/Clips/MKV/2026/04 - April/foo.mkv")
    assert detect_relative_path(p) == Path("2026/04 - April/foo.mkv")


def test_detect_relative_path_oldest_year_wins():
    """If multiple year-looking folders exist, the FIRST (outermost) wins."""
    p = Path("D:/2024/Backups/2026/clip.mp4")
    assert detect_relative_path(p) == Path("2024/Backups/2026/clip.mp4")


def test_detect_relative_path_no_year_returns_none():
    p = Path("D:/random/folder/clip.mp4")
    assert detect_relative_path(p) is None


def test_detect_relative_path_invalid_year_format():
    """Folders like '202' or '20266' must NOT match — only 4-digit years."""
    p1 = Path("D:/202/clip.mp4")
    assert detect_relative_path(p1) is None
    p2 = Path("D:/20266/clip.mp4")
    assert detect_relative_path(p2) is None


def test_detect_relative_path_only_19xx_or_20xx_match():
    """Year regex is ^(19|20)\\d{2}$ — 1899 and 2100 must NOT match."""
    assert detect_relative_path(Path("D:/1899/clip.mp4")) is None
    assert detect_relative_path(Path("D:/2100/clip.mp4")) is None
    assert detect_relative_path(Path("D:/1999/clip.mp4")) == Path("1999/clip.mp4")
    assert detect_relative_path(Path("D:/2099/clip.mp4")) == Path("2099/clip.mp4")


# ─── _unique_destination ─────────────────────────────────────────────────

def test_unique_destination_no_collision(tmp_path):
    p = tmp_path / "foo.mp4"
    assert _unique_destination(p) == p


def test_unique_destination_increments_on_collision(tmp_path):
    (tmp_path / "foo.mp4").touch()
    assert _unique_destination(tmp_path / "foo.mp4") == tmp_path / "foo (1).mp4"


def test_unique_destination_increments_multiple(tmp_path):
    (tmp_path / "foo.mp4").touch()
    (tmp_path / "foo (1).mp4").touch()
    (tmp_path / "foo (2).mp4").touch()
    assert _unique_destination(tmp_path / "foo.mp4") == tmp_path / "foo (3).mp4"


# ─── archive_original ────────────────────────────────────────────────────

def test_archive_original_with_year_path(tmp_path):
    """Source under 2026/04 → archived to <root>/2026/04/clip.mp4."""
    src_dir = tmp_path / "src" / "2026" / "04 - April"
    src_dir.mkdir(parents=True)
    src = src_dir / "clip.mp4"
    src.write_bytes(b"video data")
    archive_root = tmp_path / "archive"
    result = archive_original(src, archive_root)
    assert result == (archive_root / "2026" / "04 - April" / "clip.mp4").resolve()
    assert result.exists()
    assert not src.exists()


def test_archive_original_no_year_falls_back_to_unsorted(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src = src_dir / "rogue.mp4"
    src.write_bytes(b"video data")
    archive_root = tmp_path / "archive"
    result = archive_original(src, archive_root)
    assert result == (archive_root / "Unsorted" / "rogue.mp4").resolve()
    assert result.exists()


def test_archive_original_collision_appends_number(tmp_path):
    archive_root = tmp_path / "archive"
    (archive_root / "Unsorted").mkdir(parents=True)
    (archive_root / "Unsorted" / "name.mp4").touch()

    src = tmp_path / "src" / "name.mp4"
    src.parent.mkdir()
    src.write_bytes(b"new clip")

    result = archive_original(src, archive_root)
    assert result.name == "name (1).mp4"


def test_archive_original_already_in_archive_is_noop(tmp_path):
    """If the source is already inside the archive root, no move happens."""
    archive_root = tmp_path / "archive"
    (archive_root / "2026").mkdir(parents=True)
    src = archive_root / "2026" / "already.mp4"
    src.write_bytes(b"already here")
    result = archive_original(src, archive_root)
    assert result == src.resolve()
    assert src.exists()  # still there


def test_archive_original_missing_source_raises(tmp_path):
    src = tmp_path / "ghost.mp4"  # never created
    archive_root = tmp_path / "archive"
    with pytest.raises(FileNotFoundError):
        archive_original(src, archive_root)


# ─── _unique_destination overflow guard ───────────────────────────────────

def test_unique_destination_caps_iterations(tmp_path, monkeypatch):
    """If exists() always returns True (e.g. permission-denied target),
    _unique_destination must raise instead of spinning forever."""
    # Patch Path.exists across all paths to always claim "exists".
    monkeypatch.setattr(Path, "exists", lambda self: True)
    target = tmp_path / "stuck.mkv"
    with pytest.raises(RuntimeError, match=str(_MAX_COLLISION_ATTEMPTS)):
        _unique_destination(target)


def test_unique_destination_finds_within_cap(tmp_path):
    """Sanity: when only a few colliding names exist, the function returns
    the next free name well under the cap."""
    target = tmp_path / "real.mkv"
    target.touch()
    (tmp_path / "real (1).mkv").touch()
    (tmp_path / "real (2).mkv").touch()
    result = _unique_destination(target)
    assert result == tmp_path / "real (3).mkv"
    assert not result.exists()
