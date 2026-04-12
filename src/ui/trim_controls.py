from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QGroupBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QMouseEvent


class RangeSlider(QWidget):
    """Dual-handle range slider for selecting a sub-range of 0..max."""

    range_changed = pyqtSignal(int, int)  # start_ms, end_ms
    playhead_changed = pyqtSignal(int)   # ms — user clicked/dragged to seek
    focus_taken = pyqtSignal()  # this widget grabbed focus

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(40)
        self.setMinimumWidth(200)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._min = 0
        self._max = 1000
        self._start = 0
        self._end = 1000
        self._dragging = None  # "start", "end", or "playhead"
        self._selected = None  # "start", "end", or "playhead" — for arrow keys
        self._drag_offset = 0
        self._playhead = 0

    def set_range(self, max_val: int):
        self._max = max(1, max_val)
        self._start = 0
        self._end = self._max
        self.update()

    def set_selection(self, start: int, end: int):
        self._start = max(self._min, min(start, self._max))
        self._end = max(self._start, min(end, self._max))
        self.update()

    def set_playhead(self, pos: int):
        self._playhead = max(self._start, min(pos, self._end))
        self.update()

    def get_selection(self) -> tuple[int, int]:
        return self._start, self._end

    # ── Coordinate helpers ──────────────────────────────────────

    def _val_to_x(self, val: int) -> int:
        margin = 8
        usable = self.width() - 2 * margin
        return margin + int(val / self._max * usable) if self._max > 0 else margin

    def _x_to_val(self, x: int) -> int:
        margin = 8
        usable = self.width() - 2 * margin
        val = int((x - margin) / usable * self._max) if usable > 0 else 0
        return max(self._min, min(val, self._max))

    # ── Painting ────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        h = self.height()
        track_y = h // 2 - 4
        track_h = 8

        # Background track
        p.fillRect(0, track_y, self.width(), track_h, QColor(60, 60, 60))

        # Selected range
        x1 = self._val_to_x(self._start)
        x2 = self._val_to_x(self._end)
        p.fillRect(x1, track_y, x2 - x1, track_h, QColor(80, 140, 220))

        # Playhead
        px = self._val_to_x(self._playhead)
        playhead_selected = self._selected == "playhead"
        p.setPen(QPen(QColor(120, 255, 120) if playhead_selected else QColor(255, 80, 80), 2))
        p.drawLine(px, track_y - 4, px, track_y + track_h + 4)

        # Handles
        for label, x in (("start", x1), ("end", x2)):
            sel = (self._selected == label)
            p.setBrush(QColor(120, 255, 120) if sel else QColor(255, 255, 255))
            p.setPen(QPen(QColor(80, 200, 80) if sel else QColor(100, 100, 100), 1))
            p.drawRoundedRect(x - 5, track_y - 6, 10, track_h + 12, 3, 3)

        p.end()

    # ── Mouse interaction ───────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        self.setFocus()
        self.focus_taken.emit()
        x = event.pos().x()

        if event.button() == Qt.MouseButton.LeftButton:
            # Left click → seek playhead + select it (clamped to trim zone)
            self._dragging = "playhead"
            self._selected = "playhead"
            val = max(self._start, min(self._x_to_val(x), self._end))
            self._playhead = val
            self.playhead_changed.emit(val)
            self.update()

        elif event.button() == Qt.MouseButton.RightButton:
            # Right click → move whichever trim handle is closer + select it
            x1 = self._val_to_x(self._start)
            x2 = self._val_to_x(self._end)
            if abs(x - x1) <= abs(x - x2):
                self._dragging = "start"
                self._selected = "start"
                val = self._x_to_val(x)
                self._start = min(val, self._end - 1)
            else:
                self._dragging = "end"
                self._selected = "end"
                val = self._x_to_val(x)
                self._end = max(val, self._start + 1)
            self.range_changed.emit(self._start, self._end)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self._dragging:
            return
        x = event.pos().x()

        if self._dragging == "start":
            val = self._x_to_val(x)
            self._start = min(val, self._end - 1)
            self.range_changed.emit(self._start, self._end)
        elif self._dragging == "end":
            val = self._x_to_val(x)
            self._end = max(val, self._start + 1)
            self.range_changed.emit(self._start, self._end)
        elif self._dragging == "playhead":
            val = max(self._start, min(self._x_to_val(x), self._end))
            self._playhead = val
            self.playhead_changed.emit(val)

        self.update()

    def mouseReleaseEvent(self, event):
        self._dragging = None

    def keyPressEvent(self, event):
        if self._selected is None:
            super().keyPressEvent(event)
            return
        # Step: ~1% of range per press, minimum 10ms
        step = max(10, self._max // 100)
        key = event.key()
        if key == Qt.Key.Key_Left:
            self._nudge_selected(-step)
        elif key == Qt.Key.Key_Right:
            self._nudge_selected(step)
        else:
            super().keyPressEvent(event)

    def _nudge_selected(self, delta: int):
        if self._selected == "start":
            self._start = max(self._min, min(self._start + delta, self._end - 1))
            self.range_changed.emit(self._start, self._end)
        elif self._selected == "end":
            self._end = max(self._start + 1, min(self._end + delta, self._max))
            self.range_changed.emit(self._start, self._end)
        elif self._selected == "playhead":
            self._playhead = max(self._start, min(self._playhead + delta, self._end))
            self.playhead_changed.emit(self._playhead)
        self.update()

    def deselect(self):
        self._selected = None
        self.update()


class TrimControls(QWidget):
    """Trim panel: range slider + start/end time text fields."""

    trim_changed = pyqtSignal(float, float)  # start_s, end_s
    seek_requested = pyqtSignal(int)  # ms — user clicked timeline to seek

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("Trim")
        gl = QVBoxLayout(group)

        self.slider = RangeSlider()
        self.slider.range_changed.connect(self._on_slider_changed)
        self.slider.playhead_changed.connect(self.seek_requested.emit)
        gl.addWidget(self.slider)

        row = QHBoxLayout()
        row.addWidget(QLabel("Start:"))
        self.txt_start = QLineEdit("00:00.00")
        self.txt_start.setFixedWidth(90)
        self.txt_start.editingFinished.connect(self._on_text_changed)
        row.addWidget(self.txt_start)

        row.addStretch()
        self.lbl_duration = QLabel("Duration: 00:00.00")
        self.lbl_duration.setStyleSheet("font-family: monospace;")
        row.addWidget(self.lbl_duration)
        row.addStretch()

        row.addWidget(QLabel("End:"))
        self.txt_end = QLineEdit("00:00.00")
        self.txt_end.setFixedWidth(90)
        self.txt_end.editingFinished.connect(self._on_text_changed)
        row.addWidget(self.txt_end)

        gl.addLayout(row)
        layout.addWidget(group)

    def set_duration(self, ms: int):
        self._duration_ms = ms
        self.slider.set_range(ms)
        self.txt_end.setText(self._fmt(ms))
        self._update_duration_label()

    def set_playhead(self, ms: int):
        self.slider.set_playhead(ms)

    def get_trim_seconds(self) -> tuple[float, float]:
        s, e = self.slider.get_selection()
        return s / 1000.0, e / 1000.0

    def set_trim(self, start_ms: int, end_ms: int):
        """Set the trim selection programmatically."""
        self.slider.set_selection(start_ms, end_ms)
        self.txt_start.setText(self._fmt(start_ms))
        self.txt_end.setText(self._fmt(end_ms))
        self._update_duration_label()

    def _on_slider_changed(self, start_ms: int, end_ms: int):
        self.txt_start.setText(self._fmt(start_ms))
        self.txt_end.setText(self._fmt(end_ms))
        self._update_duration_label()
        self.trim_changed.emit(start_ms / 1000.0, end_ms / 1000.0)

    def _on_text_changed(self):
        start_ms = self._parse(self.txt_start.text())
        end_ms = self._parse(self.txt_end.text())
        if start_ms is not None and end_ms is not None and start_ms < end_ms:
            self.slider.set_selection(start_ms, end_ms)
            self._update_duration_label()
            self.trim_changed.emit(start_ms / 1000.0, end_ms / 1000.0)

    def _update_duration_label(self):
        s, e = self.slider.get_selection()
        self.lbl_duration.setText(f"Duration: {self._fmt(e - s)}")

    @staticmethod
    def _fmt(ms: int) -> str:
        s = ms / 1000
        m = int(s // 60)
        s = s - m * 60
        return f"{m:02d}:{s:05.2f}"

    @staticmethod
    def _parse(text: str) -> int | None:
        try:
            parts = text.strip().split(":")
            if len(parts) == 2:
                m, s = int(parts[0]), float(parts[1])
                return int((m * 60 + s) * 1000)
            elif len(parts) == 1:
                return int(float(parts[0]) * 1000)
        except ValueError:
            pass
        return None
