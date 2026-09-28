"""Presets, VideoItem defaults, and the crop/trim widgets (ported from the old
import-time runner tests/test_fixes.py)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.core.ffmpeg_runner import get_output_path
from src.core.presets import calc_preset_crop, calc_stretch_to_fit
from src.core.video_item import VideoItem


def test_video_item_defaults():
    item = VideoItem(path="test.mp4")
    assert item.speed == 1.0
    assert not item.probed
    assert item.crop_w == 0


def test_preset_crop_is_even_and_in_bounds():
    x, y, w, h = calc_preset_crop("9:16", 1920, 1080)
    assert w % 2 == 0 and h % 2 == 0
    assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080


def test_free_preset_is_the_full_frame():
    assert calc_preset_crop("Free", 1920, 1080) == (0, 0, 1920, 1080)


def test_square_preset_is_square():
    _, _, w, h = calc_preset_crop("1:1", 1920, 1080)
    assert w == h


def test_stretch_to_fit_compresses_one_axis():
    sh, sv = calc_stretch_to_fit("9:16", 1920, 1080)
    assert sh <= 1.0 or sv <= 1.0


def test_stretch_to_fit_with_zero_dims_is_identity():
    assert calc_stretch_to_fit("9:16", 0, 0) == (1.0, 1.0)


def test_output_path_gets_the_suffix():
    assert "test_edited" in get_output_path("test.mp4", "_edited")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def overlay(qapp):
    from src.ui.crop_overlay import CropOverlay
    o = CropOverlay()
    o.resize(640, 360)
    o._video_w, o._video_h = 1920, 1080
    yield o
    o.deleteLater()


def test_set_crop_from_video_stores_video_coords(overlay):
    overlay.set_crop_from_video(480, 270, 960, 540)
    assert (overlay._crop_vx, overlay._crop_vy, overlay._crop_vw, overlay._crop_vh) == (480, 270, 960, 540)


def test_resize_does_not_rewrite_video_coords(overlay):
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QResizeEvent
    overlay.set_crop_from_video(480, 270, 960, 540)
    overlay.resizeEvent(QResizeEvent(QSize(800, 450), QSize(640, 360)))
    assert (overlay._crop_vx, overlay._crop_vy, overlay._crop_vw, overlay._crop_vh) == (480, 270, 960, 540)


def test_top_left_handle_hit_at_1x_zoom(overlay):
    from PyQt6.QtCore import QPoint, QRect
    overlay._crop_rect = QRect(100, 100, 200, 150)
    assert overlay._hit_handle(QPoint(100, 100)) == "tl"


def test_range_slider_range_and_selection(qapp):
    from src.ui.trim_controls import RangeSlider
    slider = RangeSlider()
    slider.set_range(10000)
    assert (slider._max, slider._start, slider._end) == (10000, 0, 10000)
    slider.set_selection(2000, 8000)
    assert slider.get_selection() == (2000, 8000)
    slider.deleteLater()


def test_trim_controls_duration_and_trim(qapp):
    from src.ui.trim_controls import TrimControls
    trim = TrimControls()
    trim.set_duration(60000)
    assert trim._duration_ms == 60000
    trim.set_trim(5000, 55000)
    s, e = trim.get_trim_seconds()
    assert abs(s - 5.0) < 0.01 and abs(e - 55.0) < 0.01
    trim.deleteLater()


@pytest.mark.parametrize("ms, text", [(65500, "01:05.50"), (0, "00:00.00")])
def test_trim_time_format(ms, text):
    from src.ui.trim_controls import TrimControls
    assert TrimControls._fmt(ms) == text


@pytest.mark.parametrize("text, ms", [("01:30.50", 90500), ("45.5", 45500), ("garbage", None)])
def test_trim_time_parse(text, ms):
    from src.ui.trim_controls import TrimControls
    assert TrimControls._parse(text) == ms
