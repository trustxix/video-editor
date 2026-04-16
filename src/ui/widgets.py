"""Shared custom widgets used across the UI."""

from PyQt6.QtWidgets import QSlider, QStyle
from PyQt6.QtCore import Qt


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
