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


def test_write_crash_prunes_to_cap(isolated_crash_dir):
    """Dumps must not accumulate unbounded — writing more than the cap keeps
    only the most recent _MAX_CRASH_DUMPS."""
    from src.core import crash_reporter
    n = crash_reporter._MAX_CRASH_DUMPS + 5
    for i in range(n):
        try:
            raise ValueError(f"boom {i}")
        except ValueError as e:
            crash_reporter.write_crash(type(e), e, e.__traceback__)
    assert len(crash_reporter.list_pending_crashes()) == crash_reporter._MAX_CRASH_DUMPS


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


class _FakeSentry:
    """Stands in for the sentry_sdk module; records what would be sent."""

    def __init__(self):
        self.captured = []
        self.client = None
        self.Hub = type("Hub", (), {"current": self})

    def init(self, **kwargs):
        self.client = kwargs

    def capture_exception(self, exc_info):
        self.captured.append(exc_info)


@pytest.fixture
def fake_sentry(isolated_crash_dir, monkeypatch):
    from src.core import crash_reporter
    fake = _FakeSentry()
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    monkeypatch.setenv("SENTRY_DSN", "https://key@example.invalid/1")
    monkeypatch.setattr(crash_reporter, "_remote_consent", False)
    return fake


def test_sentry_sends_nothing_without_consent(fake_sentry):
    """A DSN and an importable sentry-sdk are not consent."""
    from src.core import crash_reporter
    crash_reporter._maybe_send_sentry(ValueError, ValueError("x"), None)
    assert fake_sentry.captured == []
    assert fake_sentry.client is None


def test_sentry_sends_after_opt_in(fake_sentry):
    from src.core import crash_reporter
    crash_reporter.set_remote_reporting(True)
    crash_reporter._maybe_send_sentry(ValueError, ValueError("x"), None)
    assert len(fake_sentry.captured) == 1
    assert fake_sentry.client["send_default_pii"] is False


def test_sentry_opt_out_stops_sending(fake_sentry):
    from src.core import crash_reporter
    crash_reporter.set_remote_reporting(True)
    crash_reporter.set_remote_reporting(False)
    crash_reporter._maybe_send_sentry(ValueError, ValueError("x"), None)
    assert fake_sentry.captured == []


@pytest.mark.parametrize("value", ["true", 1, "yes", None])
def test_only_a_real_true_counts_as_consent(fake_sentry, value):
    """settings.json is user-editable; anything but JSON true is not an opt-in."""
    from src.core import crash_reporter
    crash_reporter.set_remote_reporting(value)
    crash_reporter._maybe_send_sentry(ValueError, ValueError("x"), None)
    assert fake_sentry.captured == []


def test_uncaught_crash_without_consent_is_only_written_locally(fake_sentry, monkeypatch):
    from src.core import crash_reporter
    monkeypatch.setattr(sys, "excepthook", lambda *a: None)
    crash_reporter.install_global_handler()
    try:
        raise RuntimeError("boom")
    except RuntimeError as e:
        sys.excepthook(type(e), e, e.__traceback__)
    assert len(crash_reporter.list_pending_crashes()) == 1
    assert fake_sentry.captured == []
