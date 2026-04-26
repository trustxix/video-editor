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


def test_open_bug_report_calls_clipboard_url_and_desktop_writer(tmp_path, monkeypatch):
    """The clipboard_setter receives the report, url_opener gets the issue
    URL, and desktop_writer is called with the report — all three channels
    fire so the user always has a way to recover the bug content."""
    from src.core import paths, bug_report
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)

    captured = {"clipboard": None, "url": None, "saved": None}

    def fake_writer(report):
        path = tmp_path / "fake-desktop-bug-report.md"
        path.write_text(report, encoding="utf-8")
        captured["saved"] = path
        return path

    result = bug_report.open_bug_report(
        clipboard_setter=lambda t: captured.update(clipboard=t),
        url_opener=lambda u: captured.update(url=u),
        desktop_writer=fake_writer,
    )
    assert captured["clipboard"] is not None
    assert "Bug Report" in captured["clipboard"]
    assert captured["url"] == bug_report.ISSUE_URL_TEMPLATE
    assert captured["saved"] is not None
    assert captured["saved"].read_text(encoding="utf-8").startswith("## Bug Report")
    # Returned dict surfaces all four channels for the UI
    assert result["clipboard_set"] is True
    assert result["saved_path"] == str(captured["saved"])
    assert result["issue_url"] == bug_report.ISSUE_URL_TEMPLATE
    assert result["mailto_url"].startswith("mailto:")


def test_open_bug_report_survives_clipboard_failure(tmp_path, monkeypatch):
    """If the clipboard write fails (rare, but happens on locked-down or
    headless sessions), the function still saves the report to disk and
    returns clipboard_set=False so the UI can warn appropriately."""
    from src.core import paths, bug_report
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)

    def boom(_text):
        raise RuntimeError("clipboard unavailable")

    captured_path = tmp_path / "fallback.md"

    def writer(report):
        captured_path.write_text(report, encoding="utf-8")
        return captured_path

    result = bug_report.open_bug_report(
        clipboard_setter=boom,
        url_opener=lambda _: None,
        desktop_writer=writer,
    )
    assert result["clipboard_set"] is False
    assert captured_path.exists()
    assert "Bug Report" in captured_path.read_text(encoding="utf-8")


def test_open_bug_report_survives_url_failure(tmp_path, monkeypatch):
    """If the URL opener throws (no default browser, or QDesktopServices
    can't reach the URL), the function still completes and returns the
    mailto fallback so the UI can offer it."""
    from src.core import paths, bug_report
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)

    def boom(_url):
        raise RuntimeError("no browser")

    result = bug_report.open_bug_report(
        clipboard_setter=lambda _: None,
        url_opener=boom,
        desktop_writer=lambda _: None,
    )
    assert "Bug Report" in result["report"]
    assert result["mailto_url"].startswith("mailto:")


def test_save_report_to_desktop_writes_timestamped_file(tmp_path, monkeypatch):
    """Smoke-test the real save_report_to_desktop using a fake Desktop dir."""
    from src.core import bug_report
    fake_desktop = tmp_path / "Desktop"
    fake_desktop.mkdir()
    monkeypatch.setattr(bug_report, "_desktop_dir", lambda: fake_desktop)

    path = bug_report.save_report_to_desktop("hello\nworld\n")
    assert path is not None
    assert path.parent == fake_desktop
    assert path.name.startswith("VideoEditor-bug-report-")
    assert path.name.endswith(".md")
    assert path.read_text(encoding="utf-8") == "hello\nworld\n"
