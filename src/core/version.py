"""Application version + update check.

Single source of truth for the app version string. The update check is a
single HTTPS GET against GitHub's "latest release" API endpoint, so
publishing a GitHub Release is the entire update workflow — there is no
manifest file to keep in sync. The fields used from the response:

    tag_name  "v0.1.1"   the newest published release (never a draft or
                         pre-release)
    html_url  the release page, offered to the user
    body      the release notes; the first paragraph is shown

Privacy: the request includes only the IP (visible to GitHub via the TCP
connection) and a User-Agent header carrying the installed app version.
No filenames, settings, or PII are transmitted. See PRIVACY.md.
"""
from __future__ import annotations

import json
import threading
from typing import Callable, Optional

VERSION = "0.1.1"

UPDATE_URL = "https://api.github.com/repos/trustxix/video-editor/releases/latest"


class UpdateCheckError(Exception):
    """The update check could not complete (offline, HTTP error, bad response)."""


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


def _release_summary(body: str, limit: int = 400) -> str:
    """First paragraph of the release notes, with markdown bold stripped."""
    first = body.replace("\r\n", "\n").strip().split("\n\n", 1)[0]
    first = first.replace("**", "").strip()
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


def check_for_update(
    current: str = VERSION,
    timeout: float = 5.0,
) -> Optional[dict]:
    """Synchronous update check.

    Returns a dict {latest, url, notes} if a newer version is available and
    None if `current` is up to date. Raises UpdateCheckError when the check
    itself fails, so callers can tell "up to date" from "couldn't check".
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        UPDATE_URL,
        headers={
            "User-Agent": f"VideoEditor/{current}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise UpdateCheckError(f"GitHub returned HTTP {e.code}") from e
    except OSError as e:  # URLError, timeouts, connection resets
        reason = getattr(e, "reason", None) or e
        raise UpdateCheckError(f"could not reach GitHub ({reason})") from e
    except ValueError as e:  # bad UTF-8 or JSON
        raise UpdateCheckError("GitHub sent an unreadable response") from e
    if not isinstance(data, dict):
        raise UpdateCheckError("GitHub sent an unexpected response")

    latest = str(data.get("tag_name") or "").strip().lstrip("vV")
    if not latest:
        raise UpdateCheckError("the latest release has no version tag")
    if parse_version(latest) <= parse_version(current):
        return None
    # Validate the URL before surfacing it to the UI — defends against a
    # tampered response steering the user to a non-GitHub site. Only
    # github.com release pages are a legitimate target.
    raw_url = str(data.get("html_url") or "")
    safe_url = raw_url if raw_url.startswith("https://github.com/") else ""
    return {
        "latest": latest,
        "url":    safe_url,
        "notes":  _release_summary(str(data.get("body") or "")),
    }


def check_for_update_async(
    callback: Callable[[dict | None | UpdateCheckError], None],
) -> None:
    """Background-thread update check. The callback receives the update dict,
    None when up to date, or the UpdateCheckError when the check failed.
    It is invoked from the worker thread — Qt callers must marshal back to
    the UI thread (e.g. via a queued signal) before touching widgets."""
    def run():
        try:
            result = check_for_update()
        except UpdateCheckError as e:
            result = e
        callback(result)

    threading.Thread(target=run, daemon=True, name="VideoEditor-UpdateCheck").start()
