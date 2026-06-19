from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, QEvent, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath


HANDLE_SIZE = 10


class CropOverlay(QWidget):
    """Transparent overlay for a draggable, resizable crop rectangle.
    Install as a child of the video surface widget."""

    crop_changed = pyqtSignal(int, int, int, int)  # x, y, w, h in video coords
    stretch_changed = pyqtSignal(float, float)      # stretch_h, stretch_v
    gesture_started = pyqtSignal()   # a drag began (for undo coalescing)
    gesture_finished = pyqtSignal()  # the drag ended

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self._video_w = 1920
        self._video_h = 1080
        self._aspect_ratio: tuple[int, int] | None = None

        # Crop in video coordinates (source of truth, survives resize)
        self._crop_vx = 0
        self._crop_vy = 0
        self._crop_vw = self._video_w
        self._crop_vh = self._video_h

        # Crop in logical widget coordinates (for painting and interaction)
        self._crop_rect = QRect(0, 0, self.width(), self.height())

        self._dragging = False
        self._resizing = False
        self._resize_edge = None
        self._drag_offset = QPoint()
        self._stretching = False
        self._stretch_start = QPoint()
        self._stretch_start_h = 1.0
        self._stretch_start_v = 1.0
        self._stretch_start_vr_w = 1
        self._stretch_start_vr_h = 1
        self._panning = False
        self._pan_is_view = False
        self._pan_start = QPoint()
        self._pan_start_x = 0.0
        self._pan_start_y = 0.0
        self._locked = False
        self._pending_restore = None

        if parent:
            parent.installEventFilter(self)
            self.resize(parent.size())
            if hasattr(parent, 'zoom_changed'):
                parent.zoom_changed.connect(self._on_view_changed)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            self.resize(obj.size())
        return False

    # ── View transform helpers ────────────────────────────────

    def _to_logical(self, pos) -> QPoint:
        """Convert widget-space position to logical (pre-view-transform) coords."""
        parent = self.parent()
        vz = getattr(parent, '_view_zoom', 1.0)
        vpx = getattr(parent, '_view_pan_x', 0.0)
        vpy = getattr(parent, '_view_pan_y', 0.0)
        return QPoint(int((pos.x() - vpx) / vz), int((pos.y() - vpy) / vz))

    # ── Video geometry ───────────────────────────────────────

    def _video_rect(self) -> QRect:
        """Unstretched video rect in logical coords for crop mapping."""
        parent = self.parent()
        if parent and hasattr(parent, 'video_crop_rect'):
            return parent.video_crop_rect()
        if self._video_w <= 0 or self._video_h <= 0 or self.width() <= 0 or self.height() <= 0:
            return QRect(0, 0, self.width(), self.height())
        scale = min(self.width() / self._video_w, self.height() / self._video_h)
        w = int(self._video_w * scale)
        h = int(self._video_h * scale)
        x = (self.width() - w) // 2
        y = (self.height() - h) // 2
        return QRect(x, y, w, h)

    def set_video_size(self, w: int, h: int):
        self._video_w = w
        self._video_h = h
        self.reset()

    def set_locked(self, locked: bool):
        self._locked = locked

    def set_aspect_ratio(self, ratio: tuple[int, int] | None):
        self._aspect_ratio = ratio

    def _on_view_changed(self):
        """Video panned/zoomed — rebuild crop rect, sync if not restoring."""
        if self._pending_restore:
            x, y, w, h = self._pending_restore
            self._crop_vx, self._crop_vy = x, y
            self._crop_vw, self._crop_vh = w, h
            self._crop_rect = self._from_video_coords(x, y, w, h)
            self._pending_restore = None
        else:
            # Content pan: crop stays fixed on screen, recompute video coords
            self._sync_crop_coords()
        self.update()

    def reset(self):
        self._crop_vx = 0
        self._crop_vy = 0
        self._crop_vw = self._video_w
        self._crop_vh = self._video_h
        self._crop_rect = self._video_rect()
        self.crop_changed.emit(0, 0, self._video_w, self._video_h)
        self.update()

    def set_crop_from_video(self, x: int, y: int, w: int, h: int, pending: bool = False):
        """Set the crop rect from video-coordinate values.
        If pending=True, re-syncs on next view change (for initial video load)."""
        self._crop_vx = x
        self._crop_vy = y
        self._crop_vw = w
        self._crop_vh = h
        self._crop_rect = self._from_video_coords(x, y, w, h)
        if pending:
            self._pending_restore = (x, y, w, h)
        self.update()

    # ── Coordinate mapping (all in logical space) ─────────────

    def _to_video_coords(self, rect: QRect) -> tuple[int, int, int, int]:
        vr = self._video_rect()
        if vr.width() == 0 or vr.height() == 0:
            return 0, 0, self._video_w, self._video_h
        sx = self._video_w / vr.width()
        sy = self._video_h / vr.height()
        return (
            int((rect.x() - vr.x()) * sx),
            int((rect.y() - vr.y()) * sy),
            int(rect.width() * sx),
            int(rect.height() * sy),
        )

    def _from_video_coords(self, x: int, y: int, w: int, h: int) -> QRect:
        vr = self._video_rect()
        if self._video_w == 0 or self._video_h == 0:
            return QRect(0, 0, self.width(), self.height())
        sx = vr.width() / self._video_w
        sy = vr.height() / self._video_h
        return QRect(
            int(x * sx) + vr.x(),
            int(y * sy) + vr.y(),
            int(w * sx),
            int(h * sy),
        )

    def _sync_crop_coords(self):
        """Update stored video coords from widget crop rect and emit signal."""
        coords = self._to_video_coords(self._crop_rect.normalized())
        self._crop_vx, self._crop_vy, self._crop_vw, self._crop_vh = coords
        self.crop_changed.emit(*coords)

    # ── Painting ─────────────────────────────────────────────

    def paintEvent(self, event):
        parent = self.parent()
        vz = getattr(parent, '_view_zoom', 1.0)
        vpx = getattr(parent, '_view_pan_x', 0.0)
        vpy = getattr(parent, '_view_pan_y', 0.0)

        painter = QPainter(self)
        painter.save()
        painter.translate(vpx, vpy)
        painter.scale(vz, vz)

        vr = self._video_rect()
        r = self._crop_rect.normalized()

        # Dim video area outside the crop
        dim = QColor(0, 0, 0, 120)
        path = QPainterPath()
        path.addRect(QRectF(vr))
        path.addRect(QRectF(r))
        painter.fillPath(path, dim)

        # Crop border
        painter.setPen(QPen(QColor(255, 255, 255), 2 / vz))
        painter.drawRect(r)

        # Rule of thirds
        painter.setPen(QPen(QColor(255, 255, 255, 80), 1 / vz, Qt.PenStyle.DashLine))
        for i in range(1, 3):
            x = r.x() + r.width() * i // 3
            y = r.y() + r.height() * i // 3
            painter.drawLine(x, r.top(), x, r.bottom())
            painter.drawLine(r.left(), y, r.right(), y)

        # Corner and edge handles
        hs = HANDLE_SIZE / vz
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(Qt.PenStyle.NoPen)
        for hx, hy in self._handle_positions(r):
            painter.drawRect(int(hx - hs / 2), int(hy - hs / 2), int(hs), int(hs))

        painter.restore()
        painter.end()

    def _handle_positions(self, r: QRect):
        return [
            (r.left(), r.top()), (r.right(), r.top()),
            (r.left(), r.bottom()), (r.right(), r.bottom()),
            (r.center().x(), r.top()), (r.center().x(), r.bottom()),
            (r.left(), r.center().y()), (r.right(), r.center().y()),
        ]

    # ── Mouse interaction ────────────────────────────────────

    def _hit_handle(self, pos: QPoint) -> str | None:
        r = self._crop_rect.normalized()
        vz = getattr(self.parent(), '_view_zoom', 1.0)
        tol = HANDLE_SIZE / vz  # constant screen-space hit radius
        handles = {
            "tl": (r.left(), r.top()), "tr": (r.right(), r.top()),
            "bl": (r.left(), r.bottom()), "br": (r.right(), r.bottom()),
            "t": (r.center().x(), r.top()), "b": (r.center().x(), r.bottom()),
            "l": (r.left(), r.center().y()), "r": (r.right(), r.center().y()),
        }
        for name, (hx, hy) in handles.items():
            if abs(pos.x() - hx) <= tol and abs(pos.y() - hy) <= tol:
                return name
        return None

    def mousePressEvent(self, event):
        # A drag begins — let the host snapshot one undo entry for the whole
        # gesture (a no-op gesture like a view-pan commits nothing).
        self.gesture_started.emit()
        if event.button() == Qt.MouseButton.MiddleButton:
            # View pan (widget coords — moves the viewport)
            self._panning = True
            self._pan_is_view = True
            self._pan_start = event.pos()
            parent = self.parent()
            self._pan_start_x = getattr(parent, '_view_pan_x', 0.0)
            self._pan_start_y = getattr(parent, '_view_pan_y', 0.0)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._stretching = True
            self._stretch_start = self._to_logical(event.pos())
            parent = self.parent()
            self._stretch_start_h = getattr(parent, '_stretch_h', 1.0)
            self._stretch_start_v = getattr(parent, '_stretch_v', 1.0)
            vr = self._video_rect()
            self._stretch_start_vr_w = max(1, vr.width())
            self._stretch_start_vr_h = max(1, vr.height())
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return

        logical = self._to_logical(event.pos())

        if self._locked:
            # Locked: left-click = content pan (logical coords)
            self._panning = True
            self._pan_is_view = False
            self._pan_start = logical
            parent = self.parent()
            self._pan_start_x = getattr(parent, '_pan_x', 0.0)
            self._pan_start_y = getattr(parent, '_pan_y', 0.0)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        handle = self._hit_handle(logical)
        if handle:
            self._resizing = True
            self._resize_edge = handle
        elif self._crop_rect.normalized().contains(logical):
            self._dragging = True
            self._drag_offset = logical - self._crop_rect.normalized().topLeft()
        else:
            # Left-click outside crop = content pan (logical coords)
            self._panning = True
            self._pan_is_view = False
            self._pan_start = logical
            parent = self.parent()
            self._pan_start_x = getattr(parent, '_pan_x', 0.0)
            self._pan_start_y = getattr(parent, '_pan_y', 0.0)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        pos = event.pos()
        parent = self.parent()

        if self._panning:
            if self._pan_is_view:
                # View pan: widget coords
                dx = pos.x() - self._pan_start.x()
                dy = pos.y() - self._pan_start.y()
                if parent:
                    parent._view_pan_x = self._pan_start_x + dx
                    parent._view_pan_y = self._pan_start_y + dy
                    parent.update()
                    parent.zoom_changed.emit()
            else:
                # Content pan: logical coords
                logical = self._to_logical(pos)
                dx = logical.x() - self._pan_start.x()
                dy = logical.y() - self._pan_start.y()
                if parent:
                    parent._pan_x = self._pan_start_x + dx
                    parent._pan_y = self._pan_start_y + dy
                    parent.update()
                    parent.zoom_changed.emit()
            return

        if self._stretching:
            logical = self._to_logical(pos)
            dx = logical.x() - self._stretch_start.x()
            dy = logical.y() - self._stretch_start.y()
            sens_h = 2.0 / self._stretch_start_vr_w
            sens_v = 2.0 / self._stretch_start_vr_h
            new_h = max(0.1, min(5.0, self._stretch_start_h + dx * sens_h))
            new_v = max(0.1, min(5.0, self._stretch_start_v + dy * sens_v))
            self.stretch_changed.emit(new_h, new_v)
            return

        logical = self._to_logical(pos)
        r = self._crop_rect.normalized()

        if self._resizing and self._resize_edge:
            r = QRect(r)
            e = self._resize_edge
            # Clamp mouse position to video rect
            vr = self._video_rect()
            clamped = QPoint(
                max(vr.left(), min(logical.x(), vr.right())),
                max(vr.top(), min(logical.y(), vr.bottom()))
            )
            if "l" in e:
                r.setLeft(clamped.x())
            if "r" in e:
                r.setRight(clamped.x())
            if "t" in e:
                r.setTop(clamped.y())
            if "b" in e:
                r.setBottom(clamped.y())

            if self._aspect_ratio:
                aw, ah = self._aspect_ratio
                target = aw / ah
                if e in ("t", "b"):
                    new_w = int(r.height() * target)
                    cx = r.center().x()
                    r.setLeft(cx - new_w // 2)
                    r.setRight(cx - new_w // 2 + new_w)
                else:
                    new_h = int(r.width() / target)
                    if "b" in e or "t" not in e:
                        r.setHeight(new_h)
                    else:
                        r.setTop(r.bottom() - new_h)

            # Normalize (handle may have crossed opposite edge) and enforce minimum
            r = r.normalized()
            if r.width() < 2:
                r.setWidth(2)
            if r.height() < 2:
                r.setHeight(2)

            self._crop_rect = r
            self._sync_crop_coords()
            self.update()

        elif self._dragging:
            new_tl = logical - self._drag_offset
            vr = self._video_rect()
            new_tl = QPoint(
                max(vr.left(), min(new_tl.x(), vr.right() - r.width())),
                max(vr.top(), min(new_tl.y(), vr.bottom() - r.height()))
            )
            self._crop_rect = QRect(new_tl, r.size())
            self._sync_crop_coords()
            self.update()

        else:
            handle = self._hit_handle(logical)
            cursors = {
                "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
                "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
            }
            if handle:
                self.setCursor(cursors[handle])
            elif r.contains(logical):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self.gesture_finished.emit()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self.unsetCursor()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._stretching = False
            self.unsetCursor()
            return
        if self._panning:
            self._panning = False
            self.unsetCursor()
        self._dragging = False
        self._resizing = False
        self._resize_edge = None

    def resizeEvent(self, event):
        if self._video_w > 0 and self._video_h > 0:
            self._crop_rect = self._from_video_coords(
                self._crop_vx, self._crop_vy, self._crop_vw, self._crop_vh
            )
        else:
            self._crop_rect = QRect(0, 0, self.width(), self.height())
        # Do NOT call _sync_crop_coords — video coords are authoritative and
        # the widget→video round-trip accumulates integer truncation error
        self.update()
