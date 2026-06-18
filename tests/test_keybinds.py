"""Tests for src/core/keybinds.py — parse/format/Manager round-trips."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.keybinds import (
    ACTION_DEFS,
    PLAYER_ACTION_DEFS,
    Keybind,
    KeybindManager,
    format_binding,
    parse_binding,
)


# ─── parse_binding ────────────────────────────────────────────────────────

def test_parse_simple_letter():
    kb = parse_binding("S")
    assert kb is not None
    assert kb.modifiers == 0
    assert kb.mouse_button == 0
    assert kb.key != 0


def test_parse_with_ctrl():
    kb = parse_binding("Ctrl+S")
    assert kb is not None
    assert kb.modifiers != 0
    assert kb.key != 0


def test_parse_with_all_modifiers():
    kb = parse_binding("Ctrl+Shift+Alt+S")
    assert kb is not None
    # All three modifier bits set
    from PyQt6.QtCore import Qt
    expected = (
        Qt.KeyboardModifier.ControlModifier.value |
        Qt.KeyboardModifier.ShiftModifier.value |
        Qt.KeyboardModifier.AltModifier.value
    )
    assert kb.modifiers == expected


def test_parse_mouse_button():
    kb = parse_binding("MouseBack")
    assert kb is not None
    assert kb.mouse_button != 0
    assert kb.key == 0


def test_parse_invalid_returns_none():
    assert parse_binding("NotARealKey") is None
    assert parse_binding("") is None
    assert parse_binding("   ") is None


def test_parse_modifier_only_returns_none():
    """Bare 'Ctrl' (no key or mouse) is invalid."""
    assert parse_binding("Ctrl") is None


# ─── format_binding round-trip ────────────────────────────────────────────

@pytest.mark.parametrize("s", [
    "S",
    "Ctrl+S",
    "Shift+Right",
    "Ctrl+Shift+W",
    "Ctrl+Alt+Space",
    "F11",
    "Period",
    "Comma",
    "Backspace",
    "MouseBack",
    "MouseForward",
])
def test_parse_format_round_trip(s):
    kb = parse_binding(s)
    assert kb is not None, f"{s!r} did not parse"
    assert format_binding(kb) == s


def test_exotic_mouse_button_round_trips():
    """An exotic mouse button not in _MOUSE_BUTTON_MAP must serialize to a
    numeric, parseable token (the old 'Mouse?' was a constant that collided
    for every unmapped button and could not be parsed back)."""
    from src.core.keybinds import Keybind
    kb = Keybind(mouse_button=99)
    s = format_binding(kb)
    assert s == "Mouse99"
    assert parse_binding(s) == kb
    # With a modifier too
    ctrl = parse_binding("Ctrl+Mouse99")
    assert ctrl is not None and ctrl.mouse_button == 99


# ─── KeybindManager ──────────────────────────────────────────────────────

def test_manager_default_bindings_loaded():
    m = KeybindManager(ACTION_DEFS)
    space = parse_binding("Space")
    assert "play_pause" in m.lookup(space)


def test_manager_custom_binding_overrides_default():
    m = KeybindManager(ACTION_DEFS, bindings={"play_pause": ["P"]})
    p = parse_binding("P")
    space = parse_binding("Space")
    assert "play_pause" in m.lookup(p)
    assert "play_pause" not in m.lookup(space)  # default replaced


def test_manager_set_bindings_rebuilds_lookup():
    m = KeybindManager(ACTION_DEFS)
    m.set_bindings("play_pause", ["X"])
    x = parse_binding("X")
    space = parse_binding("Space")
    assert "play_pause" in m.lookup(x)
    assert "play_pause" not in m.lookup(space)


def test_manager_to_dict_round_trip():
    m1 = KeybindManager(ACTION_DEFS)
    m1.set_bindings("export_current", ["F12"])
    saved = m1.to_dict()
    m2 = KeybindManager(ACTION_DEFS, bindings=saved)
    f12 = parse_binding("F12")
    assert "export_current" in m2.lookup(f12)


def test_manager_reset_to_defaults():
    m = KeybindManager(ACTION_DEFS, bindings={"play_pause": ["P"]})
    p = parse_binding("P")
    assert "play_pause" in m.lookup(p)
    m.reset_to_defaults()
    space = parse_binding("Space")
    assert "play_pause" in m.lookup(space)
    assert "play_pause" not in m.lookup(p)


def test_manager_garbage_bindings_ignored():
    """Saved bindings file may contain non-string entries — must not crash."""
    bad = {"play_pause": ["Space", 42, None, "Ctrl+P"]}
    m = KeybindManager(ACTION_DEFS, bindings=bad)
    binds = m.get_bindings("play_pause")
    assert "Space" in binds
    assert "Ctrl+P" in binds
    assert 42 not in binds


def test_manager_player_actions_load():
    """PLAYER_ACTION_DEFS is the second registry — must work the same way."""
    m = KeybindManager(PLAYER_ACTION_DEFS)
    space = parse_binding("Space")
    assert "p_play_pause" in m.lookup(space)


def test_get_menu_shortcut_skips_mouse():
    """Menu shortcut display can't show mouse — skip mouse bindings."""
    m = KeybindManager(ACTION_DEFS,
                       bindings={"play_pause": ["MouseBack", "Ctrl+P"]})
    assert m.get_menu_shortcut("play_pause") == "Ctrl+P"
