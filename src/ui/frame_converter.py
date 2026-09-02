"""Off-GUI-thread QVideoFrame -> QImage conversion.

`QVideoFrame.toImage()` is a full CPU colour-space convert, not a cheap
accessor. On 2560x1440 10-bit (Format_P010, HandleType.NoHandle — the frames
QMediaPlayer hands us for OBS replay clips) it measures **7.9-19 ms**. Running
it inside the `videoFrameChanged` slot means it runs on the GUI thread once per
delivered frame, and QMediaPlayer delivers `source_fps x playback_rate` frames
per second — 120 x 2.0 = 240/s. That is 246% of a GUI thread.

The visible symptom was audio, not video: `PitchedAudioPlayer._feed` shares that
thread on a 15 ms QTimer and was measured running **20-26 ticks instead of ~400
in six seconds** (a 3-10% duty cycle, inter-tick gaps up to 2.5 s), so the
QAudioSink ran dry for seconds at a time. Anything above ~1.35x stuttered.

This class fixes it in two ways:

1. **The conversion moves to a worker thread.** The frames are CPU-side buffers
   with no GPU handle, so `toImage()` is pure arithmetic and safe off the GUI
   thread. Qt keeps a per-thread RHI holder, so no state is shared.
2. **Delivered frames are coalesced.** Only the newest QVideoFrame is kept (a
   refcounted handle — holding one costs nothing), and at most one conversion
   runs per `interval_ms`. Frames that would never reach the screen are never
   converted. Playback at 120 fps does not need 120 conversions and 120
   repaints a second; it needs one per display refresh.

The result is emitted as-is, nulls included — QVideoSink emits null frames
between `setSource` calls and each surface has its own policy for those.
A monotonic `gen` token accompanies every request and comes back with the
result, so a surface can invalidate work that is already in flight (frame-step,
colour preview and placeholder images all assign `_image` directly and must not
be overwritten by a conversion that started before them).
"""

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QImage
from PyQt6.QtMultimedia import QVideoFrame

from src.core.log_setup import log

# Bounds on the minimum gap between conversions.
#  - 8 ms caps the worker at ~125 conversions/s. At ~10 ms per 1440p convert
#    that is about one core; going faster buys nothing a human can see and
#    starts costing the encoder/decoder threads real time.
#  - 33 ms keeps preview above 30 fps even on an oddly-reported display.
_MIN_INTERVAL_MS = 8
_MAX_INTERVAL_MS = 33
_FALLBACK_INTERVAL_MS = 16


def display_interval_ms() -> int:
    """One display refresh, clamped. Converting a frame the screen will never
    draw is pure waste, so the refresh rate is the natural ceiling."""
    screen = QGuiApplication.primaryScreen()
    hz = screen.refreshRate() if screen is not None else 0.0
    if not hz or hz <= 0:
        return _FALLBACK_INTERVAL_MS
    return max(_MIN_INTERVAL_MS, min(_MAX_INTERVAL_MS, round(1000.0 / hz)))


class _ConverterWorker(QObject):
    """Lives on the worker thread. Nothing else may touch it directly."""

    converted = pyqtSignal(QImage, int)

    def convert(self, frame: QVideoFrame, gen: int):
        try:
            img = frame.toImage()
        except Exception as e:  # pragma: no cover — toImage does not raise in practice
            log().warning(f"Frame conversion failed: {e}")
            img = QImage()
        self.converted.emit(img, gen)


class FrameConverter(QObject):
    """Converts the newest delivered video frame off the GUI thread.

    Owns a QThread for the lifetime of the surface it belongs to.
    `shutdown()` must be called before the process exits.
    """

    image_ready = pyqtSignal(QImage, int)

    # Emitted from the GUI thread, received on the worker thread (queued).
    #
    # The frame is typed `object`, NOT QVideoFrame, and that is load-bearing:
    # PyQt6 6.11 cannot marshal a QVideoFrame across a queued connection and
    # **fails silently** — no exception, no "Cannot queue arguments of type"
    # warning, the slot simply never runs. Declaring the parameter as `object`
    # passes the Python wrapper through by reference instead, which keeps the
    # implicitly-shared frame alive across the thread boundary. QImage coming
    # back is a registered metatype and queues normally.
    _request = pyqtSignal(object, int)

    def __init__(self, parent=None, interval_ms: int | None = None):
        super().__init__(parent)
        if interval_ms is None:
            interval_ms = display_interval_ms()
        self._pending: QVideoFrame | None = None
        self._pending_gen = 0
        self._busy = False        # a conversion is in flight on the worker
        self._cooling = False     # the minimum gap between conversions
        self._shutdown = False

        self._thread = QThread(self)
        self._thread.setObjectName("frame-convert")
        self._worker = _ConverterWorker()
        self._worker.moveToThread(self._thread)
        self._request.connect(self._worker.convert)
        self._worker.converted.connect(self._on_converted)
        self._thread.start()

        # Backstop: a QThread destroyed while still running aborts the process.
        # The owning surface is expected to call shutdown() explicitly, but a
        # surface that is simply dropped must not take the app down with it.
        if parent is not None:
            parent.destroyed.connect(self._on_owner_destroyed)

        # Single-shot: armed when a conversion starts, so the *first* frame
        # after an idle period converts immediately (no added latency) and
        # only a sustained stream gets throttled to the display cadence.
        self._cooldown = QTimer(self)
        self._cooldown.setSingleShot(True)
        self._cooldown.setInterval(interval_ms)
        self._cooldown.timeout.connect(self._on_cooldown)

    def submit(self, frame: QVideoFrame, gen: int):
        """Hand over the newest frame. Replaces any frame not yet started —
        an undisplayed frame is worth nothing, so dropping it is free.

        `QVideoFrame(frame)` is NOT redundant. PyQt6 wraps the `const
        QVideoFrame &` that Qt passes into the slot **without taking
        ownership**: the wrapper is only valid for the duration of the call.
        Storing the argument itself and touching it one tick later is a
        use-after-free, and it does not raise — the process dies with an
        access violation (measured: 0xC0000005 within ~500 ms of play() on
        1440p HEVC, both on the GUI thread and on a worker thread). The copy
        is cheap: QVideoFrame is implicitly shared, so this bumps a refcount
        on the decoded buffer rather than copying ~11 MB of pixels, and it is
        what keeps that buffer alive past the signal.
        """
        if self._shutdown:
            return
        self._pending = QVideoFrame(frame)
        self._pending_gen = gen
        self._maybe_start()

    def discard_pending(self):
        """Drop the queued frame. Used when the surface takes over the image
        itself; the in-flight one is handled by the caller bumping `gen`."""
        self._pending = None

    def _maybe_start(self):
        if self._busy or self._cooling or self._pending is None or self._shutdown:
            return
        frame, gen = self._pending, self._pending_gen
        self._pending = None
        self._busy = True
        self._cooling = True
        self._cooldown.start()
        self._request.emit(frame, gen)

    def _on_cooldown(self):
        self._cooling = False
        self._maybe_start()

    def _on_converted(self, img: QImage, gen: int):
        self._busy = False
        if self._shutdown:
            return
        self.image_ready.emit(img, gen)
        self._maybe_start()

    def _on_owner_destroyed(self, *_):
        self.shutdown()

    def shutdown(self):
        """Stop the worker thread. Safe to call more than once."""
        if self._shutdown:
            return
        self._shutdown = True
        self._cooldown.stop()
        self._pending = None
        self._thread.quit()
        # A conversion in flight is bounded by one frame (~20 ms worst case),
        # so this always returns well inside the timeout. Never terminate() —
        # killing a thread mid-toImage would leave the frame's buffer mapped.
        if not self._thread.wait(3000):
            log().warning("Frame converter thread did not stop within 3s")
