"""Build a clipboard-friendly bug report from recent log lines.

The log contents are sanitized via `log_setup.sanitize_path` before being
included so the report never carries the user's Windows username or home
directory. Nothing is transmitted automatically — the report is copied to
the clipboard, then a GitHub issue URL is opened so the user can paste
and submit at their discretion.
"""
from __future__ import annotations

import platform
import sys
from typing import Optional

from src.core import paths
from src.core.log_setup import sanitize_path
from src.core.version import VERSION

# REPLACE before publishing the project to a real repo.
ISSUE_URL_TEMPLATE = (
    "https://github.com/PLACEHOLDER_OWNER/PLACEHOLDER_REPO/issues/new"
    "?title=Bug%20report&body=Paste%20clipboard%20contents%20here"
)


def build_report(include_log_lines: int = 200) -> str:
    """Return a markdown-formatted bug report ready to paste into GitHub Issues."""
    log_path = paths.config_dir() / "editor.log"
    log_excerpt = ""
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            log_excerpt = sanitize_path("\n".join(lines[-include_log_lines:]))
        except OSError:
            log_excerpt = "(log read failed)"
    return (
        "## Bug Report\n\n"
        f"**Version:** {VERSION}\n"
        f"**Python:** {sys.version.split()[0]}\n"
        f"**Platform:** {platform.platform()}\n\n"
        "### What I expected\n"
        "<describe expected behavior>\n\n"
        "### What happened\n"
        "<describe actual behavior>\n\n"
        "### Steps to reproduce\n"
        "1.\n"
        "2.\n"
        "3.\n\n"
        f"### Recent log (last {include_log_lines} lines, paths sanitized)\n"
        "```\n"
        f"{log_excerpt}\n"
        "```\n"
    )


def open_bug_report(clipboard_setter: Optional[callable] = None,
                    url_opener: Optional[callable] = None) -> str:
    """Build the report, copy to clipboard, open the issue URL.

    Returns the report string. The two callable parameters are injection
    points for testing — production callers leave them None and the
    function imports Qt lazily."""
    report = build_report()

    if clipboard_setter is None:
        from PyQt6.QtWidgets import QApplication
        clipboard_setter = lambda text: QApplication.clipboard().setText(text)
    if url_opener is None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        url_opener = lambda url: QDesktopServices.openUrl(QUrl(url))

    clipboard_setter(report)
    url_opener(ISSUE_URL_TEMPLATE)
    return report
