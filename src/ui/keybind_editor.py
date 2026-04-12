"""Keybind editor panel for the Settings dialog.

KeybindCapture — click-to-capture widget for a single binding slot.
KeybindEditor  — full scrollable panel with all actions grouped by category.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal

from src.core.keybinds import (
    ACTION_DEFS, KeybindManager, format_binding,
    keybind_from_key_event, keybind_from_mouse_event,
    MODIFIER_KEYS, _to_int,
)


class KeybindCapture(QLineEdit):
    """Read-only field that captures key/mouse input when clicked.

    Flow: click → "Press key or button..." → next key or mouse press is
    captured. Escape cancels. Delete/Backspace clears.
    Mouse: first click enters capture mode; next mouse press captures.
    """

    binding_changed = pyqtSignal(str)

    def __init__(self, binding: str = "", parent=None):
        super().__init__(parent)
        self._binding = binding
        self._capturing = False
        self._capture_ready = False
        self._conflict = False

        self.setReadOnly(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedWidth(140)
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._refresh()

    def get_binding(self) -> str:
        return self._binding

    def set_binding(self, binding: str):
        self._binding = binding
        self._refresh()

    def set_conflict(self, conflict: bool, tooltip: str = ""):
        self._conflict = conflict
        self.setToolTip(tooltip if conflict else "Click to rebind  |  Del to clear")
        self._refresh()

    # ── Visual ────────────────────────────────────────────────

    def _refresh(self):
        if self._capturing:
            self.setText("Press key or button...")
            self.setStyleSheet("QLineEdit { border: 2px solid #6688ff; }")
        elif self._conflict:
            self.setText(self._binding or "")
            self.setStyleSheet("QLineEdit { border: 2px solid #cccc00; }")
        else:
            self.setText(self._binding or "")
            self.setPlaceholderText("(unbound)")
            self.setStyleSheet("")

    # ── Capture ───────────────────────────────────────────────

    def mousePressEvent(self, event):
        if self._capturing and self._capture_ready:
            kb = keybind_from_mouse_event(event.modifiers(), event.button())
            self._finish(format_binding(kb))
        else:
            self._capturing = True
            self._capture_ready = False
            self._refresh()
            self.setFocus()

    def mouseReleaseEvent(self, event):
        if self._capturing and not self._capture_ready:
            self._capture_ready = True

    def keyPressEvent(self, event):
        if not self._capturing:
            return
        key = _to_int(event.key())
        if key in MODIFIER_KEYS:
            return
        if key == Qt.Key.Key_Escape.value:
            self._capturing = False
            self._capture_ready = False
            self._refresh()
            return
        if key in (Qt.Key.Key_Delete.value, Qt.Key.Key_Backspace.value):
            self._finish("")
            return
        kb = keybind_from_key_event(event.modifiers(), event.key())
        if kb:
            self._finish(format_binding(kb))

    def focusOutEvent(self, event):
        if self._capturing:
            self._capturing = False
            self._capture_ready = False
            self._refresh()
        super().focusOutEvent(event)

    def _finish(self, binding: str):
        self._binding = binding
        self._capturing = False
        self._capture_ready = False
        self._refresh()
        self.binding_changed.emit(binding)


class KeybindEditor(QWidget):
    """Full keybind editor: search bar, grouped action rows, reset button."""

    def __init__(self, bindings: dict[str, list[str]] | None = None, parent=None):
        super().__init__(parent)
        self._manager = KeybindManager(bindings)
        self._captures: dict[str, list[KeybindCapture]] = {}
        self._row_widgets: dict[str, QWidget] = {}
        self._category_labels: dict[str, QLabel] = {}
        self._setup_ui()
        self._update_conflicts()

    def get_bindings(self) -> dict[str, list[str]]:
        """Current bindings dict for saving to settings.json."""
        result: dict[str, list[str]] = {}
        for aid, caps in self._captures.items():
            result[aid] = [c.get_binding() for c in caps if c.get_binding()]
        return result

    # ── Layout ────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search shortcuts...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self._grid = QVBoxLayout(content)
        self._grid.setSpacing(1)
        self._grid.setContentsMargins(0, 0, 0, 0)

        prev_cat = ""
        for aid, adef in ACTION_DEFS.items():
            if adef.category != prev_cat:
                prev_cat = adef.category
                lbl = QLabel(f"  {adef.category.upper()}")
                lbl.setStyleSheet(
                    "font-weight: 700; padding: 10px 0 4px 0; background: transparent;"
                )
                self._grid.addWidget(lbl)
                self._category_labels[adef.category] = lbl

            row = self._build_row(aid, adef)
            self._row_widgets[aid] = row
            self._grid.addWidget(row)

        self._grid.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, stretch=1)

        btn = QPushButton("Reset All to Defaults")
        btn.clicked.connect(self._reset_all)
        layout.addWidget(btn)

    def _build_row(self, aid: str, adef) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 3, 8, 3)
        h.setSpacing(6)

        lbl = QLabel(adef.display_name)
        lbl.setFixedWidth(180)
        h.addWidget(lbl)

        bindings = self._manager.get_bindings(aid) or [""]
        caps: list[KeybindCapture] = []
        for s in bindings:
            cap = KeybindCapture(s)
            cap.binding_changed.connect(lambda _, a=aid: self._on_changed(a))
            h.addWidget(cap)
            caps.append(cap)
        self._captures[aid] = caps

        btn_add = QPushButton("+")
        btn_add.setFixedSize(26, 26)
        btn_add.setToolTip("Add another binding")
        btn_add.clicked.connect(lambda _, a=aid: self._add_slot(a))
        h.addWidget(btn_add)

        btn_rst = QPushButton("\u21ba")
        btn_rst.setFixedSize(26, 26)
        btn_rst.setToolTip("Reset to default")
        btn_rst.clicked.connect(lambda _, a=aid: self._reset_action(a))
        h.addWidget(btn_rst)

        h.addStretch()
        return row

    # ── Editing ───────────────────────────────────────────────

    def _on_changed(self, action_id: str):
        binds = [c.get_binding() for c in self._captures[action_id] if c.get_binding()]
        self._manager.set_bindings(action_id, binds)
        self._update_conflicts()

    def _add_slot(self, action_id: str):
        row = self._row_widgets[action_id]
        layout = row.layout()
        cap = KeybindCapture("")
        cap.binding_changed.connect(lambda _, a=action_id: self._on_changed(a))
        # Insert before the + button (count - 3 = before +, reset, stretch)
        layout.insertWidget(layout.count() - 3, cap)
        self._captures[action_id].append(cap)

    def _reset_action(self, action_id: str):
        defaults = ACTION_DEFS[action_id].default_bindings
        self._manager.set_bindings(action_id, defaults)
        self._rebuild_row(action_id)
        self._update_conflicts()

    def _rebuild_row(self, action_id: str):
        # Disconnect old captures to prevent stale signals firing between
        # deleteLater() and actual destruction.
        for cap in self._captures.get(action_id, []):
            try:
                cap.binding_changed.disconnect()
            except TypeError:
                pass
        old = self._row_widgets[action_id]
        idx = self._grid.indexOf(old)
        self._grid.removeWidget(old)
        old.setParent(None)
        old.deleteLater()

        new = self._build_row(action_id, ACTION_DEFS[action_id])
        self._row_widgets[action_id] = new
        self._grid.insertWidget(idx, new)

    def _reset_all(self):
        self._manager.reset_to_defaults()
        for aid in ACTION_DEFS:
            self._rebuild_row(aid)
        self._update_conflicts()

    # ── Conflicts ─────────────────────────────────────────────

    def _update_conflicts(self):
        bind_map: dict[str, list[str]] = {}
        for aid, caps in self._captures.items():
            for cap in caps:
                b = cap.get_binding()
                if b:
                    bind_map.setdefault(b, []).append(aid)

        for aid, caps in self._captures.items():
            for cap in caps:
                b = cap.get_binding()
                if b and len(bind_map.get(b, [])) > 1:
                    others = [
                        ACTION_DEFS[a].display_name
                        for a in bind_map[b] if a != aid
                    ]
                    cap.set_conflict(
                        True, f"\u26a0 Also bound to: {', '.join(others)}"
                    )
                else:
                    cap.set_conflict(False)

    # ── Search ────────────────────────────────────────────────

    def _on_search(self, text: str):
        q = text.lower().strip()
        visible_cats: set[str] = set()
        for aid, row in self._row_widgets.items():
            if not q:
                match = True
            else:
                # Match on action display name OR any of its binding strings
                match = q in ACTION_DEFS[aid].display_name.lower()
                if not match:
                    for cap in self._captures.get(aid, []):
                        if q in cap.get_binding().lower():
                            match = True
                            break
            row.setVisible(match)
            if match:
                visible_cats.add(ACTION_DEFS[aid].category)
        for cat, lbl in self._category_labels.items():
            lbl.setVisible(cat in visible_cats)
