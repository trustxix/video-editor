"""Settings file version migrations.

Legacy settings files (no `version` key) are treated as version 0. Each
registered migration takes a dict and returns the same dict bumped to the
next version. Migrations are applied in order until the dict reaches
`CURRENT_VERSION`.

Add a new migration:
    @migration(from_version=2)
    def _2_to_3(s: dict) -> dict:
        s["new_field"] = "default"
        if "old_field" in s:
            s["new_name"] = s.pop("old_field")
        s["version"] = 3
        return s

Then bump CURRENT_VERSION to 3 above.
"""
from __future__ import annotations

import copy
from typing import Callable, Dict


CURRENT_VERSION = 1


_migrations: Dict[int, Callable[[dict], dict]] = {}


def migration(from_version: int):
    """Decorator: register a migration FROM the given version to from_version+1."""
    def decorator(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        if from_version in _migrations:
            raise ValueError(f"Migration from version {from_version} already registered")
        _migrations[from_version] = fn
        return fn
    return decorator


@migration(from_version=0)
def _0_to_1(s: dict) -> dict:
    """Initial migration — ensure new observability/UI keys exist with defaults.

    Adds: check_for_updates (default True), crash_reports (default False — opt-in)."""
    s.setdefault("ui_scale", 90)
    s.setdefault("theme", "dark")
    s.setdefault("check_for_updates", True)
    s.setdefault("crash_reports", False)
    s["version"] = 1
    return s


def migrate(settings: dict) -> dict:
    """Apply all migrations from settings['version'] (default 0) to CURRENT_VERSION.

    Returns a new dict (does not mutate the input). Idempotent — already-current
    settings pass through unchanged."""
    out = copy.deepcopy(settings)
    v = out.get("version", 0)
    while v < CURRENT_VERSION:
        if v in _migrations:
            out = _migrations[v](out)
        else:
            # No migration registered for this gap — bump version anyway
            # so we don't infinite-loop. Indicates a bug if we hit this.
            out["version"] = v + 1
        new_v = out.get("version", v + 1)
        if new_v <= v:
            # Migration didn't bump version — would infinite-loop. Force bump.
            new_v = v + 1
            out["version"] = new_v
        v = new_v
    return out
