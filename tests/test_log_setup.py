"""Tests for src/core/log_setup.py — sanitization correctness."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.log_setup import init, sanitize_path


def test_sanitize_user_dir_replaces_username():
    out = sanitize_path(r"C:\Users\alice\Documents\file.mp4")
    assert "<USER>" in out
    assert "alice" not in out


def test_sanitize_preserves_filenames_and_extensions():
    out = sanitize_path(r"C:\Users\bob\Videos\my-clip.mp4")
    assert "my-clip.mp4" in out
    assert "Videos" in out


def test_sanitize_handles_forward_slashes():
    out = sanitize_path("C:/Users/charlie/clip.mp4")
    assert "<USER>" in out
    assert "charlie" not in out


def test_sanitize_no_path_passthrough():
    """Strings without user paths should pass through unchanged."""
    assert sanitize_path("just a regular log message") == "just a regular log message"


def test_sanitize_empty_string():
    assert sanitize_path("") == ""


def test_sanitize_multiple_users_in_one_string():
    out = sanitize_path(r"copied C:\Users\alice\a.mp4 to C:\Users\bob\b.mp4")
    assert "alice" not in out
    assert "bob" not in out
    assert out.count("<USER>") == 2


def test_init_creates_log_file(tmp_path):
    log = init(tmp_path, force=True)
    log.info("hello world")
    # Force flush
    for h in log.handlers:
        h.flush()
    log_file = tmp_path / "editor.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "hello world" in content


def test_init_log_message_is_sanitized(tmp_path):
    log = init(tmp_path, force=True)
    log.info(r"opened C:\Users\alice\secret.mp4")
    for h in log.handlers:
        h.flush()
    content = (tmp_path / "editor.log").read_text(encoding="utf-8")
    assert "alice" not in content
    assert "<USER>" in content
    assert "secret.mp4" in content
