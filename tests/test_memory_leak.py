"""Memory leak harness — repeated load/unload of QMediaPlayer.

Catches gross leaks where Qt media resources are not released between
file loads. Marked `slow` so it isn't part of the default fast pytest run;
opt in with `pytest -m slow`.
"""
from __future__ import annotations

import gc
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

psutil = pytest.importorskip("psutil")


@pytest.mark.slow
def test_repeated_qmediaplayer_load_unload_does_not_leak(fixture_video):
    """Load a fixture video into QMediaPlayer 50 times. RSS growth bounded < 100 MB."""
    pytest.importorskip("PyQt6.QtCore")
    from PyQt6.QtCore import QCoreApplication, QUrl
    from PyQt6.QtMultimedia import QMediaPlayer

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    # Warm-up: a couple of load cycles so Qt's lazy allocations settle before
    # we start measuring.
    for _ in range(3):
        p = QMediaPlayer()
        p.setSource(QUrl.fromLocalFile(str(fixture_video)))
        app.processEvents()
        p.setSource(QUrl())
        del p
    gc.collect()
    app.processEvents()

    proc = psutil.Process()
    rss_before = proc.memory_info().rss

    for _ in range(50):
        p = QMediaPlayer()
        p.setSource(QUrl.fromLocalFile(str(fixture_video)))
        app.processEvents()
        p.setSource(QUrl())
        del p
    gc.collect()
    app.processEvents()

    rss_after = proc.memory_info().rss
    growth_mb = (rss_after - rss_before) / (1024 * 1024)
    # Threshold is generous because Qt does some legitimate caching across
    # QMediaPlayer instances (codec init, GPU resources). A real leak shows
    # up as continuously growing RSS — this just bounds it.
    assert growth_mb < 100, (
        f"RSS grew by {growth_mb:.1f} MB after 50 load/unload cycles "
        "(suspect leak in QMediaPlayer / QVideoSink lifecycle)"
    )


@pytest.mark.slow
def test_speed_curve_no_leak_under_repeated_get():
    """Pure-Python speed_curve should never leak — sanity check for psutil baseline."""
    from src.core.speed_curve import SpeedCurve

    proc = psutil.Process()
    rss_before = proc.memory_info().rss

    c = SpeedCurve([(0, 1.0), (1000, 2.0), (2000, 0.5)])
    for ms in range(100_000):
        c.get_speed_at(ms % 3000)
    gc.collect()

    rss_after = proc.memory_info().rss
    growth_mb = (rss_after - rss_before) / (1024 * 1024)
    assert growth_mb < 5, f"Pure-Python loop should not leak; grew {growth_mb:.1f} MB"
