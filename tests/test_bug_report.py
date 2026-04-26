"""Tests for src/core/bug_report.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_build_report_includes_version_python_platform(tmp_path, monkeypatch):
    from src.core import paths, bug_report
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    r = bug_report.build_report()
    assert "Version:" in r
    assert "Python:" in r
    assert "Platform:" in r
    from src.core.version import VERSION
    assert VERSION in r


def test_build_report_no_log_handles_gracefully(tmp_path, monkeypatch):
    """If editor.log doesn't exist, report should still build (with empty log section)."""
    from src.core import paths, bug_report
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    r = bug_report.build_report()
    assert "Recent log" in r
    # Empty log section shouldn't crash anything


def test_build_report_sanitizes_log_contents(tmp_path, monkeypatch):
    from src.core import paths, bug_report
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    (tmp_path / "editor.log").write_text(
        r"opened C:\Users\alice\Videos\file.mp4",
        encoding="utf-8",
    )
    r = bug_report.build_report()
    assert "alice" not in r
    assert "<USER>" in r
    assert "file.mp4" in r  # filenames preserved


def test_build_report_truncates_to_include_log_lines(tmp_path, monkeypatch):
    from src.core import paths, bug_report
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    big_log = "\n".join(f"line {i}" for i in range(500))
    (tmp_path / "editor.log").write_text(big_log, encoding="utf-8")
    r = bug_report.build_report(include_log_lines=10)
    # Only last 10 lines (indices 490..499) included
    assert "line 499" in r
    assert "line 490" in r
    assert "line 489" not in r


def test_open_bug_report_calls_clipboard_and_url(tmp_path, monkeypatch):
    """The clipboard_setter receives the report; url_opener gets the issue URL."""
    from src.core import paths, bug_report
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)

    captured = {"clipboard": None, "url": None}
    bug_report.open_bug_report(
        clipboard_setter=lambda t: captured.update(clipboard=t),
        url_opener=lambda u: captured.update(url=u),
    )
    assert captured["clipboard"] is not None
    assert "Bug Report" in captured["clipboard"]
    assert captured["url"] == bug_report.ISSUE_URL_TEMPLATE
