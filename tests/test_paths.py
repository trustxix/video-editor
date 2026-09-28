"""Tests for src/core/paths.py — config dir selection and the read-only fallback."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import paths


@pytest.fixture
def fresh_resolution(tmp_path, monkeypatch):
    """Point the base dir and %LOCALAPPDATA% into tmp_path, and drop the cached
    choice before and after so no other test sees a tmp_path config dir."""
    base = tmp_path / "install"
    base.mkdir()
    local = tmp_path / "local"
    monkeypatch.setattr(paths, "get_base_dir", lambda: base)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    paths._resolve_config_dir.cache_clear()
    yield base, local
    paths._resolve_config_dir.cache_clear()


def test_writable_install_dir_keeps_config_beside_the_exe(fresh_resolution):
    base, local = fresh_resolution
    assert paths.get_config_dir() == base / "config"
    assert (base / "config").is_dir()
    assert not local.exists()


def test_write_probe_leaves_no_file_behind(fresh_resolution):
    base, _ = fresh_resolution
    paths.get_config_dir()
    assert list((base / "config").iterdir()) == []


def test_uncreatable_config_dir_falls_back_to_localappdata(fresh_resolution):
    base, local = fresh_resolution
    (base / "config").write_text("a file where the folder should be")
    d = paths.get_config_dir()
    assert d == local / paths.APP_DIR_NAME / "config"
    assert d.is_dir()


def test_existing_but_read_only_config_dir_falls_back(fresh_resolution, monkeypatch):
    """The Program Files case: the folder exists (the installer made it) but a
    standard user can't create files in it."""
    base, local = fresh_resolution
    (base / "config").mkdir()
    real_mkstemp = paths.tempfile.mkstemp

    def deny_in_install_dir(*args, dir=None, **kwargs):
        if Path(dir) == base / "config":
            raise PermissionError(13, "Access is denied", str(dir))
        return real_mkstemp(*args, dir=dir, **kwargs)

    monkeypatch.setattr(paths.tempfile, "mkstemp", deny_in_install_dir)
    assert paths.get_config_dir() == local / paths.APP_DIR_NAME / "config"


def test_choice_is_made_once_per_process(fresh_resolution, monkeypatch):
    base, _ = fresh_resolution
    calls = []
    real_probe = paths._is_writable_dir
    monkeypatch.setattr(paths, "_is_writable_dir", lambda d: calls.append(d) or real_probe(d))
    for _ in range(3):
        paths.get_config_dir()
    assert calls == [base / "config"]


def test_config_dir_is_recreated_if_deleted_while_running(fresh_resolution):
    base, _ = fresh_resolution
    d = paths.get_config_dir()
    d.rmdir()
    assert paths.get_config_dir() == d
    assert d.is_dir()
