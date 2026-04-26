"""Build a clipboard-friendly bug report from recent log lines.

The log contents are sanitized via `log_setup.sanitize_path` before being
included so the report never carries the user's Windows username or home
directory. Nothing is transmitted automatically — the report is copied to
the clipboard AND saved to the Desktop, the GitHub issue URL is opened,
and a `mailto:` fallback URL is returned so the UI can offer it as a
secondary path when GitHub isn't reachable.
"""
from __future__ import annotations

import datetime
import os
import platform
import sys
from pathlib import Path
from typing import Callable, Optional

from src.core import paths
from src.core.log_setup import sanitize_path
from src.core.version import VERSION

ISSUE_URL_TEMPLATE = (
    "https://github.com/trustxix/video-editor/issues/new"
    "?title=Bug%20report&body=Paste%20clipboard%20contents%20here"
)

# Fallback contact when the GitHub repo isn't reachable (or doesn't exist
# yet). The mailto: URL pre-fills the subject + a body hint pointing at
# the saved-to-Desktop file. Body is tiny because mailto: bodies have a
# ~2 KB practical limit across email clients.
MAILTO_FALLBACK = (
    "mailto:trustofficialbusiness@gmail.com"
    "?subject=Video%20Editor%20bug%20report"
    "&body=See%20attached%20file%20on%20Desktop%20%28VideoEditor-bug-report-*.md%29.%0A"
    "%0AOr%20paste%20clipboard%20contents%20here%3A%0A"
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


def _desktop_dir() -> Path:
    """Return the user's Desktop, falling back to the config dir.

    `USERPROFILE\\Desktop` covers Windows; `~/Desktop` covers POSIX. If
    neither exists (rare — locked-down corporate environments), fall back
    to the app's config dir so the file always lands somewhere writable."""
    candidates = [
        os.environ.get("USERPROFILE", ""),
        os.path.expanduser("~"),
    ]
    for base in candidates:
        if not base:
            continue
        p = Path(base) / "Desktop"
        if p.is_dir():
            return p
    return paths.config_dir()


def save_report_to_desktop(report: str) -> Path | None:
    """Drop a timestamped copy of `report` onto the user's Desktop.

    Returns the file path on success, None on failure. Filename pattern:
    ``VideoEditor-bug-report-YYYYMMDD-HHMMSS.md``. Best-effort: if the
    Desktop is read-only or missing, returns None silently — the
    clipboard copy is still available."""
    try:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = _desktop_dir() / f"VideoEditor-bug-report-{ts}.md"
        path.write_text(report, encoding="utf-8")
        return path
    except OSError:
        return None


def open_bug_report(
    clipboard_setter: Optional[Callable[[str], None]] = None,
    url_opener: Optional[Callable[[str], None]] = None,
    desktop_writer: Optional[Callable[[str], Path | None]] = None,
) -> dict:
    """Build the report and surface it through three channels:

    1. Copy to clipboard (immediate paste anywhere).
    2. Save a timestamped Markdown file on the user's Desktop (always
       works, survives a closed app and can be attached to email).
    3. Open the GitHub issues URL (works once the repo exists; a 404 from
       a not-yet-published repo is cosmetic — the file + clipboard cover
       the report).

    Returns a dict ``{"report", "clipboard_set", "saved_path",
    "issue_url", "mailto_url"}`` so the UI can show a status dialog with
    accurate next-step guidance — ``mailto_url`` is the email fallback
    when the GitHub repo isn't reachable.

    The three callable parameters are injection points for testing —
    production callers leave them None and the function lazy-imports Qt.
    """
    report = build_report()
    saved_path: Path | None = None
    clipboard_ok = False

    if clipboard_setter is None:
        from PyQt6.QtWidgets import QApplication
        clipboard_setter = lambda text: QApplication.clipboard().setText(text)
    if url_opener is None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        url_opener = lambda url: QDesktopServices.openUrl(QUrl(url))
    if desktop_writer is None:
        desktop_writer = save_report_to_desktop

    try:
        clipboard_setter(report)
        clipboard_ok = True
    except Exception:
        clipboard_ok = False

    saved_path = desktop_writer(report)

    # Open the GitHub issues URL — graceful 404 if the repo doesn't exist
    # yet. The user's clipboard + file fallbacks remain.
    try:
        url_opener(ISSUE_URL_TEMPLATE)
    except Exception:
        pass

    return {
        "report":         report,
        "clipboard_set":  clipboard_ok,
        "saved_path":     str(saved_path) if saved_path else None,
        "issue_url":      ISSUE_URL_TEMPLATE,
        "mailto_url":     MAILTO_FALLBACK,
    }
