"""Centralized rotating logger with path sanitization.

All log output goes through here so every line has filesystem paths redacted
before it lands on disk. Logs that users share in bug reports won't expose
their Windows username, home directory, or project layout.
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


# Patterns to redact in log messages. The Windows username can appear in
# many places (cmd lines, file paths, env vars), so we replace it with a
# placeholder before persisting the log.
_USER_DIR_RE = re.compile(r"([A-Z]:[\\/])Users([\\/])([^\\/\s'\"]+)", re.IGNORECASE)
_HOMEDIR_RE = re.compile(re.escape(str(Path.home())), re.IGNORECASE)


def sanitize_path(text: str) -> str:
    """Replace user-identifying path fragments with placeholders.

    - `C:\\Users\\alice\\...` → `C:\\Users\\<USER>\\...`
    - the user's actual $HOME → `<HOME>`
    """
    if not text:
        return text
    text = _HOMEDIR_RE.sub("<HOME>", text)
    text = _USER_DIR_RE.sub(r"\1Users\2<USER>", text)
    return text


class _SanitizingFormatter(logging.Formatter):
    """logging.Formatter that runs sanitize_path() over the final message."""

    def format(self, record: logging.LogRecord) -> str:
        return sanitize_path(super().format(record))


_initialized = False
_logger: Optional[logging.Logger] = None


def init(log_dir: Path, level: int = logging.INFO, force: bool = False) -> logging.Logger:
    """Initialize the rotating logger. Idempotent unless force=True.

    Pass force=True to switch log directories (useful in tests)."""
    global _initialized, _logger
    if _initialized and _logger is not None and not force:
        return _logger
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "editor.log"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,  # 2 MB per file
        backupCount=3,             # keep 3 rotations → ~8 MB max footprint
        encoding="utf-8",
    )
    handler.setFormatter(
        _SanitizingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger = logging.getLogger("video_editor")
    logger.setLevel(level)
    # Replace any prior handlers (in case of test re-init)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.addHandler(handler)
    logger.propagate = False
    _initialized = True
    _logger = logger
    return logger


def log() -> logging.Logger:
    """Get the configured logger. Falls back to a no-op logger if init() was
    never called (so importing this module never crashes a tool that just
    wants the function reference)."""
    if _logger is None:
        return logging.getLogger("video_editor_uninit")
    return _logger
