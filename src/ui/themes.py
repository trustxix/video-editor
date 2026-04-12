"""Application themes — QSS stylesheets applied to the whole QApplication.

Each theme is a palette of named colors that feed a single QSS template.
To add a theme, append a new entry to THEMES. To tweak the look of all themes
at once, edit `_build_qss` below.
"""
from PyQt6.QtWidgets import QApplication


THEMES: dict[str, dict[str, str]] = {
    "Dark": {
        "bg":           "#1e1e1e",
        "bg_alt":       "#252526",
        "bg_input":     "#333337",
        "bg_hover":     "#2d2d30",
        "border":       "#3e3e42",
        "fg":           "#d4d4d4",
        "fg_dim":       "#969696",
        "accent":       "#007acc",
        "accent_hover": "#1890d6",
        "disabled":     "#5a5a5a",
    },
    "Midnight": {
        "bg":           "#0f1419",
        "bg_alt":       "#1a1f2a",
        "bg_input":     "#242b38",
        "bg_hover":     "#2a3140",
        "border":       "#2d3444",
        "fg":           "#d0d6e0",
        "fg_dim":       "#7a8294",
        "accent":       "#64ffda",
        "accent_hover": "#80ffed",
        "disabled":     "#4a5160",
    },
    "Graphite": {
        "bg":           "#2b2b2b",
        "bg_alt":       "#323232",
        "bg_input":     "#3c3c3c",
        "bg_hover":     "#404040",
        "border":       "#4a4a4a",
        "fg":           "#e8e8e8",
        "fg_dim":       "#a0a0a0",
        "accent":       "#ff8c42",
        "accent_hover": "#ffa866",
        "disabled":     "#606060",
    },
    "Light": {
        "bg":           "#f5f5f5",
        "bg_alt":       "#ffffff",
        "bg_input":     "#ffffff",
        "bg_hover":     "#e8e8e8",
        "border":       "#d0d0d0",
        "fg":           "#1e1e1e",
        "fg_dim":       "#6c6c6c",
        "accent":       "#007acc",
        "accent_hover": "#1890d6",
        "disabled":     "#a0a0a0",
    },
}

DEFAULT_THEME = "Dark"


def _build_qss(c: dict[str, str], scale: float = 1.0) -> str:
    # Scale helper — rounds to int for pixel values, 1 decimal for pt
    def px(base: int) -> int:
        return max(1, round(base * scale))
    def pt(base: float) -> str:
        return f"{base * scale:.1f}"

    return f"""
QWidget {{
    background-color: {c['bg']};
    color: {c['fg']};
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: {pt(9)}pt;
}}
QMainWindow, QDialog {{
    background-color: {c['bg']};
}}
QGroupBox {{
    background-color: {c['bg_alt']};
    border: 1px solid {c['border']};
    border-radius: {px(4)}px;
    margin-top: {px(10)}px;
    padding: {px(8)}px {px(6)}px {px(6)}px {px(6)}px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {px(8)}px;
    padding: 0 {px(4)}px;
    color: {c['fg_dim']};
    background-color: {c['bg']};
}}
QLabel {{
    background: transparent;
    border: none;
}}
QPushButton {{
    background-color: {c['bg_input']};
    color: {c['fg']};
    border: 1px solid {c['border']};
    border-radius: {px(4)}px;
    padding: {px(3)}px {px(10)}px;
    min-height: {px(16)}px;
}}
QPushButton:hover {{
    background-color: {c['bg_hover']};
    border-color: {c['accent']};
}}
QPushButton:pressed {{
    background-color: {c['accent']};
    color: {c['bg']};
}}
QPushButton:checked {{
    background-color: {c['accent']};
    color: {c['bg']};
    border-color: {c['accent']};
}}
QPushButton:disabled {{
    color: {c['disabled']};
    border-color: {c['border']};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {c['bg_input']};
    color: {c['fg']};
    border: 1px solid {c['border']};
    border-radius: {px(4)}px;
    padding: {px(3)}px {px(4)}px;
    min-height: {px(16)}px;
    selection-background-color: {c['accent']};
    selection-color: {c['bg']};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {c['accent']};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {c['disabled']};
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background-color: {c['bg_input']};
    color: {c['fg']};
    border: 1px solid {c['border']};
    selection-background-color: {c['accent']};
    selection-color: {c['bg']};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background-color: {c['bg_input']};
    border: none;
    width: 14px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {c['accent']};
}}
QSlider::groove:horizontal {{
    height: {px(4)}px;
    background: {c['border']};
    border-radius: {px(2)}px;
}}
QSlider::sub-page:horizontal {{
    background: {c['accent']};
    border-radius: {px(2)}px;
}}
QSlider::handle:horizontal {{
    background: {c['accent']};
    width: {px(12)}px;
    margin: {px(-4)}px 0;
    border-radius: {px(6)}px;
}}
QSlider::handle:horizontal:hover {{
    background: {c['accent_hover']};
}}
QProgressBar {{
    background-color: {c['bg_input']};
    border: 1px solid {c['border']};
    border-radius: {px(4)}px;
    text-align: center;
    color: {c['fg']};
    min-height: {px(14)}px;
}}
QProgressBar::chunk {{
    background-color: {c['accent']};
    border-radius: {px(3)}px;
}}
QCheckBox {{
    spacing: 6px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: {px(13)}px;
    height: {px(13)}px;
    border: 1px solid {c['border']};
    border-radius: {px(3)}px;
    background-color: {c['bg_input']};
}}
QCheckBox::indicator:checked {{
    background-color: {c['accent']};
    border-color: {c['accent']};
}}
QMenuBar {{
    background-color: {c['bg']};
    border-bottom: 1px solid {c['border']};
}}
QMenuBar::item {{
    background: transparent;
    padding: {px(3)}px {px(8)}px;
}}
QMenuBar::item:selected {{
    background-color: {c['bg_hover']};
}}
QMenu {{
    background-color: {c['bg_alt']};
    border: 1px solid {c['border']};
    padding: 4px;
}}
QMenu::item {{
    padding: {px(4)}px {px(18)}px;
    border-radius: {px(3)}px;
}}
QMenu::item:selected {{
    background-color: {c['accent']};
    color: {c['bg']};
}}
QMenu::separator {{
    height: 1px;
    background: {c['border']};
    margin: 4px 8px;
}}
QToolTip {{
    background-color: {c['bg_alt']};
    color: {c['fg']};
    border: 1px solid {c['accent']};
    padding: {px(3)}px;
    border-radius: {px(3)}px;
}}
QStatusBar {{
    background-color: {c['bg']};
    border-top: 1px solid {c['border']};
    color: {c['fg_dim']};
    font-family: 'Segoe UI', monospace;
    font-size: {pt(8)}pt;
    padding: {px(1)}px {px(6)}px;
}}
QStatusBar QLabel {{
    background: transparent;
    color: {c['fg_dim']};
    padding: 0 4px;
}}
QScrollBar:vertical {{
    background: {c['bg_alt']};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {c['border']};
    border-radius: 5px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c['accent']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""


def is_dark_theme(name: str) -> bool:
    """True if the theme has a dark background (for DWM title bar matching)."""
    palette = THEMES.get(name) or THEMES[DEFAULT_THEME]
    bg = palette["bg"].lstrip("#")
    r, g, b = int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


_current_scale: float = 0.9  # default slightly compact


def apply_theme(name: str, scale: float | None = None) -> None:
    """Apply a theme to the running QApplication. Unknown names fall back to default."""
    global _current_scale
    if scale is not None:
        _current_scale = scale
    palette = THEMES.get(name) or THEMES[DEFAULT_THEME]
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(_build_qss(palette, _current_scale))
