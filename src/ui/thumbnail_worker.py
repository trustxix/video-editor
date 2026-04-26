"""Background thumbnail extractor for seek-bar hover preview.

Single-pending-slot design: when a new request arrives, it replaces any
pending request. The worker only ever processes the most recent request,
so rapid mouse motion doesn't queue up dozens of extractions.

Cache lives in the consumer (SeekBar), not here — this thread is purely
an extractor. That keeps the worker stateless and avoids cross-thread
cache locking.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from src.core.ffmpeg_runner import extract_thumbnail, extract_frame


class ThumbnailWorker(QThread):
    """Worker thread that extracts a single thumbnail per `request()` call.

    Newer requests replace older pending ones. The thread sleeps on a
    condition variable when idle, so it costs nothing when the user
    isn't hovering the seek bar.

    Signals:
        ready(path, ms, QImage): emitted on the worker thread; Qt's
            queued connection delivers it back on the main thread.
    """

    ready = pyqtSignal(str, int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._pending: tuple[str, int] | None = None
        self._running = True

    def request(self, path: str, ms: int) -> None:
        """Queue (or replace) a thumbnail request. Returns immediately."""
        with self._cond:
            self._pending = (path, ms)
            self._cond.notify()

    def cancel_for(self, path: str) -> None:
        """Drop any pending request for `path` (e.g. when video changes)."""
        with self._cond:
            if self._pending and self._pending[0] == path:
                self._pending = None

    def run(self) -> None:
        while True:
            with self._cond:
                while self._running and self._pending is None:
                    self._cond.wait()
                if not self._running:
                    return
                req = self._pending
                self._pending = None

            path, ms = req
            try:
                data = extract_thumbnail(path, max(0.0, ms / 1000.0))
            except Exception:
                data = None
            if not data:
                continue
            img = QImage()
            if img.loadFromData(data, b"JPEG"):
                self.ready.emit(path, ms, img)

    def stop(self) -> None:
        """Signal the worker to exit and wait up to 2s for it to finish."""
        with self._cond:
            self._running = False
            self._cond.notify_all()
        self.wait(2000)


class FirstFramePreloader(QThread):
    """Pre-extracts a full-resolution first frame for the next playlist
    file, so playlist navigation can paint a placeholder instantly
    instead of black-flashing while QMediaPlayer loads.

    Single-pending-slot like ThumbnailWorker. New requests replace the
    pending one. Result is delivered via the `ready` signal.
    """

    ready = pyqtSignal(str, object)  # path, QImage

    # Tiny offset avoids the all-black first frame some encoders produce
    SEEK_OFFSET_S = 0.04

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._pending: str | None = None
        self._running = True

    def request(self, path: str) -> None:
        with self._cond:
            self._pending = path
            self._cond.notify()

    def cancel(self) -> None:
        with self._cond:
            self._pending = None

    def run(self) -> None:
        while True:
            with self._cond:
                while self._running and self._pending is None:
                    self._cond.wait()
                if not self._running:
                    return
                path = self._pending
                self._pending = None

            try:
                data = extract_frame(path, self.SEEK_OFFSET_S)
            except Exception:
                data = None
            if not data:
                continue
            img = QImage()
            if img.loadFromData(data):
                self.ready.emit(path, img)

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._cond.notify_all()
        self.wait(2000)
