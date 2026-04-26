"""Application version + update check.

Single source of truth for the app version string. The update check is a
single HTTPS GET against a static JSON file hosted on GitHub raw — free,
no infrastructure to maintain. The JSON schema:

    {
      "latest": "0.1.1",
      "url":    "https://github.com/<owner>/<repo>/releases/tag/v0.1.1",
      "notes":  "What's new in this version"
    }

Privacy: the request includes only the IP (visible to GitHub via the TCP
connection) and a User-Agent header carrying the installed app version.
No filenames, settings, or PII are transmitted. See PRIVACY.md.
"""
from __future__ import annotations

import json
import threading
from typing import Callable, Optional

VERSION = "0.1.0"

# Static JSON hosted on GitHub raw — free, no infrastructure to maintain.
# When the repo exists at this URL with a `release/latest.json`, the update
# check activates; until then it 404s and returns None silently.
UPDATE_URL = "https://raw.githubusercontent.com/trustxix/video-editor/main/release/latest.json"


def parse_version(s: str) -> tuple[int, ...]:
    """Parse a dotted version like '1.2.3' into a tuple of ints.

    Non-numeric components become 0 — keeps comparison total even when
    someone publishes a pre-release tag like '1.2.beta'. Two-component
    versions like '1.2' compare as (1, 2) which is < (1, 2, 0)'s tuple
    order — Python tuple comparison handles this correctly."""
    parts: list[int] = []
    for p in s.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            # Strip non-digit suffix and try again, e.g. '2beta' → 2
            digits = "".join(c for c in p if c.isdigit())
            parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_for_update(
    current: str = VERSION,
    timeout: float = 5.0,
) -> Optional[dict]:
    """Synchronous update check.

    Returns a dict {latest, url, notes} if a newer version is available,
    None otherwise. Network failures, malformed JSON, and unreachable hosts
    all return None — callers do not need to handle exceptions.
    """
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(
            UPDATE_URL,
            headers={"User-Agent": f"VideoEditor/{current}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
        return None

    latest = str(data.get("latest", ""))
    if not latest:
        return None
    if parse_version(latest) <= parse_version(current):
        return None
    return {
        "latest": latest,
        "url":    str(data.get("url", "")),
        "notes":  str(data.get("notes", "")),
    }


def check_for_update_async(callback: Callable[[Optional[dict]], None]) -> None:
    """Background-thread update check. Callback is invoked from the worker
    thread — Qt callers must marshal back to the UI thread (e.g. via
    QTimer.singleShot(0, lambda: ...)) before touching widgets."""
    t = threading.Thread(
        target=lambda: callback(check_for_update()),
        daemon=True,
        name="VideoEditor-UpdateCheck",
    )
    t.start()
