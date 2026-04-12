from PyQt6.QtWidgets import QWidget, QInputDialog
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QMouseEvent, QFont, QPainterPath


class AutomationLane(QWidget):
    """Speed automation lane with linear interpolation between keyframes.

    Left click to add/drag, right click to remove, middle click to duplicate,
    double-click a keyframe to type a value.
    """

    changed = pyqtSignal()  # keyframes were modified

    _MIN_SPEED = 0.05
    _MAX_SPEED = 2.0
    _MARGIN = 8
    _HIT_RADIUS = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(80)
        self.setMaximumHeight(80)
        self._max_ms = 1000
        self._keyframes: list[tuple[int, float]] = []  # sorted by time_ms
        self._dragging_idx = -1
        self._playhead = 0
        self._base_speed = 1.0

    # ── Public API ──────────────────────────────────────────

    def set_duration(self, ms: int):
        self._max_ms = max(1, ms)
        self.update()

    def set_playhead(self, ms: int):
        self._playhead = ms
        self.update()

    def set_keyframes(self, kf: list[tuple[int, float]]):
        self._keyframes = sorted(kf, key=lambda k: k[0])
        self.update()

    def get_keyframes(self) -> list[tuple[int, float]]:
        return list(self._keyframes)

    def set_base_speed(self, speed: float):
        self._base_speed = speed
        self.update()

    def get_speed_at(self, time_ms: int) -> float:
        """Return the speed at a given time with linear interpolation."""
        if not self._keyframes:
            return self._base_speed

        # Before the first keyframe: lerp from base_speed to first keyframe
        first_t, first_s = self._keyframes[0]
        if time_ms <= first_t:
            if first_t == 0:
                return first_s
            frac = time_ms / first_t
            return self._base_speed + (first_s - self._base_speed) * frac

        # Between keyframes: linear interpolation
        for i in range(len(self._keyframes) - 1):
            t1, s1 = self._keyframes[i]
            t2, s2 = self._keyframes[i + 1]
            if t1 <= time_ms <= t2:
                span = t2 - t1
                if span == 0:
                    return s2
                frac = (time_ms - t1) / span
                return s1 + (s2 - s1) * frac

        # After the last keyframe: hold last value
        return self._keyframes[-1][1]

    def clear(self):
        self._keyframes.clear()
        self.changed.emit()
        self.update()

    # ── Coordinate helpers ──────────────────────────────────

    def _t_to_x(self, t: int) -> float:
        usable = self.width() - 2 * self._MARGIN
        return self._MARGIN + t / self._max_ms * usable if self._max_ms > 0 else self._MARGIN

    def _x_to_t(self, x: float) -> int:
        usable = self.width() - 2 * self._MARGIN
        t = int((x - self._MARGIN) / usable * self._max_ms) if usable > 0 else 0
        return max(0, min(t, self._max_ms))

    def _speed_to_y(self, speed: float) -> float:
        h = self.height() - 2 * self._MARGIN
        frac = (speed - self._MIN_SPEED) / (self._MAX_SPEED - self._MIN_SPEED)
        return self._MARGIN + (1 - frac) * h

    def _y_to_speed(self, y: float) -> float:
        h = self.height() - 2 * self._MARGIN
        frac = 1 - (y - self._MARGIN) / h if h > 0 else 0.5
        speed = self._MIN_SPEED + frac * (self._MAX_SPEED - self._MIN_SPEED)
        # Snap to 0.05 increments
        speed = round(speed * 20) / 20
        return max(self._MIN_SPEED, min(self._MAX_SPEED, speed))

    # ── Painting ────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        p.fillRect(0, 0, w, h, QColor(40, 40, 40))

        # Grid lines. Extra low-end references (0.1, 0.25) help eyeball
        # extreme slow-down values now that the floor is 0.05x.
        p.setPen(QPen(QColor(60, 60, 60), 1))
        for spd in (0.1, 0.25, 0.5, 1.0, 1.5, 2.0):
            y = self._speed_to_y(spd)
            p.drawLine(self._MARGIN, int(y), w - self._MARGIN, int(y))

        # 1x label
        p.setPen(QColor(90, 90, 90))
        p.setFont(QFont("monospace", 7))
        p.drawText(w - self._MARGIN + 2, int(self._speed_to_y(1.0)) + 4, "1x")

        # Build point list: (start) -> keyframes -> (end)
        points = []
        base_y = self._speed_to_y(self._base_speed)
        if self._keyframes:
            first_t, first_s = self._keyframes[0]
            # Line from left edge at base_speed to first keyframe
            points.append((self._MARGIN, base_y))
            for t, s in self._keyframes:
                points.append((self._t_to_x(t), self._speed_to_y(s)))
            # Hold last value to end
            points.append((w - self._MARGIN, self._speed_to_y(self._keyframes[-1][1])))
        else:
            points.append((self._MARGIN, base_y))
            points.append((w - self._MARGIN, base_y))

        # Draw the interpolation line
        p.setPen(QPen(QColor(100, 200, 255), 2))
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Keyframe dots + value labels
        p.setFont(QFont("monospace", 8))
        for t, s in self._keyframes:
            kx = self._t_to_x(t)
            ky = self._speed_to_y(s)

            p.setBrush(QColor(255, 255, 255))
            p.setPen(QPen(QColor(100, 200, 255), 2))
            p.drawEllipse(int(kx) - 5, int(ky) - 5, 10, 10)

            label = f"{s:.2f}x"
            p.setPen(QColor(220, 220, 220))
            label_y = int(ky) - 10 if ky > 30 else int(ky) + 18
            p.drawText(int(kx) - 15, label_y, label)

        # Playhead
        px = self._t_to_x(self._playhead)
        p.setPen(QPen(QColor(255, 80, 80), 2))
        p.drawLine(int(px), 0, int(px), h)

        p.end()

    # ── Mouse interaction ───────────────────────────────────

    def _hit_test(self, x: float, y: float) -> int:
        for i, (t, s) in enumerate(self._keyframes):
            kx = self._t_to_x(t)
            ky = self._speed_to_y(s)
            if abs(x - kx) < self._HIT_RADIUS and abs(y - ky) < self._HIT_RADIUS:
                return i
        return -1

    def _sort_and_find(self, t: int, s: float) -> int:
        self._keyframes.sort(key=lambda k: k[0])
        for i, (kt, ks) in enumerate(self._keyframes):
            if kt == t and ks == s:
                return i
        return 0

    def mousePressEvent(self, event: QMouseEvent):
        x, y = event.pos().x(), event.pos().y()

        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._hit_test(x, y)
            if idx >= 0:
                self._dragging_idx = idx
            else:
                t = self._x_to_t(x)
                s = self._y_to_speed(y)
                self._keyframes.append((t, s))
                self._dragging_idx = self._sort_and_find(t, s)
                self.changed.emit()
                self.update()

        elif event.button() == Qt.MouseButton.RightButton:
            idx = self._hit_test(x, y)
            if idx >= 0:
                self._keyframes.pop(idx)
                self.changed.emit()
                self.update()

        elif event.button() == Qt.MouseButton.MiddleButton:
            idx = self._hit_test(x, y)
            if idx >= 0:
                t, s = self._keyframes[idx]
                self._keyframes.append((t, s))
                self._dragging_idx = self._sort_and_find(t, s)
                self.changed.emit()
                self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = event.pos().x(), event.pos().y()
        idx = self._hit_test(x, y)
        if idx < 0:
            return
        t, old_s = self._keyframes[idx]
        val, ok = QInputDialog.getDouble(
            self, "Keyframe Speed", "Speed:",
            old_s, self._MIN_SPEED, self._MAX_SPEED, 2,
        )
        if ok:
            self._keyframes[idx] = (t, round(val, 2))
            self.changed.emit()
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging_idx < 0:
            return
        x, y = event.pos().x(), event.pos().y()
        t = self._x_to_t(x)
        s = self._y_to_speed(y)
        self._keyframes[self._dragging_idx] = (t, s)
        self._dragging_idx = self._sort_and_find(t, s)
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, event):
        self._dragging_idx = -1
