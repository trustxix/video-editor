"""Behavioral regression tests for MainWindow-level wiring.

Constructs the real MainWindow under the offscreen Qt platform. The startup
update check is patched out so the tests never touch the network.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch):
    # Never hit the network from a test: stub the async update check.
    import src.core.version as version
    monkeypatch.setattr(version, "check_for_update_async", lambda cb: None)
    from src.ui.main_window import MainWindow
    w = MainWindow()
    try:
        yield w
    finally:
        w.close()


def test_semitone_buttons_stay_in_slider_range(window):
    """The semitone buttons must not drive the preview past the slider/export
    maximum (2.0x) or minimum (0.25x) — otherwise preview and export diverge."""
    for _ in range(40):
        window._change_semitone(1)
    assert window._semitones == 12
    assert abs(window.player._speed - 2.0) < 1e-9          # preview rate
    assert abs(window.sld_speed.value() / 100.0 - 2.0) < 1e-9  # export reads this
    for _ in range(80):
        window._change_semitone(-1)
    assert window._semitones == -24
    assert abs(window.player._speed - 0.25) < 1e-9
    assert abs(window.sld_speed.value() / 100.0 - 0.25) < 1e-9


def test_formant_round_trips_through_video_item(window):
    from src.core.video_item import VideoItem
    it = VideoItem(path="x.mp4", duration_ms=1000, trim_end_ms=1000)
    window._queue = [it]
    window._queue_index = 0
    window.sld_formant.setValue(8)
    window._save_current_state()
    assert it.formant == -8.0  # slider 0..12 maps to 0..-12 semitones
    window.sld_formant.setValue(0)
    window._restore_state(it)
    assert window.sld_formant.value() == 8
    assert window.lbl_formant.text() == "-8 st"


def test_help_menu_actions_exist(window):
    assert "check_updates" in window._menu_actions
    assert "report_bug" in window._menu_actions


@pytest.mark.parametrize("outcome, expected", [
    ("error", ("warning", "Couldn't check for updates: GitHub returned HTTP 404.")),
    ("current", ("information", "You're on the latest version")),
    ("update", ("update", "9.9.9")),
])
def test_manual_update_check_reports_each_outcome(window, monkeypatch, outcome, expected):
    """A failed check must say so, not claim the app is up to date."""
    import src.core.version as version
    from src.ui import main_window as mw
    result = {
        "error": version.UpdateCheckError("GitHub returned HTTP 404"),
        "current": None,
        "update": {"latest": "9.9.9", "url": "", "notes": ""},
    }[outcome]
    monkeypatch.setattr(version, "check_for_update_async", lambda cb: cb(result))
    shown = []
    monkeypatch.setattr(mw.QMessageBox, "warning",
                        lambda parent, title, text: shown.append(("warning", text)))
    monkeypatch.setattr(mw.QMessageBox, "information",
                        lambda parent, title, text: shown.append(("information", text)))
    monkeypatch.setattr(window, "_show_update_available",
                        lambda info: shown.append(("update", info["latest"])))

    window._check_updates_manual()

    assert len(shown) == 1
    kind, text = shown[0]
    assert kind == expected[0] and text.startswith(expected[1])
    assert window._menu_actions["check_updates"].isEnabled()


def test_undo_coalesces_a_drag_gesture(window):
    """A continuous drag must collapse to exactly one undo entry holding the
    pre-drag state, so one Ctrl+Z reverts the whole drag (not a single tick)."""
    n0 = len(window._undo_stack)
    window._undo_begin_gesture()
    for val in (10, 20, 30, 40):
        window.sld_brightness.setValue(val)   # per-tick pushes are coalesced away
    window._undo_end_gesture()
    assert len(window._undo_stack) - n0 == 1
    assert window._undo_stack[-1]["brightness"] == 0  # the pre-drag value


def test_undo_history_is_per_clip(window, monkeypatch):
    """Undo must never reach into another clip's history. With one shared
    stack, undoing on clip B past B's own edits applied clip A's snapshot
    (trim, crop, speed, ...) to B, which then exported with A's edits."""
    from src.core.video_item import VideoItem
    monkeypatch.setattr(window.player, "load", lambda path: None)

    def clip(name, w, h):
        return VideoItem(path=name, video_w=w, video_h=h, duration_s=10.0,
                         duration_ms=10_000, trim_end_ms=10_000,
                         crop_w=w, crop_h=h, probed=True)

    a, b = clip("a.mp4", 1920, 1080), clip("b.mp4", 1280, 720)
    b.trim_start_ms, b.trim_end_ms = 2_000, 6_000
    window._queue = [a, b]
    window._queue_index = -1
    window._navigate_to(0)
    window._undo_begin_gesture()
    window.sld_brightness.setValue(40)
    window._undo_end_gesture()
    assert len(window._undo_stack) == 1

    window._navigate_to(1)
    window._undo()
    window._save_current_state()
    assert (b.trim_start_ms, b.trim_end_ms) == (2_000, 6_000)
    assert (b.crop_w, b.crop_h) == (1280, 720)

    window._navigate_to(0)  # A's own history survives the round trip
    window._undo()
    window._save_current_state()
    assert a.brightness == 0.0
    assert (a.trim_start_ms, a.trim_end_ms) == (0, 10_000)


def test_every_keybind_action_has_a_handler(window):
    """No dead keybinds: every declared editor/player action must have a wired
    handler, and there must be no orphan handlers."""
    from src.core.keybinds import ACTION_DEFS, PLAYER_ACTION_DEFS
    assert set(ACTION_DEFS) == set(window._action_handlers)
    assert set(PLAYER_ACTION_DEFS) == set(window._player_action_handlers)


def test_default_keybinds_parse_with_no_conflicts(window):
    """Every default binding must parse, and no two defaults in the same
    context may map to the same key combo."""
    from src.core.keybinds import ACTION_DEFS, PLAYER_ACTION_DEFS, parse_binding
    for defs in (ACTION_DEFS, PLAYER_ACTION_DEFS):
        seen = {}
        for aid, ad in defs.items():
            for b in ad.default_bindings:
                kb = parse_binding(b)
                assert kb is not None, f"unparseable default {b!r} for {aid}"
                assert kb not in seen, f"default conflict {b!r}: {seen.get(kb)} and {aid}"
                seen[kb] = aid


def test_aspect_lock_enforced_on_spinbox_entry(window):
    """A locked aspect ratio must constrain typed crop dims, not just drags."""
    window._video_w, window._video_h = 1920, 1080
    window.crop_overlay.set_video_size(1920, 1080)
    window.crop_overlay.set_aspect_ratio((16, 9))
    window.spn_w.setValue(800)
    assert window.spn_h.value() == 450   # 800 * 9/16
    window.spn_h.setValue(360)
    assert window.spn_w.value() == 640   # 360 * 16/9
