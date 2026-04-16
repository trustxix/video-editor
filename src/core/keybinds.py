"""Rebindable keyboard/mouse shortcut system.

Data model:
    ActionDef      — metadata for a bindable action (id, name, category, defaults)
    Keybind        — a single input combo (modifiers + key or mouse button)
    KeybindManager — registry that maps Keybind → action_id(s)

Binding strings use the format: "Ctrl+Shift+W", "Space", "MouseBack".
Modifier order is always Ctrl → Shift → Alt when formatted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from PyQt6.QtCore import Qt


# ── Qt enum → string mappings ────────────────────────────────────

_KEY_TO_NAME: dict[int, str] = {}
_NAME_TO_KEY: dict[str, int] = {}

for _attr in dir(Qt.Key):
    if _attr.startswith("Key_"):
        _val = getattr(Qt.Key, _attr)
        _short = _attr[4:]
        _KEY_TO_NAME[int(_val)] = _short
        _NAME_TO_KEY[_short] = int(_val)

_MOUSE_BUTTON_MAP = {
    Qt.MouseButton.LeftButton.value: "MouseLeft",
    Qt.MouseButton.RightButton.value: "MouseRight",
    Qt.MouseButton.MiddleButton.value: "MouseMiddle",
    Qt.MouseButton.BackButton.value: "MouseBack",
    Qt.MouseButton.ForwardButton.value: "MouseForward",
}
_NAME_TO_MOUSE = {v: k for k, v in _MOUSE_BUTTON_MAP.items()}

_MOD_DEFS = [
    (Qt.KeyboardModifier.ControlModifier.value, "Ctrl"),
    (Qt.KeyboardModifier.ShiftModifier.value, "Shift"),
    (Qt.KeyboardModifier.AltModifier.value, "Alt"),
]
_MOD_MASK = sum(m for m, _ in _MOD_DEFS)

MODIFIER_KEYS = frozenset({
    Qt.Key.Key_Control.value, Qt.Key.Key_Shift.value, Qt.Key.Key_Alt.value,
    Qt.Key.Key_Meta.value, Qt.Key.Key_AltGr.value,
})


def _to_int(val) -> int:
    """Convert a PyQt6 enum/flag value to int.
    Qt.Key supports int() directly; Qt.KeyboardModifier and Qt.MouseButton
    (flag enums) require .value in PyQt6 >= 6.4."""
    if isinstance(val, int):
        return val
    return val.value


# ── Data types ───────────────────────────────────────────────────

@dataclass(frozen=True)
class Keybind:
    """A single input combination: modifiers + (key XOR mouse button)."""
    modifiers: int = 0
    key: int = 0
    mouse_button: int = 0


@dataclass
class ActionDef:
    """Metadata for one bindable action."""
    action_id: str
    display_name: str
    category: str
    default_bindings: list[str] = field(default_factory=list)
    allow_repeat: bool = False


# ── Action definitions ───────────────────────────────────────────

ACTION_DEFS: dict[str, ActionDef] = {}


def _def(aid: str, name: str, cat: str, defaults: list[str], repeat: bool = False):
    ACTION_DEFS[aid] = ActionDef(aid, name, cat, list(defaults), repeat)


_def("play_pause",          "Play / Pause",        "Playback", ["Space"])
_def("frame_step_forward",  "Frame Step Forward",  "Playback", ["Right", "Period"],  repeat=True)
_def("frame_step_backward", "Frame Step Backward", "Playback", ["Left", "Comma"],   repeat=True)
_def("seek_forward_5s",     "Seek Forward 5s",     "Playback", ["Shift+Right"],      repeat=True)
_def("seek_backward_5s",    "Seek Backward 5s",    "Playback", ["Shift+Left"],       repeat=True)
_def("queue_prev",          "Previous Clip",       "Queue",    ["Ctrl+Left"])
_def("queue_next",          "Next Clip",           "Queue",    ["Ctrl+Right"])
_def("open_file",           "Open Video",          "File",     ["Ctrl+O"])
_def("export_current",      "Export Current",      "File",     ["Ctrl+E"])
_def("export_all",          "Export All Edited",   "File",     [])
_def("show_in_explorer",    "Show in Explorer",    "File",     [])
_def("close_clip",          "Close Current Clip",  "File",     ["Ctrl+W"])
_def("clear_queue",         "Clear Queue",         "File",     ["Ctrl+Shift+W"])
_def("undo",                "Undo",                "Edit",     ["Ctrl+Z"])
_def("redo",                "Redo",                "Edit",     ["Ctrl+Y"])
_def("settings",            "Settings",            "App",      [])
_def("mode_editor",         "Switch to Editor",    "App",      ["F5"])
_def("mode_player",         "Switch to Player",    "App",      ["F6"])
_def("fullscreen",          "Toggle Fullscreen",   "App",      ["F11"])
_def("quit",                "Quit",                "App",      ["Ctrl+Q"])


# ── Player action definitions ───────────────────────────────────

PLAYER_ACTION_DEFS: dict[str, ActionDef] = {}


def _pdef(aid: str, name: str, cat: str, defaults: list[str], repeat: bool = False):
    PLAYER_ACTION_DEFS[aid] = ActionDef(aid, name, cat, list(defaults), repeat)


# Playback
_pdef("p_play_pause",       "Play / Pause",         "Playback", ["Space", "MediaPlay"])
_pdef("p_seek_fwd",         "Seek Forward",         "Playback", ["Right"],          repeat=True)
_pdef("p_seek_back",        "Seek Backward",        "Playback", ["Left"],           repeat=True)
_pdef("p_seek_fwd_large",   "Seek Forward (Large)", "Playback", ["Shift+Right"],    repeat=True)
_pdef("p_seek_back_large",  "Seek Back (Large)",    "Playback", ["Shift+Left"],     repeat=True)
_pdef("p_frame_fwd",        "Frame Step Forward",   "Playback", ["Period"],         repeat=True)
_pdef("p_frame_back",       "Frame Step Backward",  "Playback", ["Comma"],          repeat=True)
_pdef("p_speed_up",         "Speed Up",             "Playback", ["BracketRight"],   repeat=True)
_pdef("p_speed_down",       "Speed Down",           "Playback", ["BracketLeft"],    repeat=True)
_pdef("p_speed_reset",      "Reset Speed",          "Playback", ["Backspace"])

# Volume
_pdef("p_volume_up",        "Volume Up",            "Volume",   ["Up"],             repeat=True)
_pdef("p_volume_down",      "Volume Down",          "Volume",   ["Down"],           repeat=True)
_pdef("p_mute",             "Mute Toggle",          "Volume",   ["M"])

# Navigation
_pdef("p_next_file",        "Next File",            "Navigation", ["Ctrl+Right", "MediaNext"])
_pdef("p_prev_file",        "Previous File",        "Navigation", ["Ctrl+Left", "MediaPrevious"])
_pdef("p_jump_start",       "Jump to Start",        "Navigation", ["Home"])
_pdef("p_jump_end",         "Jump to End",          "Navigation", ["End"])
_pdef("p_goto_time",        "Go to Timestamp",      "Navigation", ["Ctrl+G"])

# Loop
_pdef("p_loop_cycle",       "Cycle Loop Mode",      "Loop",     ["Ctrl+L"])
_pdef("p_ab_mark",          "A-B Loop Mark",        "Loop",     ["L"])
_pdef("p_shuffle",          "Toggle Shuffle",       "Loop",     ["Ctrl+Shift+S"])

# File
_pdef("p_screenshot",       "Screenshot",           "File",     ["S"])
_pdef("p_copy_path",        "Copy File Path",       "File",     ["Ctrl+C"])
_pdef("p_open_external",    "Open in External App", "File",     ["Ctrl+Shift+O"])
_pdef("p_open_folder",      "Open Folder",          "File",     ["Ctrl+O"])
_pdef("p_refresh",          "Refresh File List",    "File",     ["F5"])

# App
_pdef("p_aspect_cycle",     "Cycle Aspect Ratio",   "App",      ["A"])
_pdef("p_compact",          "Toggle Compact Mode",  "App",      ["Ctrl+M"])
_pdef("p_fit_window",       "Fit Window to Video",  "App",      ["Ctrl+F"])
_pdef("p_fullscreen",       "Toggle Fullscreen",    "App",      ["F11"])
_pdef("p_mode_editor",      "Switch to Editor",     "App",      ["Escape"])
_pdef("p_quit",             "Quit",                 "App",      ["Ctrl+Q"])


# ── Parse / format ───────────────────────────────────────────────

def parse_binding(s: str) -> Keybind | None:
    """Parse "Ctrl+Shift+Space" or "MouseBack" → Keybind, or None."""
    if not s or not s.strip():
        return None
    parts = s.strip().split("+")
    mods = key = mouse = 0
    for part in parts:
        if part == "Ctrl":
            mods |= Qt.KeyboardModifier.ControlModifier.value
        elif part == "Shift":
            mods |= Qt.KeyboardModifier.ShiftModifier.value
        elif part == "Alt":
            mods |= Qt.KeyboardModifier.AltModifier.value
        elif part in _NAME_TO_MOUSE:
            mouse = _NAME_TO_MOUSE[part]
        elif part in _NAME_TO_KEY:
            key = _NAME_TO_KEY[part]
        else:
            return None
    if key == 0 and mouse == 0:
        return None
    return Keybind(modifiers=mods, key=key, mouse_button=mouse)


def format_binding(kb: Keybind) -> str:
    """Keybind → human-readable string like "Ctrl+Shift+W"."""
    parts: list[str] = []
    for flag, name in _MOD_DEFS:
        if kb.modifiers & flag:
            parts.append(name)
    if kb.key:
        parts.append(_KEY_TO_NAME.get(kb.key, f"0x{kb.key:X}"))
    if kb.mouse_button:
        parts.append(_MOUSE_BUTTON_MAP.get(kb.mouse_button, f"Mouse?"))
    return "+".join(parts)


def keybind_from_key_event(modifiers, key) -> Keybind | None:
    """Build a Keybind from a QKeyEvent's modifiers and key.
    Accepts raw PyQt6 enum values or ints."""
    k = _to_int(key)
    if k in MODIFIER_KEYS:
        return None
    return Keybind(modifiers=_to_int(modifiers) & _MOD_MASK, key=k)


def keybind_from_mouse_event(modifiers, button) -> Keybind:
    """Build a Keybind from a QMouseEvent's modifiers and button.
    Accepts raw PyQt6 enum values or ints."""
    return Keybind(modifiers=_to_int(modifiers) & _MOD_MASK,
                   mouse_button=_to_int(button))


# ── Manager ──────────────────────────────────────────────────────

class KeybindManager:
    """Central keybind registry. Owns binding data and provides O(1) lookup.

    Parameters:
        action_defs: The ActionDef registry to use (ACTION_DEFS or PLAYER_ACTION_DEFS).
        bindings: Optional saved bindings dict to overlay on defaults.
    """

    def __init__(self, action_defs: dict[str, ActionDef],
                 bindings: dict[str, list[str]] | None = None):
        self._action_defs = action_defs
        self._bindings: dict[str, list[str]] = {
            aid: list(adef.default_bindings) for aid, adef in action_defs.items()
        }
        if bindings:
            for aid in action_defs:
                if aid in bindings and isinstance(bindings[aid], list):
                    self._bindings[aid] = [s for s in bindings[aid] if isinstance(s, str)]
        self._rebuild_lookup()

    def _rebuild_lookup(self):
        self._lookup: dict[Keybind, list[str]] = {}
        for aid, strs in self._bindings.items():
            for s in strs:
                kb = parse_binding(s)
                if kb is not None:
                    self._lookup.setdefault(kb, []).append(aid)

    def lookup(self, kb: Keybind) -> list[str]:
        return self._lookup.get(kb, [])

    def get_bindings(self, action_id: str) -> list[str]:
        return list(self._bindings.get(action_id, []))

    def set_bindings(self, action_id: str, binds: list[str]):
        self._bindings[action_id] = list(binds)
        self._rebuild_lookup()

    def to_dict(self) -> dict[str, list[str]]:
        return {aid: list(b) for aid, b in self._bindings.items()}

    def reset_to_defaults(self):
        self._bindings = {
            aid: list(adef.default_bindings)
            for aid, adef in self._action_defs.items()
        }
        self._rebuild_lookup()

    def get_menu_shortcut(self, action_id: str) -> str:
        """First keyboard binding suitable for menu display.
        Mouse bindings are skipped (menus can't show them)."""
        for b in self._bindings.get(action_id, []):
            if "Mouse" not in b:
                return b
        return ""
