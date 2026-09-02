"""Tests for the off-GUI-thread frame conversion.

`QVideoFrame.toImage()` costs 8-19 ms on 1440p 10-bit footage. Running it in
the `videoFrameChanged` slot put `source_fps x playback_rate` of that on the GUI
thread (246% at 2.0x on 120 fps source), which starved the 15 ms pitched-audio
feed timer down to a 3-10% duty cycle — the "stutters above 1.35x" bug.

These lock the three properties that make the fix correct:
  1. the conversion runs on a worker thread, not the GUI thread;
  2. frames that arrive faster than the display cadence are dropped, not queued;
  3. a result can never overwrite an image the surface set deliberately
     (frame-step, colour preview, placeholder, clip unload).
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QObject, QSize, QThread  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402
from PyQt6.QtMultimedia import QVideoFrame, QVideoFrameFormat  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _frame(value: int = 200, w: int = 64, h: int = 48) -> QVideoFrame:
    """A real, mapped QVideoFrame filled with a solid colour."""
    f = QVideoFrame(QVideoFrameFormat(
        QSize(w, h), QVideoFrameFormat.PixelFormat.Format_RGBX8888))
    assert f.map(QVideoFrame.MapMode.WriteOnly)
    bits = f.bits(0)
    bits.setsize(f.mappedBytes(0))
    bits[:] = bytes([value, value, value, 255]) * (f.bytesPerLine(0) // 4 * h)
    f.unmap()
    return f


def _spin(qapp, predicate, timeout_s: float = 5.0) -> bool:
    """Run the event loop until predicate() is true or the timeout expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.002)
    return predicate()


# ── The conversion itself ────────────────────────────────────────────────


def test_converter_emits_the_converted_image(qapp):
    from src.ui.frame_converter import FrameConverter

    conv = FrameConverter(interval_ms=1)
    try:
        got: list[tuple[QImage, int]] = []
        conv.image_ready.connect(lambda img, gen: got.append((img, gen)))
        conv.submit(_frame(200), 7)
        assert _spin(qapp, lambda: got), "no image was ever emitted"
        img, gen = got[0]
        assert gen == 7
        assert (img.width(), img.height()) == (64, 48)
        assert img.pixel(3, 3) == 0xFF_C8_C8_C8   # the solid 200,200,200 fill
    finally:
        conv.shutdown()


class _ThreadProbe(QObject):
    """Receiver living on the converter's own thread, so Qt delivers the
    worker's signal to it directly — on the thread that emitted it."""

    def __init__(self):
        super().__init__()
        self.idents: list[int] = []

    def note(self, img, gen):
        self.idents.append(threading.get_ident())


def test_conversion_does_not_run_on_the_gui_thread(qapp):
    """The whole point of the fix."""
    from src.ui.frame_converter import FrameConverter

    conv = FrameConverter(interval_ms=1)
    try:
        probe = _ThreadProbe()
        probe.moveToThread(conv._thread)
        conv._worker.converted.connect(probe.note)
        assert conv._worker.thread() is conv._thread
        assert conv._thread is not QThread.currentThread()

        conv.submit(_frame(), 1)
        assert _spin(qapp, lambda: probe.idents), "no conversion happened"
        assert probe.idents[0] != threading.get_ident(), "toImage ran on the GUI thread"
    finally:
        conv.shutdown()


def test_full_resolution_is_preserved(qapp):
    """Crop mapping, `video_display_rect` and player-mode screenshots all read
    `_image.width()/height()`, so the worker must not downscale."""
    from src.ui.frame_converter import FrameConverter

    conv = FrameConverter(interval_ms=1)
    try:
        got = []
        conv.image_ready.connect(lambda img, gen: got.append(img))
        conv.submit(_frame(w=256, h=144), 1)
        assert _spin(qapp, lambda: got)
        assert (got[0].width(), got[0].height()) == (256, 144)
    finally:
        conv.shutdown()


# ── Coalescing ───────────────────────────────────────────────────────────


def test_undisplayed_frames_are_dropped_not_queued(qapp):
    """Three frames submitted back-to-back without returning to the event loop:
    the first starts converting immediately, the second is superseded by the
    third before either can start. A queue would convert all three and put the
    cost straight back onto the machine."""
    from src.ui.frame_converter import FrameConverter

    conv = FrameConverter(interval_ms=1)
    try:
        gens: list[int] = []
        conv.image_ready.connect(lambda img, gen: gens.append(gen))
        for gen in (1, 2, 3):
            conv.submit(_frame(), gen)
        assert _spin(qapp, lambda: len(gens) >= 2)
        qapp.processEvents()
        assert gens == [1, 3], gens
    finally:
        conv.shutdown()


def test_submit_takes_an_owning_copy_of_the_frame(qapp):
    """PyQt6 wraps the `const QVideoFrame &` a signal delivers WITHOUT taking
    ownership — it is only valid inside the slot. Keeping that wrapper and
    converting it a tick later kills the process with an access violation
    (measured on real 1440p HEVC playback), so submit() must copy."""
    from src.ui.frame_converter import FrameConverter

    conv = FrameConverter(interval_ms=10_000)   # never starts; stays pending
    try:
        f = _frame()
        conv.submit(f, 1)
        conv.submit(f, 2)                        # first is still cooling
        assert conv._pending is not None
        assert conv._pending is not f, "submit stored the borrowed wrapper"
    finally:
        conv.shutdown()


@pytest.mark.parametrize("hz,expected", [
    (60.0, 17),      # a plain 60 Hz panel
    (144.0, 8),      # high refresh — floored, not 7
    (359.98, 8),     # 360 Hz — floored
    (30.0, 33),      # very low — capped, still 30 fps preview
    (0.0, 16),       # refresh rate unknown
])
def test_display_interval_is_the_refresh_rate_within_bounds(qapp, monkeypatch, hz, expected):
    """Never convert faster than the screen redraws, but stay inside bounds
    that keep the worker under ~1 core and preview above 30 fps."""
    import src.ui.frame_converter as fc

    monkeypatch.setattr(fc.QGuiApplication, "primaryScreen",
                        staticmethod(lambda: type("S", (), {"refreshRate": lambda self: hz})()))
    assert fc.display_interval_ms() == expected


def test_display_interval_falls_back_without_a_screen(qapp, monkeypatch):
    import src.ui.frame_converter as fc

    monkeypatch.setattr(fc.QGuiApplication, "primaryScreen", staticmethod(lambda: None))
    assert fc.display_interval_ms() == 16


def test_owner_destroyed_reaches_a_live_child(qapp):
    """FrameConverter stops its thread from its owner's `destroyed` signal.
    That only works because the owner emits `destroyed` while its non-widget
    children are still alive — QWidget also deletes children inside its own
    destructor, so the ordering is what makes the backstop reachable at all.
    If a Qt/PyQt upgrade reverses it, fail here rather than aborting the
    process with 'QThread: Destroyed while thread is still running'."""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QWidget

    order = []

    class _Child(QObject):
        def __init__(self, parent):
            super().__init__(parent)
            self.destroyed.connect(lambda *_: order.append("child"))

    w = QWidget()
    _Child(w)
    w.destroyed.connect(lambda *_: order.append("parent"))
    w.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()

    assert order[:1] == ["parent"], (
        f"Qt now destroys children first (order={order}); FrameConverter's "
        "destroyed-backstop can no longer stop its thread in time")


def test_dropping_the_owner_stops_the_thread(qapp):
    """The backstop end to end. Before it existed, a surface dropped without
    shutdown() aborted the whole process — it happened in this test suite."""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QWidget
    from src.ui.frame_converter import FrameConverter

    w = QWidget()
    conv = FrameConverter(w)
    assert conv._thread.isRunning()

    w.deleteLater()
    del w
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()

    # `_shutdown` is a plain Python attribute, so it outlives the C++ object.
    assert conv._shutdown, "the owner was destroyed with the thread still running"


def test_submits_after_shutdown_are_ignored(qapp):
    from src.ui.frame_converter import FrameConverter

    conv = FrameConverter(interval_ms=1)
    conv.shutdown()
    conv.shutdown()   # idempotent
    got = []
    conv.image_ready.connect(lambda img, gen: got.append(gen))
    conv.submit(_frame(), 1)
    _spin(qapp, lambda: False, timeout_s=0.2)
    assert got == []


# ── Invalidation: a result must never clobber a deliberate image ─────────


@pytest.fixture
def surface(qapp):
    from src.ui.video_player import VideoSurface
    s = VideoSurface()
    yield s
    s.shutdown()
    s.deleteLater()


def test_stepping_blocks_an_in_flight_conversion(surface, qapp):
    """main_window sets `_stepping = True` and then assigns the exact
    ffmpeg-decoded frame. A conversion already running must not land on top."""
    surface._on_frame(_frame(200))
    stepped = QImage(32, 32, QImage.Format.Format_RGB32)
    stepped.fill(0xFF_00_FF_00)
    surface._stepping = True
    surface._image = stepped

    _spin(qapp, lambda: False, timeout_s=0.4)   # let any conversion finish
    assert surface._image is stepped, "the stepped frame was overwritten"


def test_clear_image_blocks_an_in_flight_conversion(surface, qapp):
    """VideoPlayer.release() drops the frame when the queue is emptied; a
    conversion from the clip just unloaded must not repaint it."""
    surface._on_frame(_frame(200))
    surface.clear_image()

    _spin(qapp, lambda: False, timeout_s=0.4)
    assert surface._image is None, "an unloaded clip's frame came back"


def test_live_frames_render_again_after_stepping_clears(surface, qapp):
    surface._stepping = True
    surface._on_frame(_frame(200))
    _spin(qapp, lambda: False, timeout_s=0.2)
    assert surface._image is None, "a frame rendered while stepping"

    surface._stepping = False
    surface._on_frame(_frame(120))
    assert _spin(qapp, lambda: surface._image is not None), "live frames never resumed"
    assert surface._image.pixel(3, 3) == 0xFF_78_78_78


def test_zoom_changed_fires_once_on_the_first_frame(surface, qapp):
    """CropOverlay geometry is rebuilt off this signal — it must still fire
    exactly when the first image lands, not on every subsequent frame."""
    fired = []
    surface.zoom_changed.connect(lambda: fired.append(1))
    surface._on_frame(_frame(200))
    assert _spin(qapp, lambda: surface._image is not None)
    assert len(fired) == 1

    surface._on_frame(_frame(120))
    assert _spin(qapp, lambda: surface._image.pixel(3, 3) == 0xFF_78_78_78)
    assert len(fired) == 1, "zoom_changed fired again on a later frame"


def test_a_null_frame_does_not_burn_the_zoom_changed_one_shot(surface, qapp):
    """release() drops the source, QVideoSink answers with a null frame, and
    the next clip loads. If the null is stored, `first` is already False when
    the real frame lands and CropOverlay never gets its one rebuild — so the
    crop box stays mapped to the bare widget rect, black bars included."""
    fired = []
    surface.zoom_changed.connect(lambda: fired.append(1))

    surface._on_image_ready(QImage(), surface._frame_gen)
    assert surface._image is None, "a null frame was stored"
    assert fired == [], "a null frame emitted zoom_changed"

    surface._on_frame(_frame(200))
    assert _spin(qapp, lambda: surface._image is not None)
    assert len(fired) == 1, "the real first frame did not emit zoom_changed"


def test_player_surface_also_drops_null_frames(qapp):
    """The sibling guard, so the pair cannot drift apart again."""
    from src.ui.player_mode import _PlayerSurface

    s = _PlayerSurface()
    try:
        placeholder = QImage(32, 32, QImage.Format.Format_RGB32)
        placeholder.fill(0xFF_00_00_FF)
        s.set_placeholder_image(placeholder)
        s._on_image_ready(QImage(), s._frame_gen)
        assert s._image is placeholder, "a null frame erased the placeholder"
    finally:
        s.shutdown()
        s.deleteLater()


def test_player_surface_placeholder_survives_an_in_flight_conversion(qapp):
    """The next-file preload paints a first-frame placeholder; a conversion
    from the file being left behind must not erase it."""
    from src.ui.player_mode import _PlayerSurface

    s = _PlayerSurface()
    try:
        s._on_frame(_frame(200))
        placeholder = QImage(32, 32, QImage.Format.Format_RGB32)
        placeholder.fill(0xFF_00_00_FF)
        s.set_placeholder_image(placeholder)

        _spin(qapp, lambda: False, timeout_s=0.4)
        assert s._image is placeholder, "the placeholder was overwritten"
    finally:
        s.shutdown()
        s.deleteLater()
