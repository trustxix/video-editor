"""Local crash reporting + optional Sentry pipe.

Default: when an uncaught exception bubbles to sys.excepthook, write a
sanitized JSON dump to <config>/crashes/ (capped at `_MAX_CRASH_DUMPS`).

Optional Sentry: exceptions are also sent to Sentry only if the user opted
in (the `crash_reports` setting, pushed in via `set_remote_reporting`) AND
the `SENTRY_DSN` env var is set AND `sentry-sdk` is importable. Official
builds don't bundle sentry-sdk, so they are local-only writers — no network
traffic, no third-party dependency required.

All transmitted/stored content is run through `sanitize_path` so the
user's Windows username and home directory don't end up in remote dumps
or files they share for bug reports.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
import traceback
import uuid
from pathlib import Path
from types import TracebackType
from typing import Optional

from src.core import paths
from src.core.log_setup import sanitize_path
from src.core.version import VERSION


def _crashes_dir() -> Path:
    d = paths.config_dir() / "crashes"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Cap retained crash dumps. Nothing acknowledges them yet, so without this
# they accumulate forever in <config>/crashes/.
_MAX_CRASH_DUMPS = 20


def _prune_old_crashes(keep: int = _MAX_CRASH_DUMPS) -> None:
    """Delete all but the most recent `keep` crash dumps. The filename embeds
    the timestamp so a name sort is chronological. Best-effort — never raises
    from the crash path."""
    try:
        dumps = sorted(_crashes_dir().glob("crash_*.json"))
        for old in dumps[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass


def write_crash(exc_type: type, exc_value: BaseException,
                exc_tb: Optional[TracebackType]) -> Path:
    """Write a sanitized crash dump to <config>/crashes/. Returns the file path."""
    dump = {
        "id":              str(uuid.uuid4()),
        "timestamp":       time.time(),
        "version":         VERSION,
        "python":          sys.version,
        "platform":        platform.platform(),
        "exception_type":  exc_type.__name__ if exc_type else "Unknown",
        "exception_value": sanitize_path(str(exc_value) if exc_value else ""),
        "traceback":       sanitize_path(
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        ),
    }
    path = _crashes_dir() / f"crash_{int(dump['timestamp'])}_{dump['id'][:8]}.json"
    try:
        path.write_text(json.dumps(dump, indent=2), encoding="utf-8")
    except OSError as e:
        # Last resort — print to stderr (which main.py routes to the log).
        sys.stderr.write(f"[crash_reporter] dump write failed ({e}): {dump}\n")
    _prune_old_crashes()
    return path


_installed = False


def install_global_handler() -> None:
    """Install sys.excepthook to capture uncaught exceptions. Idempotent."""
    global _installed
    if _installed:
        return
    prev = sys.excepthook

    def handler(exc_type, exc_value, exc_tb):
        try:
            write_crash(exc_type, exc_value, exc_tb)
            _maybe_send_sentry(exc_type, exc_value, exc_tb)
        finally:
            # Always chain to the previous handler so the traceback still prints.
            prev(exc_type, exc_value, exc_tb)

    sys.excepthook = handler
    _installed = True


# Off until the user's saved choice says otherwise, so a crash before the
# settings are loaded is never transmitted.
_remote_consent = False


def set_remote_reporting(enabled: bool) -> None:
    """Apply the user's `crash_reports` setting. Local dumps are unaffected."""
    global _remote_consent
    _remote_consent = enabled is True


def _maybe_send_sentry(exc_type, exc_value, exc_tb) -> None:
    """Capture the exception in Sentry if the user opted in, SENTRY_DSN is
    set, and sentry-sdk is importable."""
    if not _remote_consent or not os.environ.get("SENTRY_DSN"):
        return
    try:
        import sentry_sdk
    except ImportError:
        return
    try:
        if not sentry_sdk.Hub.current.client:
            sentry_sdk.init(
                dsn=os.environ["SENTRY_DSN"],
                release=VERSION,
                send_default_pii=False,
                before_send=_sentry_scrub,
            )
        sentry_sdk.capture_exception((exc_type, exc_value, exc_tb))
    except Exception:
        # NEVER raise from a crash handler.
        pass


def _sentry_scrub(event, hint):
    """Sanitize the event payload before it's sent to Sentry."""
    if "exception" in event:
        for v in event["exception"].get("values", []):
            if "value" in v:
                v["value"] = sanitize_path(str(v["value"]))
            for frame in v.get("stacktrace", {}).get("frames", []):
                if "filename" in frame:
                    frame["filename"] = sanitize_path(str(frame["filename"]))
                if "abs_path" in frame:
                    frame["abs_path"] = sanitize_path(str(frame["abs_path"]))
    return event


def list_pending_crashes() -> list[Path]:
    """Crash dumps that haven't been acknowledged by the user."""
    return sorted(_crashes_dir().glob("crash_*.json"))


def acknowledge(path: Path) -> None:
    """Mark a crash dump as seen (delete it). Silent on failure."""
    try:
        path.unlink()
    except OSError:
        pass
