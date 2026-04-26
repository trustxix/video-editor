"""Tests for src/core/settings_migration.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.settings_migration import CURRENT_VERSION, migrate


def test_migrate_empty_dict_to_current():
    out = migrate({})
    assert out["version"] == CURRENT_VERSION
    # Phase 1 keys
    assert "ui_scale" in out
    assert "theme" in out
    assert "check_for_updates" in out
    assert "crash_reports" in out


def test_migrate_preserves_existing_user_values():
    out = migrate({"ui_scale": 110, "theme": "light", "codec": "h265"})
    assert out["ui_scale"] == 110
    assert out["theme"] == "light"
    assert out["codec"] == "h265"  # unrelated keys passed through
    assert out["version"] == CURRENT_VERSION


def test_migrate_does_not_mutate_input():
    """migrate() returns a new dict — original must be untouched."""
    original = {"ui_scale": 95}
    snapshot = dict(original)
    migrate(original)
    assert original == snapshot


def test_migrate_idempotent():
    """Calling migrate twice returns the same result as calling once."""
    once = migrate({"ui_scale": 95})
    twice = migrate(once)
    assert once == twice


def test_migrate_already_current_version():
    """An already-current dict passes through unchanged (deep-copied)."""
    current = {"version": CURRENT_VERSION, "ui_scale": 100, "theme": "dark"}
    out = migrate(current)
    assert out["version"] == CURRENT_VERSION
    assert out["ui_scale"] == 100


def test_migrate_unknown_higher_version_passes_through():
    """If user runs older app code with newer settings file, don't downgrade."""
    out = migrate({"version": 999, "something_new": "value"})
    assert out["version"] == 999
    assert out["something_new"] == "value"


def test_migrate_default_crash_reports_is_off():
    """Privacy: crash reports must default to OFF (opt-in)."""
    out = migrate({})
    assert out["crash_reports"] is False


def test_migrate_default_update_check_is_on():
    """Update check defaults to ON for usability — disclosed in PRIVACY.md."""
    out = migrate({})
    assert out["check_for_updates"] is True
