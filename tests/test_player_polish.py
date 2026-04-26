"""Functional smoke test for player-mode polish features.

Run with: python tests/test_player_polish.py
Uses the offscreen Qt platform so no display is required.
"""

import os
import sys
import time
import unittest.mock as mock
from pathlib import Path

# Make the project root importable when running this file directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QPoint, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

app = QApplication([])

from src.ui.player_mode import PlayerMode


def pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    pm = PlayerMode()
    pm._settings = {"player_osd": True, "player_seek_step": 5}
    pm.resize(1200, 700)
    pm.show()
    app.processEvents()

    test_video = (
        r"C:\Users\trust\Videos\Barnyard Banter - Pig Is A Bomb Pig "
        r"Backatthebarnyard Dankmemes Meme [7289152578955382048].mp4"
    )

    # Simulate a loaded video without actually involving QMediaPlayer
    pm.seek_slider.setRange(0, 1000)
    pm.seek_slider.setValue(0)
    pm.seek_slider.set_duration(9755)
    pm.seek_slider.set_current_path(test_video)
    pm._duration_ms = 9755
    pm._current_path = test_video
    pm._playlist = [test_video]
    pm._playlist_index = 0

    sb = pm.seek_slider
    sb.setMinimumWidth(400)
    sb.resize(400, 24)
    app.processEvents()

    print(f"[1] SeekBar size: {sb.size().width()}x{sb.size().height()}")
    for x in (50, 200, 380):
        QTest.mouseMove(sb, QPoint(x, 12))
        app.processEvents()
        print(f"    hover x={x}: hover_ms={sb._hover_ms}, popup={sb._popup.isVisible()}")

    pump(2.0)
    total = sum(len(v) for v in sb._cache.values())
    print(f"[2] Cache after hover: {len(sb._cache)} videos / {total} thumbnails")
    assert total > 0, "no thumbnails cached"

    QTest.mouseMove(sb, QPoint(0, 0))
    app.processEvents()
    sb.leaveEvent(None)
    print(f"[3] After leave: popup={sb._popup.isVisible()}")

    # A-B markers
    pm.player = mock.MagicMock()
    pm.player.position.return_value = 2000
    pm.ab_mark()
    print(f"[4] 1st ab: a={pm._loop_a_ms} sb_a={sb._a_ms}")
    pm.player.position.return_value = 7000
    pm.ab_mark()
    print(f"    2nd ab: a={pm._loop_a_ms} b={pm._loop_b_ms} "
          f"sb_a={sb._a_ms} sb_b={sb._b_ms}")
    assert sb._a_ms == 2000 and sb._b_ms == 7000

    # Fullscreen / title overlay
    pm.toggle_fullscreen()
    print(f"[5] Fullscreen on: controls_visible={pm._controls_widget.isVisible()}")
    pm._on_surface_mouse_move()
    app.processEvents()
    print(f"    Mouse move shows: controls={pm._controls_widget.isVisible()} "
          f"title={pm.surface._title_visible}")
    pm.toggle_fullscreen()
    print(f"    Exit fullscreen: title={pm.surface._title_visible}")
    assert not pm.surface._title_visible

    # CompactVolumeControl hover animation
    cv = pm._vol_widget
    cv.enterEvent(None)
    pump(0.3)
    print(f"[6] After hover enter: opacity={cv._effect.opacity():.2f}")
    assert cv._effect.opacity() > 0.5
    cv.leaveEvent(None)
    pump(0.6)
    print(f"    After hover leave: opacity={cv._effect.opacity():.2f}")
    assert cv._effect.opacity() < 0.1

    # Loading state
    pm.surface.set_loading(True)
    assert pm.surface._spinner_timer.isActive()
    pm.surface.set_loading(False)
    assert not pm.surface._spinner_timer.isActive()
    print("[7] Loading spinner toggled OK")

    # OSD lifecycle — pump past the first 50ms tick so fade-in registers
    pm.surface.show_osd("Test", primary=True, duration_ms=2500)
    pump(0.2)
    op_during = pm.surface._osd_opacity
    print(f"[8] OSD primary: opacity={op_during:.2f}")
    assert op_during > 0, f"OSD did not fade in (opacity={op_during})"
    pump(3.0)
    print(f"    OSD after 2s: text=\"{pm.surface._osd_text}\" "
          f"opacity={pm.surface._osd_opacity:.2f}")
    assert pm.surface._osd_text == ""

    # Preload mechanism: verify request triggered at 80%
    pm._preload_cache.clear()
    pm._preload_requested_for = None
    pm._playlist = [test_video, test_video]  # pretend two-file playlist
    pm._playlist_index = 0
    pm._maybe_preload_next(int(9755 * 0.85))
    print(f"[9] Preload requested for: {pm._preload_requested_for is not None}")
    assert pm._preload_requested_for is not None

    pump(2.0)
    print(f"    Preload cache: {len(pm._preload_cache)} entries")

    pm.release()
    print("[10] release() OK")
    print("\nALL CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
