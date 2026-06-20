"""Player-mode behavioral tests (offscreen Qt).

Covers crash-safety of keybind-invoked methods with no clip, and frame-accurate
frame stepping (the player's frame-step must show the exact frame via ffmpeg,
not QMediaPlayer's keyframe-snapped seek).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QApplication


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def player(qapp):
    from src.ui.player_mode import PlayerMode
    pm = PlayerMode()
    pm._settings = {"player_osd": True}
    try:
        yield pm
    finally:
        pm.release()


def test_player_methods_safe_without_clip(player):
    """Every keybind-invoked player method must be a safe no-op with no clip
    loaded (regression guard against the kind of NameError that crashed the
    loading spinner)."""
    calls = [
        ("_toggle_play", ()), ("seek_relative", (5000,)), ("frame_step", (1,)),
        ("frame_step", (-1,)), ("adjust_speed", (0.25,)), ("reset_speed", ()),
        ("adjust_volume", (5,)), ("toggle_mute", ()), ("jump_to_start", ()),
        ("jump_to_end", ()), ("cycle_loop_mode", ()), ("ab_mark", ()),
        ("toggle_shuffle", ()), ("cycle_aspect", ()), ("toggle_compact", ()),
        ("refresh_file_list", ()), ("take_screenshot", ()), ("copy_path", ()),
        ("_go_next", ()), ("_go_prev", ()),
    ]
    for name, args in calls:
        getattr(player, name)(*args)  # must not raise


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")
def test_player_frame_step_is_frame_accurate(player, fixture_video):
    player._current_path = str(fixture_video)
    player._duration_ms = 5000
    player._stepped_pos = None

    player.frame_step(1)
    assert player._stepped_pos == 33  # exactly one frame at 30 fps
    assert player.surface._stepping   # live frames blocked while showing it
    assert player.surface._image is not None and not player.surface._image.isNull()
    img1 = player.surface._image.copy()

    player.frame_step(1)
    assert player._stepped_pos == 66  # advances from the stepped pos, not a keyframe
    # The displayed frame must actually change — proves it's not keyframe-stuck.
    changed = sum(
        1 for y in range(0, 240, 40) for x in range(0, 320, 40)
        if img1.pixel(x, y) != player.surface._image.pixel(x, y)
    )
    assert changed > 0, "frame did not advance (keyframe-stuck)"

    player.frame_step(-1)
    assert player._stepped_pos == 33

    from PyQt6.QtMultimedia import QMediaPlayer
    player._on_state_changed(QMediaPlayer.PlaybackState.PlayingState)
    assert not player.surface._stepping and player._stepped_pos is None
