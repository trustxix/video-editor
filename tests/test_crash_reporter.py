"""Tests for src/core/crash_reporter.py — sanitization + persistence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def isolated_crash_dir(tmp_path, monkeypatch):
    """Redirect crashes/ to a tmp_path and clear any module-level state."""
    from src.core import crash_reporter, paths
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(crash_reporter, "_installed", False)
    return tmp_path


def test_write_crash_creates_json_file(isolated_crash_dir):
    from src.core import crash_reporter
    try:
        raise ValueError("test crash")
    except ValueError as e:
        path = crash_reporter.write_crash(type(e), e, e.__traceback__)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["exception_type"] == "ValueError"
    assert "test crash" in data["exception_value"]
    assert "traceback" in data
    assert "id" in data
    assert "version" in data
    assert "platform" in data


def test_write_crash_sanitizes_paths_in_message(isolated_crash_dir):
    from src.core import crash_reporter
    try:
        raise FileNotFoundError(r"missing C:\Users\alice\secret.mp4")
    except FileNotFoundError as e:
        path = crash_reporter.write_crash(type(e), e, e.__traceback__)
    text = path.read_text(encoding="utf-8")
    assert "alice" not in text
    assert "<USER>" in text


def test_write_crash_sanitizes_paths_in_traceback(isolated_crash_dir):
    """The traceback contains file paths from sys — those must be sanitized."""
    from src.core import crash_reporter
    try:
        raise RuntimeError("boom")
    except RuntimeError as e:
        path = crash_reporter.write_crash(type(e), e, e.__traceback__)
    text = path.read_text(encoding="utf-8")
    # The Windows username from $HOME shouldn't appear in the dump
    import os
    user = os.environ.get("USERNAME", "")
    if user and len(user) > 2:  # avoid trivial usernames matching by chance
        assert user not in text


def test_list_pending_crashes_finds_files(isolated_crash_dir):
    from src.core import crash_reporter
    try:
        raise ValueError("a")
    except ValueError as e:
        crash_reporter.write_crash(type(e), e, e.__traceback__)
    try:
        raise ValueError("b")
    except ValueError as e:
        crash_reporter.write_crash(type(e), e, e.__traceback__)
    pending = crash_reporter.list_pending_crashes()
    assert len(pending) == 2


def test_acknowledge_deletes_dump(isolated_crash_dir):
    from src.core import crash_reporter
    try:
        raise ValueError("a")
    except ValueError as e:
        path = crash_reporter.write_crash(type(e), e, e.__traceback__)
    assert path.exists()
    crash_reporter.acknowledge(path)
    assert not path.exists()


def test_install_global_handler_idempotent(isolated_crash_dir):
    """Installing twice should not double-wrap excepthook."""
    from src.core import crash_reporter
    original = sys.excepthook
    crash_reporter.install_global_handler()
    after_first = sys.excepthook
    crash_reporter.install_global_handler()
    after_second = sys.excepthook
    # Restore so we don't leak handler state into other tests
    sys.excepthook = original
    assert after_first is after_second  # second install was a no-op


def test_maybe_send_sentry_no_dsn_is_silent(isolated_crash_dir, monkeypatch):
    """Without SENTRY_DSN env var, _maybe_send_sentry returns silently."""
    from src.core import crash_reporter
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    # Must not raise
    crash_reporter._maybe_send_sentry(ValueError, ValueError("x"), None)
