"""Shared custom widgets used across the UI."""

from PyQt6.QtWidgets import (
    QSlider, QStyle, QWidget, QLabel, QHBoxLayout, QGraphicsOpacityEffect,
)
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QTimer, pyqtSignal,
)


class ClickSlider(QSlider):
    """QSlider that jumps directly to the click position on left-click,
    then tracks the mouse for smooth dragging. Stock QSlider pages by
    pageStep on groove clicks, which feels like discrete steps.

    We bypass super().mousePressEvent so Qt's internal pressedControl
    is never set — meaning super().mouseReleaseEvent won't emit
    sliderReleased. We emit it ourselves in mouseReleaseEvent.
    """

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            val = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(),
                int(event.position().x()), self.width(),
            )
            self.setValue(val)
            event.accept()
            self.setSliderDown(True)
            self.sliderPressed.emit()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            val = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(),
                int(event.position().x()), self.width(),
            )
            self.setValue(val)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        was_down = self.isSliderDown()
        self.setSliderDown(False)
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and was_down:
            self.sliderReleased.emit()

    def wheelEvent(self, event):
        """Scroll wheel adjusts value by 5 per notch (120 degrees)."""
        delta = event.angleDelta().y()
        steps = delta // 120
        self.setValue(self.value() + steps * 5)
        event.accept()


class _ClickableLabel(QLabel):
    """QLabel that emits `clicked` on left mouse press."""
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class CompactVolumeControl(QWidget):
    """Volume control: clickable icon (mute toggle) + slider that
    fades in on hover. Container width is fixed so the surrounding
    layout never reflows during the fade animation.

    Exposes `slider` (a ClickSlider) for direct value access by callers,
    plus `icon_clicked` / `value_changed` / `slider_released` signals.
    """

    icon_clicked = pyqtSignal()
    value_changed = pyqtSignal(int)
    slider_released = pyqtSignal()

    SLIDER_W = 90
    FADE_MS = 160
    COLLAPSE_DELAY_MS = 220

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.icon = _ClickableLabel()
        self.icon.setFixedSize(20, 20)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setCursor(Qt.CursorShape.PointingHandCursor)
        self.icon.clicked.connect(self.icon_clicked.emit)
        layout.addWidget(self.icon)

        self.slider = ClickSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(100)
        self.slider.setFixedSize(self.SLIDER_W, 16)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 4px; background: #3a3a3a; border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #6aaae6; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 10px; height: 10px;
                margin: -4px 0;
                background: white; border-radius: 5px;
            }
        """)
        self.slider.valueChanged.connect(self.value_changed.emit)
        self.slider.sliderReleased.connect(self.slider_released.emit)
        layout.addWidget(self.slider)

        # Fade in/out via opacity effect — slider always occupies space,
        # so the layout doesn't reflow.
        self._effect = QGraphicsOpacityEffect(self.slider)
        self._effect.setOpacity(0.0)
        self.slider.setGraphicsEffect(self._effect)
        self._set_slider_interactive(False)

        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(self.FADE_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(self.COLLAPSE_DELAY_MS)
        self._collapse_timer.timeout.connect(self._collapse)

        # Total fixed width so seek bar never reflows
        self.setFixedWidth(20 + 4 + self.SLIDER_W)

    # Public conveniences
    def value(self) -> int:
        return self.slider.value()

    def setValue(self, v: int) -> None:
        self.slider.setValue(v)

    def blockSignals(self, b: bool) -> bool:
        return self.slider.blockSignals(b)

    def set_icon_pixmap(self, pix) -> None:
        self.icon.setPixmap(pix)

    # Hover handling
    def enterEvent(self, event):
        self._collapse_timer.stop()
        self._expand()
        super().enterEvent(event)

    def leaveEvent(self, event):
        # Don't snap-collapse — give the user a brief grace period
        # so they can move from icon to slider without it disappearing.
        self._collapse_timer.start()
        super().leaveEvent(event)

    def _expand(self):
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._set_slider_interactive(True)

    def _collapse(self):
        # If user is dragging, hold off until they release
        if self.slider.isSliderDown():
            self._collapse_timer.start()
            return
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_finished(self):
        if self._effect.opacity() < 0.05:
            self._set_slider_interactive(False)

    def _set_slider_interactive(self, on: bool) -> None:
        self.slider.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, not on)
