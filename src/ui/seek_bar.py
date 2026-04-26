"""Seek bar with hover thumbnail preview, A-B loop markers,
and a styled played-range fill.

Extends ClickSlider so click-to-seek and drag behavior remain identical
to the rest of the app. Custom paintEvent overlays the played fill and
A-B triangles on top of the standard groove. The hover popup is a
borderless tooltip-style window that tracks the cursor along the bar.

Cache lives here (not in ThumbnailWorker) so paint events can read it
without crossing thread boundaries. The worker is purely an extractor.
"""

from __future__ import annotations

from collections import OrderedDict

from PyQt6.QtCore import Qt, QTimer, QPoint, QRect
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QImage, QPixmap
from PyQt6.QtWidgets import (
    QFrame, QLabel, QVBoxLayout, QStyle, QStyleOptionSlider, QApplication,
)

from src.ui.widgets import ClickSlider
from src.ui.thumbnail_worker import ThumbnailWorker


class _ThumbnailPopup(QFrame):
    """Frameless floating window showing a thumbnail above the seek bar."""

    THUMB_W = 192
    THUMB_H = 108

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(self.THUMB_W, self.THUMB_H)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setStyleSheet(
            "background: #050505; color: #888; font-size: 8pt;"
        )
        self._thumb_label.setText("…")
        layout.addWidget(self._thumb_label)

        self._time_label = QLabel()
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time_label.setStyleSheet(
            "color: white; font-family: monospace; font-size: 10pt;"
            " padding: 2px;"
        )
        layout.addWidget(self._time_label)

        self.setStyleSheet(
            "_ThumbnailPopup { background: #1a1a1a;"
            " border: 1px solid #444; border-radius: 4px; }"
        )
        self._image: QImage | None = None

    def set_image(self, img: QImage | None) -> None:
        if img is None or img.isNull():
            self._thumb_label.clear()
            self._thumb_label.setText("…")
            self._image = None
            return
        self._image = img
        scaled = img.scaled(
            self.THUMB_W, self.THUMB_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._thumb_label.setPixmap(QPixmap.fromImage(scaled))

    def clear_image(self) -> None:
        self._image = None
        self._thumb_label.clear()
        self._thumb_label.setText("…")

    def set_time_text(self, text: str) -> None:
        self._time_label.setText(text)

    def show_above(self, screen_x: int, screen_y: int) -> None:
        """Show the popup centered horizontally on screen_x, just above
        screen_y. Clamps to the active screen."""
        self.adjustSize()
        w, h = self.width(), self.height()
        x = screen_x - w // 2
        y = screen_y - h - 10

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = max(geo.left() + 2, min(geo.right() - w - 2, x))
            # If above would clip, fall back to below
            if y < geo.top() + 2:
                y = screen_y + 14
        self.move(x, y)
        if not self.isVisible():
            self.show()


class SeekBar(ClickSlider):
    """Seek slider for the video player.

    Slider value range is fixed at 0..1000 (ratio×1000); the consumer
    provides duration_ms via set_duration() so we can convert mouse x
    to timestamps for the hover preview.
    """

    HOVER_DEBOUNCE_MS = 50
    CACHE_BUCKET_S = 1  # snap thumbnails to 1-second buckets
    MAX_THUMBS_PER_VIDEO = 250
    MAX_VIDEOS_CACHED = 5

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(20)

        self._duration_ms = 0
        self._current_path = ""
        self._a_ms: int | None = None
        self._b_ms: int | None = None
        self._hover_ms = -1
        self._mouse_inside = False

        self._thumb_worker: ThumbnailWorker | None = None
        # path -> OrderedDict[bucket_seconds, QImage]
        self._cache: dict[str, OrderedDict[int, QImage]] = {}

        self._popup = _ThumbnailPopup(self)

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(self.HOVER_DEBOUNCE_MS)
        self._hover_timer.timeout.connect(self._do_hover_request)

    # ── Public API ────────────────────────────────────────────

    def set_duration(self, duration_ms: int) -> None:
        self._duration_ms = max(0, int(duration_ms))

    def set_current_path(self, path: str) -> None:
        if path == self._current_path:
            return
        if self._thumb_worker:
            self._thumb_worker.cancel_for(self._current_path)
        self._current_path = path
        self._hover_ms = -1
        self._popup.hide()

    def set_thumbnail_worker(self, worker: ThumbnailWorker | None) -> None:
        if self._thumb_worker is worker:
            return
        if self._thumb_worker is not None:
            try:
                self._thumb_worker.ready.disconnect(self._on_thumb_ready)
            except (TypeError, RuntimeError):
                pass
        self._thumb_worker = worker
        if worker is not None:
            worker.ready.connect(self._on_thumb_ready)

    def set_ab_markers(self, a_ms: int | None, b_ms: int | None) -> None:
        if a_ms == self._a_ms and b_ms == self._b_ms:
            return
        self._a_ms = a_ms
        self._b_ms = b_ms
        self.update()

    def clear_cache(self, path: str | None = None) -> None:
        if path is None:
            self._cache.clear()
        else:
            self._cache.pop(path, None)

    # ── Hover handling ───────────────────────────────────────

    def enterEvent(self, event):
        self._mouse_inside = True
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._mouse_inside = False
        self._hover_ms = -1
        self._hover_timer.stop()
        self._popup.hide()
        super().leaveEvent(event)

    def hideEvent(self, event):
        # The popup is a top-level window — Qt won't hide it when our
        # parent stack switches to the editor. Do it explicitly.
        self._mouse_inside = False
        self._hover_ms = -1
        self._hover_timer.stop()
        self._popup.hide()
        super().hideEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self._duration_ms <= 0 or not self._current_path:
            return
        x = int(event.position().x())
        w = max(1, self.width())
        ratio = max(0.0, min(1.0, x / w))
        ms = int(ratio * self._duration_ms)
        self._hover_ms = ms
        self._update_popup_position(x, ms)
        self._hover_timer.start()

    def _update_popup_position(self, widget_x: int, ms: int) -> None:
        self._popup.set_time_text(self._fmt(ms))
        # Try cached thumbnail synchronously
        bucket = ms // 1000
        cache = self._cache.get(self._current_path)
        if cache is not None and bucket in cache:
            cache.move_to_end(bucket)
            self._popup.set_image(cache[bucket])
        # Move popup to track cursor
        gp = self.mapToGlobal(QPoint(widget_x, 0))
        self._popup.show_above(gp.x(), gp.y())

    def _do_hover_request(self) -> None:
        if not self._mouse_inside or self._hover_ms < 0:
            return
        if not self._current_path or self._duration_ms <= 0:
            return
        bucket = self._hover_ms // 1000
        cache = self._cache.get(self._current_path)
        if cache is not None and bucket in cache:
            return  # already cached; popup already showing it
        if self._thumb_worker is not None:
            self._thumb_worker.request(self._current_path, self._hover_ms)

    def _on_thumb_ready(self, path: str, ms: int, img: QImage) -> None:
        if not isinstance(img, QImage) or img.isNull():
            return
        bucket = ms // 1000
        cache = self._cache.setdefault(path, OrderedDict())
        cache[bucket] = img
        cache.move_to_end(bucket)
        # Per-video LRU
        while len(cache) > self.MAX_THUMBS_PER_VIDEO:
            cache.popitem(last=False)
        # Global LRU on videos
        while len(self._cache) > self.MAX_VIDEOS_CACHED:
            oldest_key = next(iter(self._cache))
            if oldest_key == self._current_path:
                # Don't evict current video; rotate
                self._cache.move_to_end(oldest_key)
                break
            del self._cache[oldest_key]
        # If this matches the current hover bucket, update popup
        if (self._mouse_inside and path == self._current_path
                and self._hover_ms >= 0
                and (self._hover_ms // 1000) == bucket):
            self._popup.set_image(img)

    # ── Painting ─────────────────────────────────────────────

    def paintEvent(self, event):
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)

        groove_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt,
            QStyle.SubControl.SC_SliderGroove, self,
        )
        # Thicken the groove for a more "video-player" feel: 6px tall,
        # vertically centered in the widget.
        bar_h = 6
        bar_y = self.height() // 2 - bar_h // 2
        bar_rect = QRect(groove_rect.left(), bar_y,
                         groove_rect.width(), bar_h)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        # Track background
        painter.setBrush(QColor(50, 50, 50))
        painter.drawRoundedRect(bar_rect, 3, 3)

        # Played-range fill
        rng = self.maximum() - self.minimum()
        if rng > 0:
            ratio = (self.value() - self.minimum()) / rng
            played_w = int(bar_rect.width() * max(0.0, min(1.0, ratio)))
            if played_w > 0:
                played_rect = QRect(bar_rect.left(), bar_rect.top(),
                                    played_w, bar_rect.height())
                painter.setBrush(QColor(96, 170, 230))
                painter.drawRoundedRect(played_rect, 3, 3)

        # Hover indicator: a faint line at the hovered position
        if self._mouse_inside and self._hover_ms >= 0 and self._duration_ms > 0:
            hover_ratio = self._hover_ms / self._duration_ms
            hx = bar_rect.left() + int(bar_rect.width() * hover_ratio)
            painter.setBrush(QColor(255, 255, 255, 100))
            painter.drawRect(QRect(hx - 1, bar_rect.top() - 2,
                                   2, bar_rect.height() + 4))

        # A-B markers
        if self._duration_ms > 0:
            for ms, color in (
                (self._a_ms, QColor(80, 220, 100)),
                (self._b_ms, QColor(230, 90, 90)),
            ):
                if ms is None or ms < 0:
                    continue
                pos_ratio = max(0.0, min(1.0, ms / self._duration_ms))
                x = bar_rect.left() + int(bar_rect.width() * pos_ratio)
                self._draw_marker(painter, x, bar_rect, color)

        # Handle: a small white circle. Drawn last so it sits on top.
        if rng > 0:
            ratio = (self.value() - self.minimum()) / rng
            hx = bar_rect.left() + int(bar_rect.width() * ratio)
            handle_r = 6
            cy = bar_rect.center().y()
            painter.setBrush(QColor(240, 240, 240))
            painter.drawEllipse(QPoint(hx, cy), handle_r, handle_r)
            painter.setBrush(QColor(96, 170, 230))
            painter.drawEllipse(QPoint(hx, cy), handle_r - 2, handle_r - 2)

        painter.end()

    @staticmethod
    def _draw_marker(painter, x: int, bar_rect: QRect, color: QColor) -> None:
        size = 4
        y_top = bar_rect.top() - 1
        y_bot = bar_rect.bottom() + 1
        # Small triangles above and below the bar pointing inward.
        path = QPainterPath()
        path.moveTo(x, y_top)
        path.lineTo(x - size, y_top - size)
        path.lineTo(x + size, y_top - size)
        path.closeSubpath()
        path.moveTo(x, y_bot)
        path.lineTo(x - size, y_bot + size)
        path.lineTo(x + size, y_bot + size)
        path.closeSubpath()
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

    @staticmethod
    def _fmt(ms: int) -> str:
        s = max(0, ms) / 1000
        m = int(s // 60)
        s -= m * 60
        return f"{m:02d}:{s:05.2f}"
