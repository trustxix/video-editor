import json
import math
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QSpinBox, QDoubleSpinBox, QComboBox, QPushButton, QFileDialog,
    QProgressBar, QMessageBox, QApplication, QLineEdit, QSlider
)
from PyQt6.QtCore import Qt, QEvent, QThread, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer

from src.core.paths import get_config_dir
from src.core.video_item import VideoItem
from src.core.archive import archive_original
from src.ui.video_player import VideoPlayer
from src.ui.crop_overlay import CropOverlay
from src.ui.trim_controls import TrimControls
from src.ui.settings_dialog import SettingsDialog
from src.ui.automation_lane import AutomationLane
from src.ui.themes import apply_theme, DEFAULT_THEME
from src.core.presets import ASPECT_PRESETS, calc_preset_crop, calc_stretch_to_fit
from src.core.ffmpeg_runner import (
    build_command, get_output_path, get_video_duration,
    get_video_resolution, run_export, export_with_automation,
)

VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv')


class ExportWorker(QThread):
    progress = pyqtSignal(float)
    finished = pyqtSignal(bool)

    def __init__(self, cmd=None, duration=0.0, auto_kwargs=None):
        super().__init__()
        self.cmd = cmd  # simple export
        self.duration = duration
        self._auto_kwargs = auto_kwargs  # automation export
        self._process = None
        self._error_msg = ""

    def run(self):
        try:
            if self._auto_kwargs:
                ok = export_with_automation(
                    **self._auto_kwargs,
                    progress_callback=self.progress.emit,
                    process_callback=self._set_process,
                )
            else:
                ok = run_export(self.cmd, self.duration, self.progress.emit,
                                process_callback=self._set_process)
        except Exception as e:
            ok = False
            self._error_msg = str(e)
        self.finished.emit(ok)

    def _set_process(self, proc):
        self._process = proc

    def cancel(self):
        """Terminate the FFmpeg child process so the thread can exit cleanly."""
        if self._process and self._process.poll() is None:
            self._process.terminate()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Editor")
        self.setMinimumSize(900, 650)
        self.setAcceptDrops(True)

        self._video_path: str | None = None
        self._video_w = 0
        self._video_h = 0
        self._duration_s = 0.0
        self._worker: ExportWorker | None = None

        self._queue: list[VideoItem] = []
        self._queue_index: int = -1
        self._last_output: str = ""
        self._settings_path = get_config_dir() / "settings.json"
        self._settings = self._load_settings()
        apply_theme(self._settings.get("theme", DEFAULT_THEME))
        self._apply_stay_on_top(self._settings.get("stay_on_top", False))

        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []

        self._setup_ui()
        self._setup_menu()
        QApplication.instance().installEventFilter(self)

    def _setup_menu(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        file_menu.addAction("&Open Video...", "Ctrl+O", self._open_file)
        file_menu.addSeparator()
        file_menu.addAction("&Settings...", self._open_settings)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", "Ctrl+Q", self.close)

        edit_menu = menu.addMenu("&Edit")
        edit_menu.addAction("&Undo", "Ctrl+Z", self._undo)
        edit_menu.addAction("&Redo", "Ctrl+Y", self._redo)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ── Queue navigation bar ──────────────────────────────
        nav = QHBoxLayout()
        self.btn_prev = QPushButton("\u25C0 Prev")
        self.btn_prev.setFixedWidth(70)
        self.btn_prev.clicked.connect(self._go_prev)
        nav.addWidget(self.btn_prev)

        self.lbl_queue = QLabel("")
        self.lbl_queue.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_queue.setStyleSheet("font-size: 13px;")
        nav.addWidget(self.lbl_queue, stretch=1)

        self.btn_next = QPushButton("Next \u25B6")
        self.btn_next.setFixedWidth(70)
        self.btn_next.clicked.connect(self._go_next)
        nav.addWidget(self.btn_next)

        self.nav_widget = QWidget()
        self.nav_widget.setLayout(nav)
        self.nav_widget.setVisible(False)
        root.addWidget(self.nav_widget)

        # ── Video area ─────────────────────────────────────────
        self.player = VideoPlayer()
        self.crop_overlay = CropOverlay(self.player.surface)
        self.crop_overlay.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        root.addWidget(self.player, stretch=1)

        # ── Trim controls ─────────────────────────────────────
        self.trim = TrimControls()
        root.addWidget(self.trim)

        # ── Speed automation lane ─────────────────────────────
        self.automation = AutomationLane()
        self.automation.changed.connect(self._on_automation_changed)
        root.addWidget(self.automation)
        self.player.set_automation(self.automation)

        # ── Bottom panel: crop fields + export ────────────────
        bottom = QHBoxLayout()

        crop_group = QGroupBox("Crop")
        cg = QHBoxLayout(crop_group)

        self.cmb_crop_mode = QComboBox()
        self.cmb_crop_mode.addItems(["Crop", "Stretch"])
        self.cmb_crop_mode.currentTextChanged.connect(self._on_crop_mode_changed)
        cg.addWidget(self.cmb_crop_mode)

        self.cmb_preset = QComboBox()
        self.cmb_preset.addItems(ASPECT_PRESETS.keys())
        self.cmb_preset.currentTextChanged.connect(self._on_preset_changed)
        cg.addWidget(self.cmb_preset)

        self.spn_x = QSpinBox(); self.spn_x.setPrefix("X: "); self.spn_x.setMinimum(-99999); self.spn_x.setMaximum(99999)
        self.spn_y = QSpinBox(); self.spn_y.setPrefix("Y: "); self.spn_y.setMinimum(-99999); self.spn_y.setMaximum(99999)
        self.spn_w = QSpinBox(); self.spn_w.setPrefix("W: "); self.spn_w.setMinimum(0); self.spn_w.setMaximum(99999)
        self.spn_h = QSpinBox(); self.spn_h.setPrefix("H: "); self.spn_h.setMinimum(0); self.spn_h.setMaximum(99999)

        for spn in (self.spn_x, self.spn_y, self.spn_w, self.spn_h):
            spn.setSingleStep(2)
            spn.valueChanged.connect(self._on_spinbox_changed)
            cg.addWidget(spn)

        self.btn_reset_crop = QPushButton("Reset")
        self.btn_reset_crop.clicked.connect(self._reset_crop)
        cg.addWidget(self.btn_reset_crop)

        self.btn_lock_crop = QPushButton("Lock")
        self.btn_lock_crop.setCheckable(True)
        self.btn_lock_crop.setFixedWidth(50)
        self.btn_lock_crop.toggled.connect(self._on_crop_lock_toggled)
        cg.addWidget(self.btn_lock_crop)

        bottom.addWidget(crop_group)

        # Effects
        effects_group = QGroupBox("Effects")
        efx = QHBoxLayout(effects_group)

        self._semitones = 0
        SEMITONE = 2 ** (1 / 12)

        self.btn_st_down = QPushButton("\u25BC")
        self.btn_st_down.setFixedSize(28, 28)
        self.btn_st_down.clicked.connect(lambda: self._change_semitone(-1))
        efx.addWidget(self.btn_st_down)

        self.lbl_semitones = QLabel("0 st")
        self.lbl_semitones.setFixedWidth(45)
        self.lbl_semitones.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_semitones.setStyleSheet("font-family: monospace;")
        efx.addWidget(self.lbl_semitones)

        self.btn_st_up = QPushButton("\u25B2")
        self.btn_st_up.setFixedSize(28, 28)
        self.btn_st_up.clicked.connect(lambda: self._change_semitone(1))
        efx.addWidget(self.btn_st_up)

        self.sld_speed = QSlider(Qt.Orientation.Horizontal)
        self.sld_speed.setRange(25, 200)  # 25% to 200%
        self.sld_speed.setValue(100)
        self.sld_speed.setFixedWidth(120)
        self.sld_speed.valueChanged.connect(self._on_speed_slider_changed)
        efx.addWidget(self.sld_speed)

        self.lbl_speed = QLabel("100%")
        self.lbl_speed.setFixedWidth(40)
        self.lbl_speed.setStyleSheet("font-family: monospace;")
        efx.addWidget(self.lbl_speed)

        efx.addWidget(QLabel("Stretch:"))
        self.spn_stretch_h = QSpinBox()
        self.spn_stretch_h.setPrefix("H:")
        self.spn_stretch_h.setSuffix("%")
        self.spn_stretch_h.setRange(10, 500)
        self.spn_stretch_h.setValue(100)
        self.spn_stretch_h.setSingleStep(10)
        efx.addWidget(self.spn_stretch_h)

        self.spn_stretch_v = QSpinBox()
        self.spn_stretch_v.setPrefix("V:")
        self.spn_stretch_v.setSuffix("%")
        self.spn_stretch_v.setRange(10, 500)
        self.spn_stretch_v.setValue(100)
        self.spn_stretch_v.setSingleStep(10)
        efx.addWidget(self.spn_stretch_v)

        bottom.addWidget(effects_group)

        export_group = QGroupBox("Export")
        eg = QVBoxLayout(export_group)
        self.btn_export = QPushButton("Export")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._export)
        eg.addWidget(self.btn_export)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        eg.addWidget(self.progress_bar)

        bottom.addWidget(export_group)
        root.addLayout(bottom)

        # ── Signals ───────────────────────────────────────────
        self.player.duration_changed.connect(self._on_video_duration)
        self.player.position_changed.connect(self._on_playback_position)
        self.trim.trim_changed.connect(self._on_trim_changed)
        self.trim.seek_requested.connect(self._on_seek_requested)
        self.crop_overlay.crop_changed.connect(self._on_overlay_crop_changed)
        self.crop_overlay.stretch_changed.connect(self._on_stretch_dragged)
        self.spn_stretch_h.valueChanged.connect(self._on_stretch_spinbox_changed)
        self.spn_stretch_v.valueChanged.connect(self._on_stretch_spinbox_changed)

        self._updating_spinboxes = False
        self._updating_stretch = False
        self._restoring = False

    # ── Drag and drop ─────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(VIDEO_EXTENSIONS):
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(VIDEO_EXTENSIONS):
                paths.append(path)
        if paths:
            self._add_to_queue(paths)

    # ── Queue management ──────────────────────────────────────

    def _add_to_queue(self, paths: list[str]):
        was_empty = len(self._queue) == 0
        for p in paths:
            self._queue.append(VideoItem(path=p))
        if was_empty:
            self._navigate_to(0)
        else:
            self._update_nav()

    def _navigate_to(self, index: int):
        if index < 0 or index >= len(self._queue):
            return
        if 0 <= self._queue_index < len(self._queue):
            self._save_current_state()

        old_index = self._queue_index
        self._queue_index = index
        item = self._queue[index]

        if not item.probed:
            try:
                item.video_w, item.video_h = get_video_resolution(item.path)
                item.duration_s = get_video_duration(item.path)
            except FileNotFoundError:
                QMessageBox.warning(self, "FFmpeg Not Found",
                    "FFmpeg/FFprobe is required but was not found.\n"
                    "Install FFmpeg and ensure it is on your PATH.")
                self._queue_index = old_index
                return
            except Exception as e:
                QMessageBox.warning(self, "Error",
                    f"Could not read video file:\n{e}")
                self._queue.pop(index)
                self._queue_index = max(0, min(old_index, len(self._queue) - 1)) if self._queue else -1
                self._update_nav()
                return
            if item.video_w == 0 or item.video_h == 0:
                QMessageBox.warning(self, "Invalid Video",
                    f"Could not read video dimensions for:\n{Path(item.path).name}")
                self._queue.pop(index)
                self._queue_index = max(0, min(old_index, len(self._queue) - 1)) if self._queue else -1
                self._update_nav()
                return
            item.duration_ms = int(item.duration_s * 1000)
            item.trim_end_ms = item.duration_ms
            item.crop_w = item.video_w
            item.crop_h = item.video_h
            item.speed = self._settings.get("default_speed", 1.0)
            item.preset_name = self._settings.get("aspect_ratio", "Free")
            item.crop_mode = "Stretch" if self._settings.get("crop_mode") == "stretch" else "Crop"
            # Apply default aspect ratio
            if item.preset_name != "Free" and item.crop_mode == "Crop":
                item.crop_x, item.crop_y, item.crop_w, item.crop_h = \
                    calc_preset_crop(item.preset_name, item.video_w, item.video_h)
            elif item.preset_name != "Free" and item.crop_mode == "Stretch":
                item.stretch_h, item.stretch_v = \
                    calc_stretch_to_fit(item.preset_name, item.video_w, item.video_h)
            item.probed = True

        self._video_path = item.path
        self._video_w = item.video_w
        self._video_h = item.video_h
        self._duration_s = item.duration_s

        self.player.load(item.path)
        self.crop_overlay.set_video_size(item.video_w, item.video_h)
        self._restore_state(item)

        self.btn_export.setEnabled(True)
        self._update_nav()

    def _save_current_state(self):
        item = self._queue[self._queue_index]
        item.trim_start_ms, item.trim_end_ms = self.trim.slider.get_selection()
        item.crop_x = self.spn_x.value()
        item.crop_y = self.spn_y.value()
        item.crop_w = self.spn_w.value()
        item.crop_h = self.spn_h.value()
        item.preset_name = self.cmb_preset.currentText()
        item.crop_mode = self.cmb_crop_mode.currentText()
        item.speed = self.sld_speed.value() / 100.0
        item.stretch_h = self.spn_stretch_h.value() / 100.0
        item.stretch_v = self.spn_stretch_v.value() / 100.0
        item.locked = self.btn_lock_crop.isChecked()
        item.speed_keyframes = self.automation.get_keyframes()

    def _restore_state(self, item: VideoItem):
        self._restoring = True
        self.trim.set_duration(item.duration_ms)
        self.trim.set_trim(item.trim_start_ms, item.trim_end_ms)

        # Block combo signals to prevent _apply_ratio from overwriting saved values
        self.cmb_crop_mode.blockSignals(True)
        self.cmb_preset.blockSignals(True)
        self._updating_spinboxes = True

        self.cmb_crop_mode.setCurrentText(item.crop_mode)
        self.cmb_preset.setCurrentText(item.preset_name)
        self.spn_x.setValue(item.crop_x)
        self.spn_y.setValue(item.crop_y)
        self.spn_w.setValue(item.crop_w)
        self.spn_h.setValue(item.crop_h)

        self._updating_spinboxes = False
        self.cmb_crop_mode.blockSignals(False)
        self.cmb_preset.blockSignals(False)

        self._apply_speed(item.speed)
        self._updating_stretch = True
        self.spn_stretch_h.setValue(int(item.stretch_h * 100))
        self.spn_stretch_v.setValue(int(item.stretch_v * 100))
        self._updating_stretch = False
        self.player.surface.set_stretch(item.stretch_h, item.stretch_v)

        # Set aspect ratio lock only in Crop mode, not Stretch mode
        if item.crop_mode == "Crop":
            self.crop_overlay.set_aspect_ratio(ASPECT_PRESETS.get(item.preset_name))
        else:
            self.crop_overlay.set_aspect_ratio(None)
        self.crop_overlay.set_crop_from_video(
            item.crop_x, item.crop_y, item.crop_w, item.crop_h, pending=True
        )
        self.btn_lock_crop.setChecked(item.locked)
        self.automation.set_duration(item.duration_ms)
        self.automation.set_keyframes(item.speed_keyframes)
        self.automation.set_base_speed(item.speed)
        need_pitched = item.speed != 1.0 or bool(item.speed_keyframes)
        self.player.enable_pitched_audio(need_pitched)
        self._restoring = False

    def _go_prev(self):
        if self._worker is not None:
            return
        self._navigate_to(self._queue_index - 1)

    def _go_next(self):
        if self._worker is not None:
            return
        self._navigate_to(self._queue_index + 1)

    def _update_nav(self):
        count = len(self._queue)
        self.nav_widget.setVisible(count > 1)
        if count > 1:
            name = Path(self._queue[self._queue_index].path).name
            self.lbl_queue.setText(
                f"Video {self._queue_index + 1} of {count}  \u2014  {name}"
            )
            self.btn_prev.setEnabled(self._queue_index > 0)
            self.btn_next.setEnabled(self._queue_index < count - 1)
        if 0 <= self._queue_index < count:
            name = Path(self._queue[self._queue_index].path).name
            self.setWindowTitle(f"Video Editor \u2014 {name}")

    def closeEvent(self, event):
        # Dead-man's switch: if clean shutdown hangs for any reason (stuck
        # QThread, unresponsive subprocess, audio sink refusing to release),
        # force-exit after 8 seconds so the user never has to kill pythonw
        # manually. Daemon so it can't keep the process alive on its own.
        import os as _os
        import threading as _th
        _watchdog = _th.Timer(8.0, lambda: _os._exit(1))
        _watchdog.daemon = True
        _watchdog.start()

        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)
            if self._worker.isRunning():
                self._worker.terminate()
                self._worker.wait()
        self.player.release()
        super().closeEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space:
            focused = QApplication.instance().focusWidget()
            if isinstance(focused, (QLineEdit, QComboBox)):
                return False
            self.player._toggle_play()
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        focused = QApplication.instance().focusWidget()
        if isinstance(focused, (QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit)):
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Left:
            self._go_prev()
        elif event.key() == Qt.Key.Key_Right:
            self._go_next()
        else:
            super().keyPressEvent(event)

    # ── Settings ──────────────────────────────────────────────

    def _load_settings(self) -> dict:
        defaults = {
            "codec": "h264", "crf": 17, "output_dir": "",
            "aspect_ratio": "16:9", "crop_mode": "crop",
            "default_speed": 1.0, "output_suffix": "_edited",
            "auto_advance": False, "audio_mode": "copy",
            "move_originals_to_archive": False,
            "untrimmed_archive_dir": "",
            "theme": DEFAULT_THEME,
            "stay_on_top": False,
        }
        try:
            data = json.loads(self._settings_path.read_text())
            defaults.update(data)
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            pass
        # Validate numeric types — corrupt settings must not crash the app
        try:
            defaults["crf"] = max(0, min(51, int(defaults["crf"])))
        except (ValueError, TypeError):
            defaults["crf"] = 17
        try:
            defaults["default_speed"] = max(0.1, min(10.0, float(defaults["default_speed"])))
        except (ValueError, TypeError):
            defaults["default_speed"] = 1.0
        if defaults.get("codec") not in ("h264", "h265"):
            defaults["codec"] = "h264"
        if defaults.get("audio_mode") not in ("copy", "reencode", "mute"):
            defaults["audio_mode"] = "copy"
        return defaults

    def _save_settings(self):
        tmp = self._settings_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self._settings, indent=2))
        tmp.replace(self._settings_path)

    def _open_settings(self):
        dlg = SettingsDialog(self._settings, self)
        if dlg.exec():
            prev_theme = self._settings.get("theme", DEFAULT_THEME)
            prev_on_top = self._settings.get("stay_on_top", False)
            self._settings = dlg.get_settings()
            self._save_settings()
            if self._settings.get("theme", DEFAULT_THEME) != prev_theme:
                apply_theme(self._settings["theme"])
            if self._settings.get("stay_on_top", False) != prev_on_top:
                self._apply_stay_on_top(self._settings["stay_on_top"])

    def _apply_stay_on_top(self, enabled: bool):
        """Toggle always-on-top via the native Win32 API.

        We can't use Qt's setWindowFlag(WindowStaysOnTopHint, ...) here: on
        Windows it destroys and recreates the HWND, which segfaults the bound
        QMediaPlayer / QVideoSink pipeline (native crash, no traceback).
        SetWindowPos(HWND_TOPMOST) toggles the OS-level topmost bit directly
        without touching any Qt resources.
        """
        import ctypes
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010
        try:
            hwnd = int(self.winId())
            flag = HWND_TOPMOST if enabled else HWND_NOTOPMOST
            ctypes.windll.user32.SetWindowPos(
                hwnd, flag, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:
            pass

    # ── Undo / Redo ────────────────────────────────────────

    def _capture_state(self) -> dict:
        return {
            'trim_start': self.trim.slider._start,
            'trim_end': self.trim.slider._end,
            'crop_x': self.spn_x.value(),
            'crop_y': self.spn_y.value(),
            'crop_w': self.spn_w.value(),
            'crop_h': self.spn_h.value(),
            'preset': self.cmb_preset.currentText(),
            'crop_mode': self.cmb_crop_mode.currentText(),
            'speed': self.sld_speed.value(),
            'stretch_h': self.spn_stretch_h.value(),
            'stretch_v': self.spn_stretch_v.value(),
            'locked': self.btn_lock_crop.isChecked(),
            'keyframes': self.automation.get_keyframes(),
        }

    def _push_undo(self):
        if self._restoring:
            return
        state = self._capture_state()
        if self._undo_stack and self._undo_stack[-1] == state:
            return
        self._undo_stack.append(state)
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _apply_undo_state(self, state: dict):
        self._restoring = True

        self.trim.set_trim(state['trim_start'], state['trim_end'])

        self.cmb_crop_mode.blockSignals(True)
        self.cmb_preset.blockSignals(True)
        self._updating_spinboxes = True
        self.cmb_crop_mode.setCurrentText(state['crop_mode'])
        self.cmb_preset.setCurrentText(state['preset'])
        self.spn_x.setValue(state['crop_x'])
        self.spn_y.setValue(state['crop_y'])
        self.spn_w.setValue(state['crop_w'])
        self.spn_h.setValue(state['crop_h'])
        self._updating_spinboxes = False
        self.cmb_crop_mode.blockSignals(False)
        self.cmb_preset.blockSignals(False)

        self._apply_speed(state['speed'] / 100.0)

        self._updating_stretch = True
        self.spn_stretch_h.setValue(state['stretch_h'])
        self.spn_stretch_v.setValue(state['stretch_v'])
        self._updating_stretch = False
        self.player.surface.set_stretch(state['stretch_h'] / 100.0, state['stretch_v'] / 100.0)

        self.btn_lock_crop.setChecked(state['locked'])

        self.automation.set_keyframes(state['keyframes'])

        if state['crop_mode'] == "Crop":
            self.crop_overlay.set_aspect_ratio(ASPECT_PRESETS.get(state['preset']))
        else:
            self.crop_overlay.set_aspect_ratio(None)
        self.crop_overlay.set_crop_from_video(
            state['crop_x'], state['crop_y'], state['crop_w'], state['crop_h']
        )

        self._restoring = False

    def _undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._capture_state())
        self._apply_undo_state(self._undo_stack.pop())

    def _redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._capture_state())
        self._apply_undo_state(self._redo_stack.pop())

    def _apply_speed(self, rate: float):
        """Apply speed from any source (slider, semitone, or automation)."""
        self.player.set_playback_rate(rate)
        # Enable/disable pitched audio based on whether speed != 1.0 or automation exists
        need_pitched = rate != 1.0 or bool(self.automation.get_keyframes())
        self.player.enable_pitched_audio(need_pitched)
        self._update_speed_display(rate)

    def _apply_speed_live(self, rate: float):
        """Update speed during playback WITHOUT recreating audio pipeline. For automation ramps."""
        self.player.set_playback_rate(rate)
        # Ensure pitched audio is running (handles case where automation starts at 1.0x)
        if not self.player._pitched_active:
            self.player.enable_pitched_audio(True)
        self._update_speed_display(rate)

    def _update_speed_display(self, rate: float):
        """Update speed UI labels and slider."""
        self._semitones = round(12 * math.log2(rate)) if rate > 0 else 0
        self.lbl_semitones.setText(f"{self._semitones:+d} st")
        pct = round(rate * 100)
        self.lbl_speed.setText(f"{pct}%")
        self.sld_speed.blockSignals(True)
        self.sld_speed.setValue(max(25, min(200, pct)))
        self.sld_speed.blockSignals(False)

    def _change_semitone(self, delta: int):
        """Semitone up/down buttons."""
        if self._restoring:
            return
        self._push_undo()
        self._semitones = max(-24, min(24, self._semitones + delta))
        rate = 2 ** (self._semitones / 12)
        self._apply_speed(rate)

    def _on_speed_slider_changed(self, pct: int):
        """Speed slider moved."""
        if self._restoring:
            return
        self._push_undo()
        rate = pct / 100
        self._apply_speed(rate)

    def _on_stretch_dragged(self, h: float, v: float):
        self.player.surface.set_stretch(h, v)
        self._updating_stretch = True
        self.spn_stretch_h.setValue(int(h * 100))
        self.spn_stretch_v.setValue(int(v * 100))
        self._updating_stretch = False

    def _on_stretch_spinbox_changed(self):
        if self._updating_stretch:
            return
        h = self.spn_stretch_h.value() / 100.0
        v = self.spn_stretch_v.value() / 100.0
        self.player.surface.set_stretch(h, v)

    # ── File handling ─────────────────────────────────────────

    def _open_file(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open Video", "",
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv);;All Files (*)"
        )
        if paths:
            self._add_to_queue(paths)

    def _on_video_duration(self, ms: int):
        self._duration_s = ms / 1000.0
        self.automation.set_duration(ms)
        self._restoring = True
        if 0 <= self._queue_index < len(self._queue):
            item = self._queue[self._queue_index]
            was_full = item.trim_end_ms >= item.duration_ms
            item.duration_ms = ms
            item.duration_s = ms / 1000.0
            if was_full:
                item.trim_end_ms = ms
            self.trim.set_duration(ms)
            self.trim.set_trim(item.trim_start_ms, min(item.trim_end_ms, ms))
        else:
            self.trim.set_duration(ms)
        self._restoring = False

    def _on_playback_position(self, pos_ms: int):
        """Update trim playhead, enforce trim boundaries, apply speed automation."""
        self.trim.set_playhead(pos_ms)
        self.automation.set_playhead(pos_ms)

        if self.player.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            # Smooth automation: only update speed variable, never touch audio pipeline
            if self.automation.get_keyframes():
                auto_speed = self.automation.get_speed_at(pos_ms)
                if abs(auto_speed - self.player._speed) > 0.005:
                    self._apply_speed_live(auto_speed)

            start_ms, end_ms = self.trim.slider.get_selection()
            if end_ms < self.trim.slider._max and pos_ms > end_ms:
                self.player.player.pause()
                self.player.seek(end_ms)

    # ── Trim sync ─────────────────────────────────────────────

    def _on_automation_changed(self):
        """Speed automation keyframes were added/removed/moved."""
        self._push_undo()
        # Enable pitched audio if automation exists
        need_pitched = self.player._speed != 1.0 or bool(self.automation.get_keyframes())
        self.player.enable_pitched_audio(need_pitched)

    def _on_seek_requested(self, ms: int):
        """User clicked/dragged on the timeline to seek."""
        self.player.seek(ms)

    def _on_trim_changed(self, start_s: float, end_s: float):
        if self._restoring:
            return
        self._push_undo()
        pos_ms = self.player.player.position()
        start_ms = int(start_s * 1000)
        end_ms = int(end_s * 1000)
        # Always clamp playback inside the trimmed region
        if pos_ms < start_ms:
            self.player.seek(start_ms)
        elif pos_ms > end_ms:
            self.player.seek(end_ms)

    # ── Crop sync ─────────────────────────────────────────────

    def _on_overlay_crop_changed(self, x: int, y: int, w: int, h: int):
        """Overlay was moved/resized -> update spin boxes."""
        if self._restoring:
            return
        self._push_undo()
        self._updating_spinboxes = True
        self.spn_x.setValue(x)
        self.spn_y.setValue(y)
        self.spn_w.setValue(w)
        self.spn_h.setValue(h)
        self._updating_spinboxes = False

    def _on_spinbox_changed(self):
        """Spin boxes edited -> update overlay."""
        if self._updating_spinboxes:
            return
        self.crop_overlay.set_crop_from_video(
            self.spn_x.value(), self.spn_y.value(),
            self.spn_w.value(), self.spn_h.value()
        )

    def _apply_ratio(self):
        """Apply the current aspect ratio using the current crop mode."""
        if self._video_w == 0:
            return
        name = self.cmb_preset.currentText()
        mode = self.cmb_crop_mode.currentText()
        ratio = ASPECT_PRESETS.get(name)

        if mode == "Stretch" and ratio is not None:
            # Stretch to fit: reset crop to full, set stretch values
            self.crop_overlay.set_aspect_ratio(None)
            self._updating_spinboxes = True
            self.crop_overlay.reset()
            self._updating_spinboxes = False
            sh, sv = calc_stretch_to_fit(name, self._video_w, self._video_h)
            self._updating_stretch = True
            self.spn_stretch_h.setValue(int(sh * 100))
            self.spn_stretch_v.setValue(int(sv * 100))
            self._updating_stretch = False
            self.player.surface.set_stretch(sh, sv)
        elif ratio is not None:
            # Crop to fit: set crop, reset stretch
            self.crop_overlay.set_aspect_ratio(ratio)
            x, y, w, h = calc_preset_crop(name, self._video_w, self._video_h)
            self._updating_spinboxes = True
            self.spn_x.setValue(x)
            self.spn_y.setValue(y)
            self.spn_w.setValue(w)
            self.spn_h.setValue(h)
            self._updating_spinboxes = False
            self.crop_overlay.set_crop_from_video(x, y, w, h)
            self._updating_stretch = True
            self.spn_stretch_h.setValue(100)
            self.spn_stretch_v.setValue(100)
            self._updating_stretch = False
            self.player.surface.set_stretch(1.0, 1.0)
        else:
            # Free: unlock ratio, keep current values
            self.crop_overlay.set_aspect_ratio(None)

    def _on_preset_changed(self, name: str):
        self._push_undo()
        self._apply_ratio()

    def _on_crop_mode_changed(self, mode: str):
        self._push_undo()
        self._apply_ratio()

    def _on_crop_lock_toggled(self, locked: bool):
        self._push_undo()
        self.btn_lock_crop.setText("Unlock" if locked else "Lock")
        self.crop_overlay.set_locked(locked)
        self.cmb_preset.setEnabled(not locked)
        self.cmb_crop_mode.setEnabled(not locked)
        self.btn_reset_crop.setEnabled(not locked)
        for spn in (self.spn_x, self.spn_y, self.spn_w, self.spn_h):
            spn.setReadOnly(locked)

    def _reset_crop(self):
        self._push_undo()
        self.cmb_crop_mode.blockSignals(True)
        self.cmb_preset.blockSignals(True)
        self.cmb_crop_mode.setCurrentText("Crop")
        self.cmb_preset.setCurrentText("Free")
        self.cmb_crop_mode.blockSignals(False)
        self.cmb_preset.blockSignals(False)
        self.crop_overlay.set_aspect_ratio(None)
        self.crop_overlay.reset()
        self._updating_stretch = True
        self.spn_stretch_h.setValue(100)
        self.spn_stretch_v.setValue(100)
        self._updating_stretch = False
        self.player.surface.set_stretch(1.0, 1.0)

    # ── Export ────────────────────────────────────────────────

    def _export(self):
        if not self._video_path or self._worker is not None:
            return

        trim_start, trim_end = self.trim.get_trim_seconds()
        crop_x = self.spn_x.value()
        crop_y = self.spn_y.value()
        crop_w = self.spn_w.value()
        crop_h = self.spn_h.value()

        # Clamp crop to video frame (content pan can push crop out of bounds)
        if crop_x < 0:
            crop_w += crop_x  # shrink width by the overshoot
            crop_x = 0
        if crop_y < 0:
            crop_h += crop_y
            crop_y = 0
        if crop_x + crop_w > self._video_w:
            crop_w = self._video_w - crop_x
        if crop_y + crop_h > self._video_h:
            crop_h = self._video_h - crop_y

        # Ensure even dimensions
        crop_w -= crop_w % 2
        crop_h -= crop_h % 2

        if crop_w < 2 or crop_h < 2:
            QMessageBox.warning(self, "Invalid Crop",
                                "Crop width and height must be at least 2.")
            return

        # Skip crop filter if it matches full video (account for even-rounding)
        even_w = self._video_w - self._video_w % 2
        even_h = self._video_h - self._video_h % 2
        is_full_frame = (crop_x == 0 and crop_y == 0 and
                         crop_w >= even_w and crop_h >= even_h)

        speed = self.sld_speed.value() / 100.0
        stretch_h = self.spn_stretch_h.value() / 100.0
        stretch_v = self.spn_stretch_v.value() / 100.0

        suffix = self._settings.get("output_suffix", "_edited")
        self._last_output = get_output_path(self._video_path, suffix)
        if self._settings["output_dir"]:
            self._last_output = str(Path(self._settings["output_dir"]) / Path(self._last_output).name)

        if Path(self._last_output).exists():
            reply = QMessageBox.question(self, "Overwrite File?",
                f"Output file already exists:\n{Path(self._last_output).name}\n\nOverwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        keyframes = self.automation.get_keyframes()
        trim_start_ms = int(trim_start * 1000)
        trim_end_ms = int(trim_end * 1000)

        if (trim_end - trim_start) <= 0:
            QMessageBox.warning(self, "Invalid Trim",
                                "Trim duration must be greater than zero.")
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.btn_export.setEnabled(False)

        if keyframes:
            # Automated export: pre-render audio + segmented video
            self._worker = ExportWorker(auto_kwargs=dict(
                input_path=self._video_path,
                output_path=self._last_output,
                keyframes=keyframes,
                base_speed=speed,
                trim_start_ms=trim_start_ms,
                trim_end_ms=trim_end_ms,
                crop_x=None if is_full_frame else crop_x,
                crop_y=None if is_full_frame else crop_y,
                crop_w=None if is_full_frame else crop_w,
                crop_h=None if is_full_frame else crop_h,
                codec=self._settings["codec"],
                crf=self._settings["crf"],
                stretch_h=stretch_h,
                stretch_v=stretch_v,
                audio_mode=self._settings.get("audio_mode", "copy"),
            ))
        else:
            # Simple export: single speed
            cmd = build_command(
                self._video_path, self._last_output,
                trim_start=trim_start if trim_start > 0 else None,
                trim_end=trim_end if trim_end < self._duration_s else None,
                crop_x=None if is_full_frame else crop_x,
                crop_y=None if is_full_frame else crop_y,
                crop_w=None if is_full_frame else crop_w,
                crop_h=None if is_full_frame else crop_h,
                codec=self._settings["codec"],
                crf=self._settings["crf"],
                speed=speed,
                stretch_h=stretch_h,
                stretch_v=stretch_v,
                audio_mode=self._settings.get("audio_mode", "copy"),
            )
            duration = (trim_end - trim_start) / speed
            self._worker = ExportWorker(cmd=cmd, duration=duration)

        self._worker.progress.connect(lambda p: self.progress_bar.setValue(int(p)))
        self._worker.finished.connect(self._on_export_done)
        self._worker.start()

    def _on_export_done(self, success: bool):
        self.progress_bar.setVisible(False)
        self.btn_export.setEnabled(True)
        error_msg = ""
        if self._worker is not None:
            error_msg = self._worker._error_msg
            self._worker.wait()
            self._worker = None
        if success:
            archive_status = self._archive_original_clip()
            QMessageBox.information(self, "Export Complete",
                                    f"Saved to:\n{self._last_output}{archive_status}")
            if self._settings.get("auto_advance", False):
                self._go_next()
        else:
            msg = error_msg or "FFmpeg returned an error. Check the console for details."
            QMessageBox.warning(self, "Export Failed", msg)

    def _archive_original_clip(self) -> str:
        """Move the just-exported source clip into the archive folder.

        Returns a string to append to the success message (blank when disabled
        or skipped). The media player is temporarily unloaded so Windows
        releases the file handle — otherwise shutil.move fails with a sharing
        violation. The player is reloaded from the new location afterward so
        navigation back/forward through the queue still works.
        """
        if not self._settings.get("move_originals_to_archive"):
            return ""
        archive_dir = self._settings.get("untrimmed_archive_dir", "").strip()
        if not archive_dir:
            return "\n(Archive skipped: no archive folder configured.)"
        if not self._video_path:
            return ""

        source = Path(self._video_path)
        try:
            # Release the QMediaPlayer file handle before touching the file.
            self.player.player.setSource(QUrl())
            QApplication.processEvents()

            new_path = archive_original(source, Path(archive_dir))
        except Exception as e:
            # Reload the original so the player isn't left empty.
            try:
                self.player.load(str(source))
            except Exception:
                pass
            return f"\nArchive failed: {e}"

        new_path_str = str(new_path)
        if new_path_str == str(source):
            # No-op (source was already inside the archive). Reload player.
            self.player.load(new_path_str)
            return ""

        if 0 <= self._queue_index < len(self._queue):
            self._queue[self._queue_index].path = new_path_str
        self._video_path = new_path_str
        self.player.load(new_path_str)
        return f"\nOriginal archived to:\n{new_path_str}"
