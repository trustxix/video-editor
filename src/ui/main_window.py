import json
import math
import threading
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QSpinBox, QDoubleSpinBox, QComboBox, QPushButton, QFileDialog,
    QProgressBar, QMessageBox, QApplication, QLineEdit, QSlider,
    QStackedWidget,
)
from src.ui.widgets import ClickSlider
from src.ui.player_mode import PlayerMode
from PyQt6.QtCore import Qt, QByteArray, QEvent, QEventLoop, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtMultimedia import QMediaPlayer

from src.core.paths import get_config_dir
from src.core.video_item import VideoItem
from src.core.archive import archive_original
from src.core.keybinds import (
    ACTION_DEFS, PLAYER_ACTION_DEFS, KeybindManager,
    keybind_from_key_event, keybind_from_mouse_event,
)
from src.ui.video_player import VideoPlayer, VideoSurface
from src.ui.crop_overlay import CropOverlay
from src.ui.trim_controls import TrimControls, RangeSlider
from src.ui.settings_dialog import SettingsDialog
from src.ui.automation_lane import AutomationLane
from src.ui.themes import apply_theme, is_dark_theme, DEFAULT_THEME
from src.core.presets import ASPECT_PRESETS, calc_preset_crop, calc_stretch_to_fit
from src.core.ffmpeg_runner import (
    build_command, get_output_path, get_video_duration, safe_output_path,
    get_video_fps, get_video_resolution, loudnorm_analyze, run_export,
    export_with_automation,
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


class LoudnormWorker(QThread):
    """Runs loudnorm first-pass analysis in a background thread.

    Cancellation is cooperative: the underlying ffmpeg subprocess can't be
    killed mid-flight (loudnorm_analyze uses subprocess.run with a 120s
    timeout), but flagging cancel discards the result so the export aborts
    cleanly. The orphan ffmpeg process exits within its own timeout. Net
    effect: the UI never freezes for more than the time it takes Qt to
    process the cancel click."""
    result_ready = pyqtSignal(object)  # dict or None

    def __init__(self, path, trim_start, trim_duration, target_lufs):
        super().__init__()
        self.path = path
        self.trim_start = trim_start
        self.trim_duration = trim_duration
        self.target_lufs = target_lufs
        self._cancelled = False

    def run(self):
        try:
            r = loudnorm_analyze(self.path, self.trim_start, self.trim_duration,
                                 target_lufs=self.target_lufs)
        except Exception:
            r = None
        if self._cancelled:
            r = None
        self.result_ready.emit(r)

    def cancel(self):
        self._cancelled = True


class FrameStepWorker(QThread):
    """Single-pending-slot background frame extractor for snappy frame
    stepping. Newer requests overwrite the pending slot, so a user holding
    the right-arrow key never queues up a backlog of stale subprocess
    invocations.

    Each request carries (video_path, ms). The worker lives for the
    lifetime of MainWindow; emit `frame_ready(path, ms, bmp_bytes)` so the
    receiver can drop results from old clips."""
    frame_ready = pyqtSignal(str, int, bytes)  # path, ms, BMP bytes

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._pending: tuple[str, int] | None = None
        self._stop = False
        self._gen = 0

    def request(self, video_path: str, ms: int):
        with self._cv:
            self._pending = (video_path, ms)
            self._gen += 1
            self._cv.notify_all()

    def stop_worker(self):
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        self.wait(1500)

    def run(self):
        from src.core.ffmpeg_runner import extract_frame
        while True:
            with self._cv:
                while self._pending is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                path, ms = self._pending
                gen = self._gen
                self._pending = None
            try:
                bmp = extract_frame(path, ms / 1000.0)
            except Exception:
                bmp = None
            # Drop the result if a newer request landed mid-extract.
            with self._cv:
                if gen != self._gen or self._stop:
                    continue
            if bmp:
                self.frame_ready.emit(path, ms, bmp)


class ColorPreviewWorker(QThread):
    """Single-pending-slot worker for the color-preview frame extract.

    Takes (video_path, ms, brightness, exposure) per request. Reduces the
    UI freeze that the synchronous subprocess.run was introducing on every
    color-slider release."""
    preview_ready = pyqtSignal(str, int, bytes)  # path, ms, BMP bytes

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._pending: tuple[str, int, float, float] | None = None
        self._stop = False
        self._gen = 0

    def request(self, video_path: str, ms: int, brightness: float, exposure: float):
        with self._cv:
            self._pending = (video_path, ms, brightness, exposure)
            self._gen += 1
            self._cv.notify_all()

    def stop_worker(self):
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        self.wait(2500)

    def run(self):
        import subprocess
        from src.core.ffmpeg_runner import _build_color_filters, get_ffmpeg, _hide_window
        while True:
            with self._cv:
                while self._pending is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                path, ms, b, e = self._pending
                gen = self._gen
                self._pending = None
            filters = _build_color_filters(b, e)
            if not filters:
                continue
            cmd = [
                get_ffmpeg(), '-loglevel', 'quiet',
                '-ss', f'{ms / 1000.0:.3f}',
                '-i', path,
                '-vf', ','.join(filters),
                '-frames:v', '1',
                '-f', 'image2pipe', '-vcodec', 'bmp',
                'pipe:1',
            ]
            bmp = b""
            try:
                result = subprocess.run(
                    cmd, capture_output=True, startupinfo=_hide_window(), timeout=3,
                )
                if result.returncode == 0 and result.stdout:
                    bmp = result.stdout
            except Exception:
                bmp = b""
            with self._cv:
                if gen != self._gen or self._stop:
                    continue
            if bmp:
                self.preview_ready.emit(path, ms, bmp)


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

        # Batch export state. _batch_queue is a list of queue indices to
        # process; _batch_index is how far we've gotten; _batch_results is
        # parallel to _batch_queue with 'ok' / 'failed' per clip, used for
        # the summary at the end. None when no batch is running.
        self._batch_queue: list[int] | None = None
        self._batch_index: int = 0
        self._batch_results: list[tuple[str, bool]] = []
        self._batch_return_to: int = -1  # where to navigate when done
        self._settings_path = get_config_dir() / "settings.json"
        self._settings = self._load_settings()
        apply_theme(self._settings.get("theme", DEFAULT_THEME),
                    scale=self._settings.get("ui_scale", 90) / 100.0)
        # Stay-on-top is applied *after* the window has been shown — see
        # the QTimer.singleShot at the end of __init__. Calling winId()
        # here during __init__ (which _apply_stay_on_top does) would
        # force the native HWND to be realized before _setup_ui()
        # creates any child widgets, and the topmost bit wouldn't stick
        # through Qt's first ShowWindow pass.

        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._export_cancelled = False
        self._stepped_pos: int | None = None  # accurate position during frame stepping
        # Long-lived background workers (lazy-init on first use; stopped in
        # closeEvent). Single-pending-slot pattern keeps rapid input from
        # queueing stale subprocess invocations.
        self._frame_step_worker: FrameStepWorker | None = None
        self._color_preview_worker: ColorPreviewWorker | None = None

        self._editor_keybind_manager = KeybindManager(
            ACTION_DEFS, self._settings.get("editor_keybinds"))
        self._player_keybind_manager = KeybindManager(
            PLAYER_ACTION_DEFS, self._settings.get("player_keybinds"))

        self._setup_ui()
        self._setup_menu()
        self._build_action_handlers()
        self._build_player_action_handlers()

        # Status bar — persistent clip info (resolution, fps, duration)
        self._status_label = QLabel("Drop a video file to begin")
        self.statusBar().addPermanentWidget(self._status_label, stretch=1)

        # Restore saved window geometry (position, size, maximized state).
        import base64
        geo_str = self._settings.get("window_geometry", "")
        if geo_str:
            try:
                self.restoreGeometry(QByteArray(base64.b64decode(geo_str)))
                # Ensure window is on a visible screen (handles monitor disconnect)
                center = self.frameGeometry().center()
                if not any(s.geometry().contains(center) for s in QApplication.screens()):
                    primary = QApplication.primaryScreen()
                    if primary:
                        self.move(primary.availableGeometry().topLeft())
            except Exception:
                pass

        QApplication.instance().installEventFilter(self)

        # Apply stay-on-top after the event loop has processed show().
        # QTimer.singleShot(0, ...) runs the callback on the next event
        # loop iteration, by which point main.py has already called
        # window.show() and Qt has finished its first ShowWindow pass.
        # Doing this here (instead of in __init__) is what makes the
        # topmost bit actually stick on a cold launch.
        QTimer.singleShot(0, lambda: self._apply_stay_on_top(
            self._settings.get("stay_on_top", False)
        ))
        QTimer.singleShot(0, lambda: self._apply_dark_title_bar(
            is_dark_theme(self._settings.get("theme", DEFAULT_THEME))
        ))

    def _setup_menu(self):
        self._menu_actions: dict[str, object] = {}
        menu = self.menuBar()

        fm = menu.addMenu("&File")
        self._menu_actions["open_file"] = fm.addAction("&Open Video...", self._open_file)
        fm.addSeparator()
        self._menu_actions["export_current"] = fm.addAction("&Export Current", self._export)
        self._menu_actions["export_all"] = fm.addAction("Export &List", self._start_batch_export)
        self._menu_actions["show_in_explorer"] = fm.addAction("Show in E&xplorer", self._show_in_explorer)
        fm.addSeparator()
        self._menu_actions["close_clip"] = fm.addAction("&Close Current Clip", self._remove_current_clip)
        self._menu_actions["clear_queue"] = fm.addAction("Clear Qu&eue", self._clear_queue)
        fm.addSeparator()
        self._menu_actions["settings"] = fm.addAction("&Settings...", self._open_settings)
        fm.addSeparator()
        self._menu_actions["quit"] = fm.addAction("E&xit", self.close)

        em = menu.addMenu("&Edit")
        self._menu_actions["undo"] = em.addAction("&Undo", self._undo)
        self._menu_actions["redo"] = em.addAction("&Redo", self._redo)

        vm = menu.addMenu("&View")
        self._act_mode_editor = vm.addAction("&Editor", lambda: self._switch_mode("editor"))
        self._act_mode_player = vm.addAction("&Player", lambda: self._switch_mode("player"))
        vm.addSeparator()
        self._menu_actions["fullscreen"] = vm.addAction("&Fullscreen", self._toggle_fullscreen)

        self._sync_menu_shortcuts()

    def _build_action_handlers(self):
        """Map action IDs to callables. Called once after _setup_ui."""
        self._action_handlers: dict[str, callable] = {
            "play_pause":          self._play_pause,
            "frame_step_forward":  lambda: self._frame_step(1),
            "frame_step_backward": lambda: self._frame_step(-1),
            "seek_forward_5s":     lambda: self._seek_relative(5000),
            "seek_backward_5s":    lambda: self._seek_relative(-5000),
            "queue_prev":          self._go_prev,
            "queue_next":          self._go_next,
            "open_file":           self._open_file,
            "export_current":      self._export,
            "export_all":          self._start_batch_export,
            "show_in_explorer":    self._show_in_explorer,
            "close_clip":          self._remove_current_clip,
            "clear_queue":         self._clear_queue,
            "undo":                self._undo,
            "redo":                self._redo,
            "settings":            self._open_settings,
            "mode_editor":         lambda: self._switch_mode("editor"),
            "mode_player":         lambda: self._switch_mode("player"),
            "fullscreen":          self._toggle_fullscreen,
            "quit":                self.close,
        }

    def _build_player_action_handlers(self):
        """Map player action IDs to callables on self.player_mode."""
        s = self._settings
        self._player_action_handlers: dict[str, callable] = {
            "p_play_pause":      lambda: self.player_mode._toggle_play(),
            "p_seek_fwd":        lambda: self.player_mode.seek_relative(
                                     s.get("player_seek_step", 5) * 1000),
            "p_seek_back":       lambda: self.player_mode.seek_relative(
                                     -s.get("player_seek_step", 5) * 1000),
            "p_seek_fwd_large":  lambda: self.player_mode.seek_relative(
                                     s.get("player_seek_step_large", 30) * 1000),
            "p_seek_back_large": lambda: self.player_mode.seek_relative(
                                     -s.get("player_seek_step_large", 30) * 1000),
            "p_frame_fwd":       lambda: self.player_mode.frame_step(1),
            "p_frame_back":      lambda: self.player_mode.frame_step(-1),
            "p_speed_up":        lambda: self.player_mode.adjust_speed(0.25),
            "p_speed_down":      lambda: self.player_mode.adjust_speed(-0.25),
            "p_speed_reset":     lambda: self.player_mode.reset_speed(),
            "p_volume_up":       lambda: self.player_mode.adjust_volume(5),
            "p_volume_down":     lambda: self.player_mode.adjust_volume(-5),
            "p_mute":            lambda: self.player_mode.toggle_mute(),
            "p_next_file":       lambda: self.player_mode._go_next(),
            "p_prev_file":       lambda: self.player_mode._go_prev(),
            "p_jump_start":      lambda: self.player_mode.jump_to_start(),
            "p_jump_end":        lambda: self.player_mode.jump_to_end(),
            "p_goto_time":       lambda: self.player_mode.goto_timestamp(),
            "p_loop_cycle":      lambda: self.player_mode.cycle_loop_mode(),
            "p_ab_mark":         lambda: self.player_mode.ab_mark(),
            "p_shuffle":         lambda: self.player_mode.toggle_shuffle(),
            "p_screenshot":      lambda: self.player_mode.take_screenshot(),
            "p_copy_path":       lambda: self.player_mode.copy_path(),
            "p_open_external":   lambda: self.player_mode.open_external(),
            "p_open_folder":     lambda: self.player_mode.open_folder_dialog(),
            "p_refresh":         lambda: self.player_mode.refresh_file_list(),
            "p_aspect_cycle":    lambda: self.player_mode.cycle_aspect(),
            "p_compact":         lambda: self.player_mode.toggle_compact(),
            "p_fit_window":      self._fit_window_to_video,
            "p_fullscreen":      self._toggle_fullscreen,
            "p_mode_editor":     lambda: self._switch_mode("editor"),
            "p_quit":            self.close,
        }

    def _sync_menu_shortcuts(self):
        """Update menu shortcut display labels from the keybind manager."""
        for aid, action in self._menu_actions.items():
            display = self._editor_keybind_manager.get_menu_shortcut(aid)
            action.setShortcut(QKeySequence(display) if display else QKeySequence())

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        # ── Editor page ───────────────────────────────────────
        editor_page = QWidget()
        root = QVBoxLayout(editor_page)
        self._stack.addWidget(editor_page)

        # ── Player page ───────────────────────────────────────
        self.player_mode = PlayerMode()
        self.player_mode._settings = self._settings
        self.player_mode.send_to_editor.connect(self._on_send_to_editor)
        self.player_mode.fullscreen_changed.connect(self._on_fullscreen_changed)
        self.player_mode.title_changed.connect(self._on_player_title_changed)
        self.player_mode.volume_changed.connect(self._on_player_volume_changed)
        self.player_mode.set_volume(self._settings.get("player_volume", 100))
        # Set OSD duration and extensions from settings
        self.player_mode.surface.set_osd_duration(
            self._settings.get("player_osd_duration", 1500))
        ext_str = self._settings.get("player_extensions", "")
        if ext_str:
            self.player_mode._file_proxy.set_extensions(ext_str)
        self.player_mode._recent_dirs = list(
            self._settings.get("player_recent_dirs", []))
        # Determine start directory based on setting
        start_mode = self._settings.get("player_start_dir_mode", "last")
        if start_mode == "specific":
            start_dir = self._settings.get("player_start_dir_path", "")
        elif start_mode == "home":
            start_dir = str(Path.home())
        else:  # "last"
            start_dir = self._settings.get("player_last_dir", "")
        if start_dir and Path(start_dir).is_dir():
            self.player_mode.navigate_to(start_dir)
        else:
            self.player_mode.navigate_to(str(Path.home()))
        self._stack.addWidget(self.player_mode)

        # ── Queue navigation bar ──────────────────────────────
        nav = QHBoxLayout()
        self.btn_prev = QPushButton("\u25C0 Prev")
        self.btn_prev.setFixedWidth(70)
        self.btn_prev.clicked.connect(self._go_prev)
        nav.addWidget(self.btn_prev)

        self.lbl_queue = QLabel("")
        self.lbl_queue.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # No hardcoded font-size — inherit from the app font so
        # Windows DPI scaling and theme changes apply cleanly.
        nav.addWidget(self.lbl_queue, stretch=1)

        self.btn_next = QPushButton("Next \u25B6")
        self.btn_next.setFixedWidth(70)
        self.btn_next.clicked.connect(self._go_next)
        nav.addWidget(self.btn_next)

        # Small separator so Remove/Clear don't look like navigation buttons.
        nav.addSpacing(12)

        self.btn_remove = QPushButton("\u2715 Remove")
        self.btn_remove.setFixedWidth(90)
        self.btn_remove.setToolTip("Remove the current clip from the queue (Ctrl+W)")
        self.btn_remove.clicked.connect(self._remove_current_clip)
        nav.addWidget(self.btn_remove)

        self.btn_clear_queue = QPushButton("Clear All")
        self.btn_clear_queue.setFixedWidth(80)
        self.btn_clear_queue.setToolTip("Remove all clips from the queue")
        self.btn_clear_queue.clicked.connect(self._clear_queue)
        nav.addWidget(self.btn_clear_queue)

        self.nav_widget = QWidget()
        self.nav_widget.setLayout(nav)
        self.nav_widget.setVisible(False)
        root.addWidget(self.nav_widget)

        # ── Video area ─────────────────────────────────────────
        self.player = VideoPlayer()
        self.player.set_volume(self._settings.get("preview_volume", 100))
        self.player.volume_changed.connect(self._on_preview_volume_changed)
        self.crop_overlay = CropOverlay(self.player.surface)
        self.crop_overlay.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        root.addWidget(self.player, stretch=1)

        # ── Trim controls ─────────────────────────────────────
        self.trim = TrimControls()
        root.addWidget(self.trim)

        # ── Speed automation lane ─────────────────────────────
        self.automation = AutomationLane()
        self.automation.changed.connect(self._on_automation_changed)
        self.automation.focus_taken.connect(lambda: self._on_widget_focus("automation"))
        self.trim.slider.focus_taken.connect(lambda: self._on_widget_focus("trim"))
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

        # Bottom bar stretch factors: crop/effects/color all grow with
        # the window; export (just buttons) stays compact on the right.
        # QGroupBox defaults to sizeHint-only sizing, so without explicit
        # stretch here the extra width never reaches the sliders inside.
        bottom.addWidget(crop_group, stretch=2)

        # Effects
        effects_group = QGroupBox("Effects")
        efx = QHBoxLayout(effects_group)

        self._semitones = 0

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

        self.sld_speed = ClickSlider(Qt.Orientation.Horizontal)
        self.sld_speed.setRange(25, 200)  # 25% to 200%
        self.sld_speed.setValue(100)
        self.sld_speed.setMinimumWidth(120)  # floor, not ceiling — grows with window
        self.sld_speed.valueChanged.connect(self._on_speed_slider_changed)
        efx.addWidget(self.sld_speed, stretch=1)

        self.lbl_speed = QLabel("100%")
        self.lbl_speed.setFixedWidth(40)
        self.lbl_speed.setStyleSheet("font-family: monospace;")
        efx.addWidget(self.lbl_speed)

        # ── Stretch (collapsible inside Effects) ──────────────
        self.btn_toggle_stretch = QPushButton("Stretch \u25B6")
        self.btn_toggle_stretch.setFixedWidth(70)
        self.btn_toggle_stretch.setCheckable(True)
        efx.addWidget(self.btn_toggle_stretch)

        self.stretch_panel = QWidget()
        sp = QHBoxLayout(self.stretch_panel)
        sp.setContentsMargins(0, 0, 0, 0)
        self.spn_stretch_h = QSpinBox()
        self.spn_stretch_h.setPrefix("H:")
        self.spn_stretch_h.setSuffix("%")
        self.spn_stretch_h.setRange(10, 500)
        self.spn_stretch_h.setValue(100)
        self.spn_stretch_h.setSingleStep(10)
        sp.addWidget(self.spn_stretch_h)
        self.spn_stretch_v = QSpinBox()
        self.spn_stretch_v.setPrefix("V:")
        self.spn_stretch_v.setSuffix("%")
        self.spn_stretch_v.setRange(10, 500)
        self.spn_stretch_v.setValue(100)
        self.spn_stretch_v.setSingleStep(10)
        sp.addWidget(self.spn_stretch_v)
        self.stretch_panel.setVisible(False)
        efx.addWidget(self.stretch_panel)

        self.btn_toggle_stretch.toggled.connect(lambda on: (
            self.stretch_panel.setVisible(on),
            self.btn_toggle_stretch.setText("Stretch \u25BC" if on else "Stretch \u25B6"),
        ))

        bottom.addWidget(effects_group, stretch=2)

        # ── Adjustments (collapsible: brightness, exposure, more later)
        adj_group = QGroupBox("Adjustments")
        ag = QVBoxLayout(adj_group)

        norm_row = QHBoxLayout()
        self.btn_normalize = QPushButton("Normalize Audio")
        self.btn_normalize.setCheckable(True)
        self.btn_normalize.setEnabled(False)
        self.btn_normalize.setToolTip("EBU R128 loudness normalization on export")
        self.btn_normalize.toggled.connect(self._on_normalize_toggled)
        norm_row.addWidget(self.btn_normalize)

        self.spn_lufs = QDoubleSpinBox()
        self.spn_lufs.setRange(-50.0, 0.0)
        self.spn_lufs.setValue(self._settings.get("normalize_lufs", -14.0))
        self.spn_lufs.setSingleStep(1.0)
        self.spn_lufs.setDecimals(1)
        self.spn_lufs.setSuffix(" LUFS")
        self.spn_lufs.setFixedWidth(100)
        self.spn_lufs.setToolTip(
            "Target loudness: -14 = YouTube/Spotify, -16 = Apple, -23 = broadcast"
        )
        self.spn_lufs.valueChanged.connect(self._on_lufs_changed)
        norm_row.addWidget(self.spn_lufs)
        ag.addLayout(norm_row)

        self.btn_toggle_adj = QPushButton("Brightness / Exposure \u25B6")
        self.btn_toggle_adj.setCheckable(True)
        ag.addWidget(self.btn_toggle_adj)

        self.adj_panel = QWidget()
        ap = QVBoxLayout(self.adj_panel)
        ap.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        row.addWidget(QLabel("Brightness:"))
        self.sld_brightness = ClickSlider(Qt.Orientation.Horizontal)
        self.sld_brightness.setRange(-100, 100)
        self.sld_brightness.setValue(0)
        self.sld_brightness.setToolTip("Preview is approximate; export is precise (ffmpeg eq)")
        self.sld_brightness.valueChanged.connect(self._on_brightness_changed)
        self.sld_brightness.sliderReleased.connect(self._on_color_slider_released)
        row.addWidget(self.sld_brightness, stretch=1)
        self.lbl_brightness = QLabel("0")
        self.lbl_brightness.setFixedWidth(35)
        self.lbl_brightness.setStyleSheet("font-family: monospace;")
        row.addWidget(self.lbl_brightness)
        ap.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Exposure:"))
        self.sld_exposure = ClickSlider(Qt.Orientation.Horizontal)
        self.sld_exposure.setRange(-100, 100)
        self.sld_exposure.setValue(0)
        self.sld_exposure.setToolTip("Photographic stops (-3 to +3). Export is precise (ffmpeg exposure)")
        self.sld_exposure.valueChanged.connect(self._on_exposure_changed)
        self.sld_exposure.sliderReleased.connect(self._on_color_slider_released)
        row.addWidget(self.sld_exposure, stretch=1)
        self.lbl_exposure = QLabel("0")
        self.lbl_exposure.setFixedWidth(35)
        self.lbl_exposure.setStyleSheet("font-family: monospace;")
        row.addWidget(self.lbl_exposure)
        ap.addLayout(row)

        row = QHBoxLayout()
        row.addStretch()
        self.btn_reset_color = QPushButton("Reset")
        self.btn_reset_color.setFixedWidth(70)
        self.btn_reset_color.clicked.connect(self._reset_color)
        row.addWidget(self.btn_reset_color)
        ap.addLayout(row)

        self.adj_panel.setVisible(False)
        ag.addWidget(self.adj_panel)

        self.btn_toggle_adj.toggled.connect(lambda on: (
            self.adj_panel.setVisible(on),
            self.btn_toggle_adj.setText(
                "Brightness / Exposure \u25BC" if on else "Brightness / Exposure \u25B6"
            ),
        ))

        bottom.addWidget(adj_group, stretch=2)

        export_group = QGroupBox("Export")
        eg = QVBoxLayout(export_group)
        self.btn_export = QPushButton("Export")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._export)
        eg.addWidget(self.btn_export)

        self.btn_export_list = QPushButton("Add to Export List")
        self.btn_export_list.setCheckable(True)
        self.btn_export_list.setEnabled(False)
        self.btn_export_list.setToolTip("Toggle whether this clip is included in batch export")
        self.btn_export_list.toggled.connect(self._on_export_list_toggled)
        eg.addWidget(self.btn_export_list)

        self.btn_export_batch = QPushButton("Export List")
        self.btn_export_batch.setEnabled(False)
        self.btn_export_batch.clicked.connect(self._start_batch_export)
        eg.addWidget(self.btn_export_batch)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        eg.addWidget(self.progress_bar)

        self.btn_cancel_export = QPushButton("Cancel")
        self.btn_cancel_export.setVisible(False)
        self.btn_cancel_export.clicked.connect(self._cancel_export)
        eg.addWidget(self.btn_cancel_export)

        bottom.addWidget(export_group)
        root.addLayout(bottom)

        # ── Signals ───────────────────────────────────────────
        self.player.duration_changed.connect(self._on_video_duration)
        self.player.position_changed.connect(self._on_playback_position)
        self.player.player.playbackStateChanged.connect(self._on_playback_state)
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
        self._stepped_pos = None
        self.player.surface._stepping = False
        if 0 <= self._queue_index < len(self._queue):
            self._save_current_state()

        old_index = self._queue_index
        self._queue_index = index
        item = self._queue[index]

        if not item.probed:
            try:
                item.video_w, item.video_h = get_video_resolution(item.path)
                item.duration_s = get_video_duration(item.path)
                item.fps = get_video_fps(item.path)
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
                if self._queue:
                    self._queue_index = -1  # reset before navigating
                    self._navigate_to(max(0, min(old_index, len(self._queue) - 1)))
                else:
                    self._unload_all_clips()
                return
            if item.video_w == 0 or item.video_h == 0:
                QMessageBox.warning(self, "Invalid Video",
                    f"Could not read video dimensions for:\n{Path(item.path).name}")
                self._queue.pop(index)
                if self._queue:
                    self._queue_index = -1
                    self._navigate_to(max(0, min(old_index, len(self._queue) - 1)))
                else:
                    self._unload_all_clips()
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
        self.btn_export_list.setEnabled(True)
        self.btn_normalize.setEnabled(True)
        self._sync_export_list_button()
        self._sync_normalize_button()
        self._update_nav()
        self._update_status_bar()

    def _save_current_state(self):
        # Guard for post-unload state: if no clip is active, saving is
        # a no-op. Without this, any UI signal that fires during or after
        # a queue-clear could crash with IndexError.
        if not (0 <= self._queue_index < len(self._queue)):
            return
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
        item.brightness = self.sld_brightness.value() * self._BRIGHTNESS_SCALE
        item.exposure = self.sld_exposure.value() * self._EXPOSURE_SCALE
        item.pan_x = self.player.surface._pan_x
        item.pan_y = self.player.surface._pan_y
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
        self.player.surface._pan_x = item.pan_x
        self.player.surface._pan_y = item.pan_y

        # Restore color adjustments. Slider signals are silenced by the
        # outer self._restoring = True guard at the top of this method,
        # so setting the values here won't trigger _on_brightness_changed.
        self.sld_brightness.blockSignals(True)
        self.sld_exposure.blockSignals(True)
        self.sld_brightness.setValue(int(round(item.brightness / self._BRIGHTNESS_SCALE)))
        self.sld_exposure.setValue(int(round(item.exposure / self._EXPOSURE_SCALE)))
        self.sld_brightness.blockSignals(False)
        self.sld_exposure.blockSignals(False)
        self.lbl_brightness.setText(f"{self.sld_brightness.value():+d}" if self.sld_brightness.value() else "0")
        self.lbl_exposure.setText(f"{self.sld_exposure.value():+d}" if self.sld_exposure.value() else "0")
        self.player.surface.set_color_adjust(item.brightness, item.exposure)

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

    def _remove_current_clip(self):
        """Remove the currently-viewed clip from the queue.

        If there are more clips, auto-advance to the next one (or the
        previous one if we removed the tail). If the queue is empty
        after removal, drop into the no-clip-loaded state so the user
        can add more without restarting.

        Disabled mid-export — removing the clip being exported would
        cause all sorts of chaos with the worker thread.
        """
        if self._worker is not None or self._batch_queue is not None:
            return
        if not (0 <= self._queue_index < len(self._queue)):
            return

        idx = self._queue_index

        # Release the QMediaPlayer file handle before removing. Not
        # strictly required (we're not deleting the file) but it avoids
        # any lingering Windows sharing locks and matches the pattern
        # we use in _archive_original_clip.
        self.player.player.setSource(QUrl())
        QApplication.processEvents()

        # Mark the slot as invalid *before* popping so that if anything
        # reacts to the pop via a signal and tries to read the current
        # item, _save_current_state's guard short-circuits safely.
        self._queue_index = -1
        self._queue.pop(idx)

        if not self._queue:
            self._unload_all_clips()
            return

        # Pick a reasonable replacement: the clip that took over the
        # removed index, or the last clip if we just removed the tail.
        new_idx = min(idx, len(self._queue) - 1)
        self._navigate_to(new_idx)

    def _unload_all_clips(self):
        """Empty the queue and reset the UI to the no-clip-loaded state."""
        self.player.release()
        self._queue.clear()
        self._queue_index = -1
        self._video_path = None
        self._video_w = 0
        self._video_h = 0
        self._duration_s = 0.0
        self._last_output = ""
        self.btn_export.setEnabled(False)
        self.btn_export_list.setEnabled(False)
        self.btn_export_list.setChecked(False)
        self.btn_export_batch.setEnabled(False)
        self.btn_normalize.setEnabled(False)
        self.btn_normalize.setChecked(False)
        # Clear any stale editing state so it doesn't leak into the next
        # clip the user drops in.
        self.automation.clear()
        self.automation.set_duration(1)
        self.sld_brightness.blockSignals(True)
        self.sld_exposure.blockSignals(True)
        self.sld_brightness.setValue(0)
        self.sld_exposure.setValue(0)
        self.sld_brightness.blockSignals(False)
        self.sld_exposure.blockSignals(False)
        self.lbl_brightness.setText("0")
        self.lbl_exposure.setText("0")
        self.player.surface.set_color_adjust(0.0, 0.0)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.progress_bar.setVisible(False)
        self._update_nav()
        self._update_status_bar()

    def _clear_queue(self):
        """Remove every clip from the queue (with confirmation)."""
        if self._worker is not None or self._batch_queue is not None:
            return
        if not self._queue:
            return
        reply = QMessageBox.question(
            self, "Clear Queue",
            f"Remove all {len(self._queue)} clip(s) from the queue?\n\n"
            "This does not delete any files on disk — it only unloads them "
            "from the editor.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._unload_all_clips()

    def _update_nav(self):
        count = len(self._queue)
        # Show the nav bar whenever there's at least one clip, so the
        # Remove / Clear All buttons are always reachable. Prev/Next
        # disable themselves when there's only one clip.
        self.nav_widget.setVisible(count >= 1)
        has_current = 0 <= self._queue_index < count
        if has_current:
            name = Path(self._queue[self._queue_index].path).name
            if count > 1:
                self.lbl_queue.setText(
                    f"Video {self._queue_index + 1} of {count}  \u2014  {name}"
                )
            else:
                self.lbl_queue.setText(name)
            self.setWindowTitle(f"Video Editor \u2014 {name}")
        else:
            self.lbl_queue.setText("")
            self.setWindowTitle("Video Editor")
        self.btn_prev.setEnabled(has_current and self._queue_index > 0)
        self.btn_next.setEnabled(has_current and self._queue_index < count - 1)
        self.btn_remove.setEnabled(has_current)
        self.btn_clear_queue.setEnabled(count >= 1)

    def closeEvent(self, event):
        # Persist window geometry and player directory
        import base64 as _b64
        try:
            self._settings["window_geometry"] = _b64.b64encode(
                bytes(self.saveGeometry())).decode()
            self._settings["player_last_dir"] = self.player_mode.get_current_directory()
            self._save_settings()
        except Exception:
            pass

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
        # Stop long-lived background workers cleanly so their daemon ffmpeg
        # children don't outlive pythonw as orphans.
        if self._frame_step_worker is not None:
            self._frame_step_worker.stop_worker()
            self._frame_step_worker = None
        if self._color_preview_worker is not None:
            self._color_preview_worker.stop_worker()
            self._color_preview_worker = None
        self.player.release()
        self.player_mode.release()
        super().closeEvent(event)

    # ── Unified keybind dispatch ─────────────────────────────

    # Mouse-interactive widget types — mouse button keybinds are
    # NOT dispatched when the event target is one of these, so
    # crop dragging, trim handles, etc. keep working normally.
    _MOUSE_INTERACTIVE = (
        CropOverlay, AutomationLane, RangeSlider, VideoSurface,
        QSlider, ClickSlider, QPushButton, QComboBox, QSpinBox, QDoubleSpinBox,
        QLineEdit,
    )

    def eventFilter(self, obj, event):
        # Don't intercept during modal dialogs (settings, message boxes)
        if QApplication.activeModalWidget():
            return super().eventFilter(obj, event)

        etype = event.type()
        kb = None
        is_repeat = False

        if etype == QEvent.Type.KeyPress:
            # Escape exits fullscreen before anything else
            if event.key() == Qt.Key.Key_Escape and self.player_mode.is_fullscreen:
                self.player_mode.toggle_fullscreen()
                return True
            # Skip when focus is on a text-entry widget
            focused = QApplication.instance().focusWidget()
            if isinstance(focused, (QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox)):
                return super().eventFilter(obj, event)
            # Let arrow/delete keys through to selection-aware widgets that
            # have an active selection. Space always stays with the keybind
            # system so play/pause works regardless of what's selected.
            key = event.key()
            _PASSTHROUGH_KEYS = (Qt.Key.Key_Left, Qt.Key.Key_Right,
                                 Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Delete)
            if key in _PASSTHROUGH_KEYS and isinstance(focused, (AutomationLane, RangeSlider)):
                if hasattr(focused, '_selected_idx') and focused._selected_idx >= 0:
                    return super().eventFilter(obj, event)
                if hasattr(focused, '_selected') and focused._selected is not None:
                    return super().eventFilter(obj, event)
            is_repeat = event.isAutoRepeat()
            kb = keybind_from_key_event(event.modifiers(), event.key())

        elif etype == QEvent.Type.MouseButtonPress:
            btn = event.button()
            # Mouse4/5 (Back/Forward) are safe to intercept globally —
            # no Qt widget uses them. Other buttons only fire keybinds
            # when the target is NOT a mouse-interactive widget.
            safe = btn in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton)
            if not safe and isinstance(obj, self._MOUSE_INTERACTIVE):
                return super().eventFilter(obj, event)
            kb = keybind_from_mouse_event(event.modifiers(), btn)

        if kb is None:
            return super().eventFilter(obj, event)

        # Mode-aware dispatch: editor (index 0) vs player (index 1)
        if self._stack.currentIndex() == 1:
            manager = self._player_keybind_manager
            handlers = self._player_action_handlers
            action_defs = PLAYER_ACTION_DEFS
        else:
            manager = self._editor_keybind_manager
            handlers = self._action_handlers
            action_defs = ACTION_DEFS

        action_ids = manager.lookup(kb)
        if not action_ids:
            return super().eventFilter(obj, event)

        # Always consume matched keybinds — even when auto-repeat is
        # suppressed — so the event never leaks to QAction shortcuts
        # (which don't respect our allow_repeat rules).
        for aid in action_ids:
            if is_repeat and not action_defs[aid].allow_repeat:
                continue
            handler = handlers.get(aid)
            if handler:
                handler()

        return True

    def _frame_step(self, direction: int):
        """Step one frame forward (1) or backward (-1) with ffmpeg accuracy.

        QMediaPlayer.setPosition() on Windows snaps to the nearest keyframe
        (GOP can be 30-120 frames apart). For precise trim-point selection
        we need the exact frame. FFmpeg's -ss decodes forward from the
        previous keyframe, giving frame-accurate output.

        The decoded BMP is shown on VideoSurface directly. A _stepping
        flag prevents QVideoSink from overwriting it. When playback
        resumes, _on_playback_state syncs QMediaPlayer to our position.
        """
        if not self._video_path:
            return
        if self.player.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.player.pause()

        item = self._queue[self._queue_index] if 0 <= self._queue_index < len(self._queue) else None
        fps = item.fps if item and item.fps > 0 else 30.0
        frame_ms = max(1, int(round(1000 / fps)))

        # Use the stepped position if already stepping, else QMediaPlayer's
        base_ms = self._stepped_pos if self._stepped_pos is not None else self.player.player.position()
        new_ms = max(0, base_ms + direction * frame_ms)
        # Clamp to trim zone — frame stepping must not escape the trim region
        start_ms, end_ms = self.trim.slider.get_selection()
        new_ms = max(start_ms, min(new_ms, end_ms))

        # Update playhead immediately for responsiveness; the actual frame
        # arrives via the worker thread (single-pending-slot, so a held
        # arrow key never queues stale subprocess invocations).
        self._stepped_pos = new_ms
        self.player.lbl_time.setText(
            f"{self.player._fmt(new_ms)} / {self.player._fmt(self.player._duration_ms)}"
        )
        self.trim.set_playhead(new_ms)
        self.automation.set_playhead(new_ms)
        if self._frame_step_worker is None:
            self._frame_step_worker = FrameStepWorker()
            self._frame_step_worker.frame_ready.connect(self._on_frame_step_ready)
            self._frame_step_worker.start()
        self._frame_step_worker.request(self._video_path, new_ms)
        # Frame arrives via _on_frame_step_ready when extract finishes.

    def _on_frame_step_ready(self, path: str, ms: int, bmp: bytes):
        """Receives a stepped frame from FrameStepWorker. Drops stale results
        whose path or position no longer matches what's loaded."""
        if path != self._video_path:
            return  # User switched clips
        if ms != self._stepped_pos:
            return  # Newer step request superseded this one
        from PyQt6.QtGui import QImage
        image = QImage()
        if image.loadFromData(bmp, "BMP"):
            self.player.surface._stepping = True
            self.player.surface._image = image
            self.player.surface.update()

    def _on_color_preview_ready(self, path: str, ms: int, bmp: bytes):
        """Receives a color-corrected preview frame from ColorPreviewWorker."""
        if path != self._video_path:
            return
        from PyQt6.QtGui import QImage
        img = QImage()
        if img.loadFromData(bmp, "BMP"):
            self.player.surface._stepping = True
            self.player.surface._image = img
            self.player.surface.set_color_adjust(0, 0)  # filters baked in
            self.player.surface.update()

    def _seek_relative(self, delta_ms: int):
        """Seek forward or backward by delta_ms. Clears stepped state."""
        if not self._video_path:
            return
        self._stepped_pos = None
        self.player.surface._stepping = False
        base = self.player.player.position()
        self.player.seek(max(0, base + delta_ms))

    def _on_playback_state(self, state):
        """Sync QMediaPlayer to the accurate stepped position when play resumes."""
        if state == QMediaPlayer.PlaybackState.PlayingState and self._stepped_pos is not None:
            self.player.player.setPosition(self._stepped_pos)
            self._stepped_pos = None
            self.player.surface._stepping = False

    # ── Mode switching ─────────────────────────────────────────

    def _switch_mode(self, mode: str):
        """Switch between 'editor' and 'player' modes."""
        if mode == "player":
            # Pause the editor's player before switching
            if self.player.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.player.pause()
            self._stack.setCurrentIndex(1)
            # Update title for player mode
            if self.player_mode._current_path:
                self._on_player_title_changed(
                    Path(self.player_mode._current_path).name)
        else:
            # Pause the player mode's player before switching
            if self.player_mode.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player_mode.player.pause()
            self._stack.setCurrentIndex(0)
            # Restore editor title
            self._update_nav()

    def _toggle_fullscreen(self):
        """Toggle fullscreen — only active in player mode."""
        if self._stack.currentIndex() != 1:
            # Switch to player mode first, then go fullscreen
            self._switch_mode("player")
        self.player_mode.toggle_fullscreen()

    def _on_fullscreen_changed(self, is_fs: bool):
        """Show/hide main window chrome for fullscreen."""
        self.menuBar().setVisible(not is_fs)
        if is_fs:
            self.showFullScreen()
        else:
            self.showNormal()

    def _fit_window_to_video(self):
        """Resize window to match native video dimensions."""
        size = self.player_mode.get_video_size()
        if size:
            vw, vh = size
            # Add some padding for controls/chrome
            self.resize(max(640, vw), max(480, vh + 100))

    def _on_player_title_changed(self, filename: str):
        """Update window title when a video is loaded in player mode."""
        if self._stack.currentIndex() == 1:
            self.setWindowTitle(f"Video Editor \u2014 {filename} \u2014 Player")

    def _on_player_volume_changed(self, value: int):
        """Persist player volume separately from editor volume (debounced)."""
        self._settings["player_volume"] = max(0, min(100, value))
        # Debounce: avoid hammering disk on held volume keys.
        # Save after 500ms of no further changes.
        if not hasattr(self, '_vol_save_timer'):
            self._vol_save_timer = QTimer(self)
            self._vol_save_timer.setSingleShot(True)
            self._vol_save_timer.setInterval(500)
            self._vol_save_timer.timeout.connect(self._save_settings)
        self._vol_save_timer.start()

    def _on_send_to_editor(self, paths: list[str]):
        """Receive files from the player mode and load them in the editor."""
        # Pause the player mode
        if self.player_mode.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player_mode.player.pause()
        self._switch_mode("editor")
        self._add_to_queue(paths)

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
            "preview_volume": 100,
            "normalize_audio": False,
            "normalize_lufs": -14.0,
            "ui_scale": 90,
            "editor_keybinds": {},
            "player_keybinds": {},
            "player_last_dir": "",
            # Player settings
            "player_click": "play_pause",
            "player_double_click": "fullscreen",
            "player_wheel": "seek",
            "player_seek_step": 5,
            "player_seek_step_large": 30,
            "player_auto_advance": True,
            "player_cursor_hide": True,
            "player_cursor_hide_delay": 3000,
            "player_controls_autohide": True,
            "player_controls_autohide_delay": 3000,
            "player_osd": True,
            "player_osd_duration": 1500,
            "player_start_dir_mode": "last",
            "player_start_dir_path": "",
            "player_remember_positions": False,
            "player_extensions": ".mp4,.mkv,.avi,.mov,.webm,.flv,.wmv",
            "player_volume": 100,
        }
        try:
            data = json.loads(self._settings_path.read_text())
            # Migrate old "keybinds" key → "editor_keybinds"
            if "keybinds" in data and "editor_keybinds" not in data:
                data["editor_keybinds"] = data.pop("keybinds")
            elif "keybinds" in data:
                data.pop("keybinds", None)
            # Versioned settings migration (adds defaults for new release fields).
            from src.core.settings_migration import migrate
            data = migrate(data)
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
        try:
            defaults["preview_volume"] = max(0, min(100, int(defaults["preview_volume"])))
        except (ValueError, TypeError):
            defaults["preview_volume"] = 100
        # normalize_lufs flows directly into ffmpeg's loudnorm filter via
        # f"{target_lufs:.1f}" — bound it to a sane range so a hand-edited
        # settings.json with NaN / inf / a huge number can't produce a
        # filter string that ffmpeg silently rejects (export with no audio).
        try:
            lufs = float(defaults["normalize_lufs"])
            if lufs != lufs or lufs < -50.0 or lufs > 0.0:  # NaN check + range
                raise ValueError
            defaults["normalize_lufs"] = lufs
        except (ValueError, TypeError):
            defaults["normalize_lufs"] = -14.0
        # Validate player numeric settings — corrupt JSON must not crash
        _player_int_keys = {
            "player_seek_step": 5, "player_seek_step_large": 30,
            "player_volume": 100,
            "player_cursor_hide_delay": 3000,
            "player_controls_autohide_delay": 3000,
            "player_osd_duration": 1500,
        }
        for key, fallback in _player_int_keys.items():
            try:
                defaults[key] = int(defaults[key])
            except (ValueError, TypeError):
                defaults[key] = fallback
        _player_bool_keys = [
            "player_auto_advance", "player_cursor_hide",
            "player_controls_autohide", "player_osd",
            "player_remember_positions",
        ]
        for key in _player_bool_keys:
            if not isinstance(defaults.get(key), bool):
                defaults[key] = True if key != "player_remember_positions" else False
        return defaults

    def _save_settings(self):
        try:
            tmp = self._settings_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self._settings, indent=2, default=str))
            tmp.replace(self._settings_path)
        except (OSError, ValueError) as e:
            # Surface to log so we can see settings-save failures in bug reports
            # instead of silently dropping every preference change.
            try:
                from src.core.log_setup import log
                log().warning(f"Settings save failed: {e}")
            except Exception:
                pass  # last-resort — never raise from settings save

    def _on_preview_volume_changed(self, value: int):
        """Persist preview volume when the user releases the slider.

        Fires from VideoPlayer.volume_changed (sliderReleased), not on
        every valueChanged tick — so we rewrite settings.json once per
        drag, not 100 times. The live volume is already applied to the
        audio paths inside VideoPlayer; we're just persisting here.
        """
        self._settings["preview_volume"] = max(0, min(100, int(value)))
        self._save_settings()

    def _open_settings(self):
        dlg = SettingsDialog(self._settings, self)
        if dlg.exec():
            prev_theme = self._settings.get("theme", DEFAULT_THEME)
            prev_scale = self._settings.get("ui_scale", 90) / 100.0
            prev_on_top = self._settings.get("stay_on_top", False)
            # .update() preserves keys the dialog doesn't manage
            # (window_geometry, preview_volume, etc.)
            self._settings.update(dlg.get_settings())
            self._save_settings()
            # Reload keybind managers from updated settings
            self._editor_keybind_manager = KeybindManager(
                ACTION_DEFS, self._settings.get("editor_keybinds"))
            self._player_keybind_manager = KeybindManager(
                PLAYER_ACTION_DEFS, self._settings.get("player_keybinds"))
            self._sync_menu_shortcuts()
            self.spn_lufs.setValue(self._settings.get("normalize_lufs", -14.0))
            new_theme = self._settings.get("theme", DEFAULT_THEME)
            new_scale = self._settings.get("ui_scale", 90) / 100.0
            if new_theme != prev_theme or new_scale != prev_scale:
                apply_theme(new_theme, scale=new_scale)
                self._apply_dark_title_bar(is_dark_theme(new_theme))
            if self._settings.get("stay_on_top", False) != prev_on_top:
                self._apply_stay_on_top(self._settings["stay_on_top"])
            # Re-apply player settings stored in child widget state
            self.player_mode.surface.set_osd_duration(
                self._settings.get("player_osd_duration", 1500))
            ext_str = self._settings.get("player_extensions", "")
            if ext_str:
                self.player_mode._file_proxy.set_extensions(ext_str)
            self.player_mode.set_volume(
                self._settings.get("player_volume", 100))

    def _apply_stay_on_top(self, enabled: bool):
        """Toggle always-on-top via the native Win32 API.

        We can't use Qt's setWindowFlag(WindowStaysOnTopHint, ...) here:
        on Windows it destroys and recreates the HWND, which segfaults
        the bound QMediaPlayer / QVideoSink pipeline (native crash, no
        traceback). SetWindowPos(HWND_TOPMOST) toggles the OS-level
        topmost bit directly without touching any Qt resources.

        Two gotchas this function carefully avoids:
          1. Without argtypes, ctypes marshals Python ints as c_int
             (32-bit), silently truncating 64-bit HWNDs on x64 Python.
             Declare full argtypes via wintypes so the handle is passed
             pointer-sized.
          2. HWND_TOPMOST (-1) and HWND_NOTOPMOST (-2) are sentinel
             pseudo-HWNDs, not real handles — they must be marshaled in
             the HWND slot (c_void_p), not as signed ints, or the OS
             won't recognize them.
        """
        import ctypes
        from ctypes import wintypes
        import logging

        SWP_NOMOVE     = 0x0002
        SWP_NOSIZE     = 0x0001
        SWP_NOACTIVATE = 0x0010

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,   # hWnd
            wintypes.HWND,   # hWndInsertAfter (HWND_TOPMOST is a sentinel HWND)
            ctypes.c_int,    # X
            ctypes.c_int,    # Y
            ctypes.c_int,    # cx
            ctypes.c_int,    # cy
            wintypes.UINT,   # uFlags
        ]
        user32.SetWindowPos.restype = wintypes.BOOL

        # -1 and -2 must be boxed as HWND (pointer-sized), not passed
        # as Python ints — that's the sentinel-truncation trap.
        HWND_TOPMOST    = wintypes.HWND(-1)
        HWND_NOTOPMOST  = wintypes.HWND(-2)

        hwnd = wintypes.HWND(int(self.winId()))
        flag = HWND_TOPMOST if enabled else HWND_NOTOPMOST

        ok = user32.SetWindowPos(
            hwnd, flag, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
        if not ok:
            err = ctypes.get_last_error()
            logging.getLogger(__name__).warning(
                "SetWindowPos(topmost=%s) failed: GetLastError=%d hwnd=%d",
                enabled, err, int(self.winId()),
            )

    def _apply_dark_title_bar(self, dark: bool):
        """Match the Windows title bar to the app theme via DWM.

        Without this, a dark QSS theme still shows a bright white title
        bar — jarring on every dark theme except Light.
        """
        try:
            import ctypes
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            hwnd = int(self.winId())
            value = ctypes.c_int(1 if dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(value), ctypes.sizeof(value))
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
            'brightness': self.sld_brightness.value(),
            'exposure': self.sld_exposure.value(),
            'locked': self.btn_lock_crop.isChecked(),
            'keyframes': self.automation.get_keyframes(),
            'locked_keyframes': list(self.automation._locked_keyframes),
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

        # Color adjustments. self._restoring = True (set at the top of
        # this method) blocks the handler from firing, so we can set
        # values directly. The .get(..., 0) fallbacks keep this method
        # compatible with older undo entries captured before color was
        # added — they just treat the missing keys as "no change".
        self.sld_brightness.blockSignals(True)
        self.sld_exposure.blockSignals(True)
        self.sld_brightness.setValue(state.get('brightness', 0))
        self.sld_exposure.setValue(state.get('exposure', 0))
        self.sld_brightness.blockSignals(False)
        self.sld_exposure.blockSignals(False)
        self.lbl_brightness.setText(f"{self.sld_brightness.value():+d}" if self.sld_brightness.value() else "0")
        self.lbl_exposure.setText(f"{self.sld_exposure.value():+d}" if self.sld_exposure.value() else "0")
        self.player.surface.set_color_adjust(
            self.sld_brightness.value() * self._BRIGHTNESS_SCALE,
            self.sld_exposure.value() * self._EXPOSURE_SCALE,
        )

        self.btn_lock_crop.setChecked(state['locked'])

        self.automation.set_keyframes(state['keyframes'])
        self.automation._locked_keyframes = set(
            tuple(kf) for kf in state.get('locked_keyframes', [])
        )

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

    def _play_pause(self):
        """Play/pause with trim-zone awareness.

        If paused at or past the trim end, pressing play restarts from
        trim start — not from the beginning of the original clip.
        If paused before the trim start, jump to trim start first.
        """
        if self.player.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.player.pause()
            return
        start_ms, end_ms = self.trim.slider.get_selection()
        pos = self._stepped_pos if self._stepped_pos is not None else self.player.player.position()
        if pos >= end_ms or pos < start_ms:
            self.player.seek(start_ms)
        self.player._toggle_play()

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
        self._push_undo()
        self.player.surface.set_stretch(h, v)
        self._updating_stretch = True
        self.spn_stretch_h.setValue(int(h * 100))
        self.spn_stretch_v.setValue(int(v * 100))
        self._updating_stretch = False

    def _on_stretch_spinbox_changed(self):
        if self._updating_stretch:
            return
        self._push_undo()
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
        start_ms, end_ms = self.trim.slider.get_selection()

        # Clamp the visual playhead to the trim zone
        clamped = max(start_ms, min(pos_ms, end_ms))
        self.trim.set_playhead(clamped)
        self.automation.set_playhead(clamped)

        if self.player.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            # Smooth automation: only update speed variable, never touch audio pipeline
            if self.automation.get_keyframes():
                auto_speed = self.automation.get_speed_at(clamped)
                if abs(auto_speed - self.player._speed) > 0.005:
                    self._apply_speed_live(auto_speed)

            # Stop at trim end — always, not just when trim < clip duration
            if pos_ms >= end_ms:
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
        self._stepped_pos = None
        self.player.surface._stepping = False
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
        self._push_undo()
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

    # ── Color (brightness / exposure) ─────────────────────────

    # Slider↔filter mapping. Sliders are -100..100 (integer, with 0 as
    # "no change") which is the right feel for a drag UI. Internally we
    # store normalized floats on the VideoItem so the ffmpeg filter args
    # come out clean without us scattering /200 divisions everywhere.
    #   Brightness: slider [-100, 100] → eq.brightness [-0.5, 0.5]
    #   Exposure:   slider [-100, 100] → exposure.exposure [-3.0, 3.0] stops
    _BRIGHTNESS_SCALE = 0.005  # 100 slider units → 0.5 eq range
    _EXPOSURE_SCALE = 0.03     # 100 slider units → 3.0 stops

    def _on_brightness_changed(self, value: int):
        if self._restoring:
            return
        self.player.surface._stepping = False  # discard baked preview frame
        self._push_undo()
        self.lbl_brightness.setText(f"{value:+d}" if value else "0")
        self.player.surface.set_color_adjust(
            brightness=value * self._BRIGHTNESS_SCALE,
            exposure=self.sld_exposure.value() * self._EXPOSURE_SCALE,
        )

    def _on_exposure_changed(self, value: int):
        if self._restoring:
            return
        self.player.surface._stepping = False  # discard baked preview frame
        self._push_undo()
        self.lbl_exposure.setText(f"{value:+d}" if value else "0")
        self.player.surface.set_color_adjust(
            brightness=self.sld_brightness.value() * self._BRIGHTNESS_SCALE,
            exposure=value * self._EXPOSURE_SCALE,
        )

    def _on_color_slider_released(self):
        """Show accurate ffmpeg-rendered preview frame on slider release.

        The live drag preview is an alpha-blend approximation. On release,
        we extract the current frame through ffmpeg with the real eq and
        exposure filters applied, giving the user an exact preview of
        what the export will look like. Skipped during playback — the
        approximate overlay is good enough for moving video.
        """
        if not self._video_path:
            return
        if self.player.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            return
        b = self.sld_brightness.value() * self._BRIGHTNESS_SCALE
        e = self.sld_exposure.value() * self._EXPOSURE_SCALE
        if abs(b) < 1e-4 and abs(e) < 1e-4:
            # No adjustments — clear any stepped frame, let live video show
            self.player.surface._stepping = False
            return
        from src.core.ffmpeg_runner import _build_color_filters
        if not _build_color_filters(b, e):
            return
        # Run the preview extract on a background worker so the slider
        # release doesn't hold the UI for up to 3 seconds. The worker uses
        # the single-pending-slot pattern: rapid slider drags result in
        # only the latest preview being rendered.
        if self._color_preview_worker is None:
            self._color_preview_worker = ColorPreviewWorker()
            self._color_preview_worker.preview_ready.connect(self._on_color_preview_ready)
            self._color_preview_worker.start()
        ms = self.player.player.position()
        self._color_preview_worker.request(self._video_path, ms, b, e)

    def _reset_color(self):
        self._push_undo()
        # Block signals so we don't push two undo entries — this is
        # conceptually one "reset color" action.
        self.sld_brightness.blockSignals(True)
        self.sld_exposure.blockSignals(True)
        self.sld_brightness.setValue(0)
        self.sld_exposure.setValue(0)
        self.sld_brightness.blockSignals(False)
        self.sld_exposure.blockSignals(False)
        self.lbl_brightness.setText("0")
        self.lbl_exposure.setText("0")
        self.player.surface.set_color_adjust(brightness=0.0, exposure=0.0)
        self.player.surface._stepping = False  # clear accurate preview frame

    # ── Audio normalization ─────────────────────────────────

    def _on_normalize_toggled(self, checked: bool):
        if self._restoring:
            return
        if not (0 <= self._queue_index < len(self._queue)):
            return
        item = self._queue[self._queue_index]
        item.audio_normalize = checked
        self.btn_normalize.setText(
            "\u2713 Normalize Audio" if checked else "Normalize Audio"
        )
        # Clear cached analysis when toggled off so a fresh analysis
        # runs next time it's enabled and exported.
        if not checked:
            item.normalize_data = None

    def _on_lufs_changed(self, _value: float):
        """LUFS target changed — invalidate cached analysis for current clip."""
        if not (0 <= self._queue_index < len(self._queue)):
            return
        self._queue[self._queue_index].normalize_data = None

    def _sync_normalize_button(self):
        if 0 <= self._queue_index < len(self._queue):
            item = self._queue[self._queue_index]
            self.btn_normalize.blockSignals(True)
            self.btn_normalize.setChecked(item.audio_normalize)
            self.btn_normalize.blockSignals(False)
            self.btn_normalize.setText(
                "\u2713 Normalize Audio" if item.audio_normalize else "Normalize Audio"
            )

    # ── Selection coordination ─────────────────────────────────

    def _on_widget_focus(self, source: str):
        """One selection-aware widget grabbed focus — deselect the others."""
        if source != "automation":
            self.automation.deselect()
        if source != "trim":
            self.trim.slider.deselect()

    # ── Navigation helpers ────────────────────────────────────

    def _show_in_explorer(self):
        """Open Explorer with the last exported file highlighted."""
        if not self._last_output:
            return
        path = Path(self._last_output)
        if path.exists():
            import subprocess
            subprocess.Popen(['explorer', '/select,', str(path)])
        elif path.parent.exists():
            import os
            os.startfile(str(path.parent))

    def _update_status_bar(self):
        """Update the status bar with current clip info."""
        if not (0 <= self._queue_index < len(self._queue)):
            self._status_label.setText("Drop a video file to begin")
            return
        item = self._queue[self._queue_index]
        parts = [f"{item.video_w}\u00d7{item.video_h}"]
        if item.fps > 0:
            fps_str = f"{item.fps:.0f}" if item.fps == int(item.fps) else f"{item.fps:.1f}"
            parts.append(f"{fps_str} fps")
        if item.duration_s > 0:
            m = int(item.duration_s // 60)
            s = item.duration_s - m * 60
            parts.append(f"{m:02d}:{s:05.2f}")
        self._status_label.setText("  \u2502  ".join(parts))

    # ── Export list ────────────────────────────────────────────

    def _on_export_list_toggled(self, checked: bool):
        if not (0 <= self._queue_index < len(self._queue)):
            return
        self._queue[self._queue_index].export_listed = checked
        self._sync_export_list_button()

    def _sync_export_list_button(self):
        """Sync the export list toggle button with the current clip's state."""
        if 0 <= self._queue_index < len(self._queue):
            item = self._queue[self._queue_index]
            self.btn_export_list.blockSignals(True)
            self.btn_export_list.setChecked(item.export_listed)
            self.btn_export_list.blockSignals(False)
            self.btn_export_list.setText(
                "\u2713 On Export List" if item.export_listed else "Add to Export List"
            )
        # Enable batch button only if at least one clip is listed
        listed = sum(1 for i in self._queue if i.export_listed)
        self.btn_export_batch.setEnabled(listed > 0)
        self.btn_export_batch.setText(f"Export List ({listed})" if listed else "Export List")

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
            if self._batch_queue is not None:
                # Batch mode: record failure, skip this clip, continue.
                self._on_export_done(False)
                return
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
        brightness = self.sld_brightness.value() * self._BRIGHTNESS_SCALE
        exposure = self.sld_exposure.value() * self._EXPOSURE_SCALE

        suffix = self._settings.get("output_suffix", "_edited")
        if self._settings["output_dir"]:
            # Custom output dir → enforce containment to block traversal via suffix.
            self._last_output = safe_output_path(
                self._settings["output_dir"], self._video_path, suffix
            )
        else:
            self._last_output = get_output_path(self._video_path, suffix)

        if Path(self._last_output).exists() and self._batch_queue is None:
            # Batch exports auto-overwrite — the whole point is "walk away
            # and let it finish", and stopping for a dialog on every clip
            # would ruin that.
            reply = QMessageBox.question(self, "Overwrite File?",
                f"Output file already exists:\n{Path(self._last_output).name}\n\nOverwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        keyframes = self.automation.get_keyframes()
        trim_start_ms = int(trim_start * 1000)
        trim_end_ms = int(trim_end * 1000)

        if (trim_end - trim_start) <= 0:
            if self._batch_queue is not None:
                self._on_export_done(False)
                return
            QMessageBox.warning(self, "Invalid Trim",
                                "Trim duration must be greater than zero.")
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.btn_export.setEnabled(False)
        self.btn_cancel_export.setVisible(True)

        # Source fps — needed for fps normalization in the automation
        # filter_complex so slow/fast segments come out at a consistent
        # rate.
        current_item = self._queue[self._queue_index] if 0 <= self._queue_index < len(self._queue) else None
        source_fps = current_item.fps if current_item else 0.0

        # Audio normalization — run first-pass analysis if enabled and
        # not yet cached. This is synchronous (~1-5s) and blocks the UI,
        # but it's a prerequisite for the export command. The cached
        # result is reused on re-exports of the same clip.
        normalize_data = None
        target_lufs = self.spn_lufs.value()
        should_normalize = (
            (current_item and current_item.audio_normalize)
            or self._settings.get("normalize_audio", False)
        ) and self._settings.get("audio_mode", "copy") != "mute"
        if should_normalize and current_item:
            cached = current_item.normalize_data
            if cached is not None:
                # {} = analysis ran but failed (no audio stream) — skip
                normalize_data = cached if cached else None
            else:
                # Run loudnorm in a background thread with a Qt event loop
                # so the UI stays responsive (was a 2-minute freeze before).
                # Cancel button discards the result and aborts the export.
                self.progress_bar.setFormat("Analyzing audio loudness...")
                self.progress_bar.setRange(0, 0)  # indeterminate spinner
                ln_worker = LoudnormWorker(
                    self._video_path, trim_start, trim_end - trim_start, target_lufs,
                )
                ln_loop = QEventLoop()
                ln_holder: list = []

                def _on_ln_done(r):
                    ln_holder.append(r)
                    ln_loop.quit()

                def _on_ln_cancel():
                    self._export_cancelled = True
                    ln_worker.cancel()

                ln_worker.result_ready.connect(_on_ln_done)
                self.btn_cancel_export.clicked.connect(_on_ln_cancel)
                try:
                    ln_worker.start()
                    ln_loop.exec()
                finally:
                    self.btn_cancel_export.clicked.disconnect(_on_ln_cancel)
                    ln_worker.wait()
                    self.progress_bar.setRange(0, 100)
                    self.progress_bar.setFormat("%p%")

                if self._export_cancelled:
                    # User aborted — don't proceed with the export.
                    self._export_cancelled = False
                    self.progress_bar.setVisible(False)
                    self.btn_cancel_export.setVisible(False)
                    self.btn_export.setEnabled(True)
                    self._sync_export_list_button()
                    return

                result = ln_holder[0] if ln_holder else None
                # Cache result: dict with data on success, {} on failure
                current_item.normalize_data = result if result else {}
                normalize_data = result

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
                source_fps=source_fps,
                brightness=brightness,
                exposure=exposure,
                normalize_data=normalize_data,
                target_lufs=target_lufs,
                auto_preset=self._settings.get("auto_preset"),
            ))
        else:
            # Simple export: single speed
            cmd = build_command(
                self._video_path, self._last_output,
                trim_start=trim_start,
                trim_end=trim_end,
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
                brightness=brightness,
                exposure=exposure,
                normalize_data=normalize_data,
                target_lufs=target_lufs,
                auto_preset=self._settings.get("auto_preset"),
            )
            duration = (trim_end - trim_start) / speed
            self._worker = ExportWorker(cmd=cmd, duration=duration)

        self._worker.progress.connect(lambda p: self.progress_bar.setValue(int(p)))
        self._worker.finished.connect(self._on_export_done)
        self._worker.start()

    def _on_export_done(self, success: bool):
        self.progress_bar.setVisible(False)
        self.btn_cancel_export.setVisible(False)
        error_msg = ""
        if self._worker is not None:
            error_msg = self._worker._error_msg
            self._worker.wait()
            self._worker = None

        # User hit Cancel — absorb silently and restore UI.
        if self._export_cancelled:
            self._export_cancelled = False
            self._batch_queue = None
            self._batch_results = []
            self.btn_export.setEnabled(True)
            self._sync_export_list_button()
            return

        # Batch path: record the result, archive (silently), and advance
        # to the next clip without any popup dialogs. The whole point of
        # batch export is "walk away" — interrupting with a dialog for
        # each clip would defeat the purpose.
        if self._batch_queue is not None:
            clip_name = Path(self._video_path).name if self._video_path else "(unknown)"
            self._batch_results.append((clip_name, success))
            if success:
                self._archive_original_clip()
            self._batch_index += 1
            # Schedule the next clip on the next event loop tick so Qt
            # finishes cleaning up the finished worker before we start
            # the next one.
            QTimer.singleShot(50, self._export_next_in_batch)
            return

        # Single-clip path: original behavior.
        self.btn_export.setEnabled(True)
        self._sync_export_list_button()
        if success:
            archive_status = self._archive_original_clip()
            msg = QMessageBox(self)
            msg.setWindowTitle("Export Complete")
            msg.setText(f"Saved to:\n{self._last_output}{archive_status}")
            msg.setIcon(QMessageBox.Icon.Information)
            msg.addButton(QMessageBox.StandardButton.Ok)
            open_btn = msg.addButton("Show in Explorer", QMessageBox.ButtonRole.ActionRole)
            msg.exec()
            if msg.clickedButton() == open_btn:
                self._show_in_explorer()
            if self._settings.get("auto_advance", False):
                self._go_next()
        else:
            msg = error_msg or "FFmpeg returned an error. Check config/export_error.log for details."
            QMessageBox.warning(self, "Export Failed", msg)

    def _cancel_export(self):
        """Cancel the running export (and batch if active)."""
        self._export_cancelled = True
        if self._worker is not None:
            self._worker.cancel()

    # ── Batch export ──────────────────────────────────────────

    def _start_batch_export(self):
        if self._worker is not None or self._batch_queue is not None:
            return  # already exporting something

        # Flush current UI state into the active VideoItem
        if 0 <= self._queue_index < len(self._queue):
            self._save_current_state()

        listed = [i for i, item in enumerate(self._queue) if item.export_listed]
        if not listed:
            QMessageBox.information(
                self, "Nothing to Export",
                "No clips on the export list. Use \"Add to Export List\" on "
                "each clip you want to include in the batch.",
            )
            return

        reply = QMessageBox.question(
            self, "Export List",
            f"Export {len(listed)} clip(s)?\n\n"
            "The editor will cycle through each listed clip and export it. "
            "You can walk away.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._batch_queue = listed
        self._batch_index = 0
        self._batch_results = []
        self._batch_return_to = self._queue_index
        self.btn_export.setEnabled(False)
        self.btn_export_batch.setEnabled(False)
        self._export_next_in_batch()

    def _export_next_in_batch(self):
        if self._batch_queue is None:
            return

        if self._batch_index >= len(self._batch_queue):
            # Done — show summary and clean up
            results = self._batch_results
            self._batch_queue = None
            self._batch_results = []
            self.btn_export.setEnabled(True)
            self.btn_export_batch.setEnabled(True)
            self.progress_bar.setFormat("%p%")  # reset label
            self.progress_bar.setVisible(False)

            # Return to the clip the user was viewing before the batch
            if 0 <= self._batch_return_to < len(self._queue):
                self._navigate_to(self._batch_return_to)

            ok_count = sum(1 for _, ok in results if ok)
            fail_count = len(results) - ok_count
            if fail_count == 0:
                QMessageBox.information(
                    self, "Batch Export Complete",
                    f"Exported {ok_count} clip(s) successfully.",
                )
            else:
                failed = "\n".join(f"  {name}" for name, ok in results if not ok)
                QMessageBox.warning(
                    self, "Batch Export Finished With Errors",
                    f"Succeeded: {ok_count}\nFailed: {fail_count}\n\n"
                    f"Failed clips:\n{failed}\n\n"
                    "See config/export_error.log for ffmpeg details.",
                )
            return

        # Navigate to the next clip, then kick off its export. The UI's
        # progress bar label advertises the batch position.
        queue_idx = self._batch_queue[self._batch_index]
        self._navigate_to(queue_idx)
        # If navigation failed (file deleted, probe error), skip this clip
        if self._queue_index != queue_idx:
            clip_name = Path(self._queue[queue_idx].path).name if queue_idx < len(self._queue) else "(removed)"
            self._batch_results.append((clip_name, False))
            self._batch_index += 1
            QTimer.singleShot(50, self._export_next_in_batch)
            return
        total = len(self._batch_queue)
        current = self._batch_index + 1
        clip_name = Path(self._queue[queue_idx].path).name.replace('%', '%%')
        self.progress_bar.setFormat(f"[{current}/{total}] {clip_name} — %p%")
        self._export()

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
