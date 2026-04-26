"""Standalone video player mode with directory browser and playlist.

Self-contained — no imports from the editor UI modules (main_window,
crop_overlay, trim_controls, automation_lane). Shares only core modules
and the ClickSlider widget.

The PlayerMode widget is designed to be embedded in a tab or stacked
widget alongside the editor. It manages its own QMediaPlayer instance
so switching modes doesn't disrupt the editor's player state.
"""

import math
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeView, QListView,
    QPushButton, QLabel, QStyle, QAbstractItemView, QMenu, QFileDialog,
    QMessageBox, QApplication, QInputDialog, QComboBox,
)
from PyQt6.QtCore import (
    Qt, QUrl, QDir, QSortFilterProxyModel, QModelIndex, pyqtSignal, QTimer,
)
from PyQt6.QtGui import (
    QFileSystemModel, QPainter, QColor, QFont, QImage, QLinearGradient,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QVideoFrame

from src.ui.widgets import ClickSlider, CompactVolumeControl
from src.ui.seek_bar import SeekBar
from src.ui.thumbnail_worker import ThumbnailWorker, FirstFramePreloader
from src.core.ffmpeg_runner import probe_video, _probe_cache

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv'}


class _PlayerSurface(QWidget):
    """Video surface with OSD overlay. Renders QVideoSink frames with
    letterbox scaling, plus a fade-out on-screen display for status text."""

    clicked = pyqtSignal()        # single click (after double-click timeout)
    double_clicked = pyqtSignal()
    mouse_moved = pyqtSignal()    # any mouse movement (for fullscreen auto-hide)
    wheel_scrolled = pyqtSignal(int)  # delta in 120ths (positive = up)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self._image = None
        self.setMinimumSize(320, 180)
        self.setStyleSheet("background: black;")

        self.setMouseTracking(True)

        # Aspect ratio override: None=original, or (w, h) ratio
        self._aspect_override: tuple[int, int] | None = None

        # OSD state
        self._osd_text = ""
        self._osd_opacity = 0.0
        self._osd_duration = 1500
        self._osd_timer = QTimer(self)
        self._osd_timer.setInterval(50)
        self._osd_timer.timeout.connect(self._osd_tick)
        self._osd_primary = False  # large center icon flash mode

        # Loading spinner state
        self._loading = False
        self._spinner_angle = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(60)
        self._spinner_timer.timeout.connect(self._spinner_tick)

        # Title overlay (shown at top during fullscreen with controls)
        self._title_text = ""
        self._title_visible = False

        # Click detection (single vs double)
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(QApplication.doubleClickInterval())
        self._click_timer.timeout.connect(self.clicked.emit)

    OSD_FADE_IN_MS = 140
    OSD_FADE_OUT_MS = 380

    def show_osd(self, text: str, duration_ms: int | None = None,
                 primary: bool = False):
        """Show OSD text. Fades in over ~140ms, holds, fades out over
        the last ~380ms of `duration_ms`. `primary=True` uses a large
        centered chip (for play/pause/mute); otherwise corner toast."""
        self._osd_text = text
        self._osd_primary = primary
        self._osd_opacity = 0.0
        dur = duration_ms if duration_ms is not None else self._osd_duration
        self._osd_total = max(self.OSD_FADE_IN_MS + self.OSD_FADE_OUT_MS, dur)
        self._osd_fade_start = self._osd_total - self.OSD_FADE_OUT_MS
        self._osd_elapsed = 0
        self._osd_timer.start()
        self.update()

    def set_osd_duration(self, ms: int):
        self._osd_duration = ms

    def _osd_tick(self):
        self._osd_elapsed += 50
        if self._osd_elapsed >= self._osd_total:
            self._osd_timer.stop()
            self._osd_text = ""
            self._osd_opacity = 0.0
            self._osd_primary = False
        elif self._osd_elapsed < self.OSD_FADE_IN_MS:
            self._osd_opacity = self._osd_elapsed / self.OSD_FADE_IN_MS
        elif self._osd_elapsed >= self._osd_fade_start:
            remaining = self._osd_total - self._osd_elapsed
            self._osd_opacity = max(0.0, remaining / self.OSD_FADE_OUT_MS)
        else:
            self._osd_opacity = 1.0
        self.update()

    def _on_frame(self, frame: QVideoFrame):
        img = frame.toImage()
        if img.isNull():
            # Don't overwrite a placeholder/preload image with a null
            # frame — QVideoSink emits these briefly between setSource
            # calls, which would erase our preloaded first-frame.
            return
        self._image = img
        self.update()

    def set_placeholder_image(self, img: QImage | None) -> None:
        """Paint a placeholder image (e.g. preloaded first frame) until
        the next QVideoSink frame arrives. Pass None to clear."""
        if img is None or img.isNull():
            return
        self._image = img
        self.update()

    def set_loading(self, state: bool) -> None:
        """Show/hide the loading spinner overlay."""
        if state == self._loading:
            return
        self._loading = state
        if state:
            self._spinner_timer.start()
        else:
            self._spinner_timer.stop()
        self.update()

    def _spinner_tick(self):
        self._spinner_angle = (self._spinner_angle + 45) % 360
        self.update()

    def set_title(self, text: str) -> None:
        if text != self._title_text:
            self._title_text = text
            if self._title_visible:
                self.update()

    def set_title_visible(self, visible: bool) -> None:
        if visible == self._title_visible:
            return
        self._title_visible = visible
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._image and not self._image.isNull():
            iw, ih = self._image.width(), self._image.height()
            if self._aspect_override:
                # Force aspect ratio: compute virtual dimensions
                aw, ah = self._aspect_override
                # Scale image to fill the override aspect
                target_ratio = aw / ah
                image_ratio = iw / ih
                if image_ratio > target_ratio:
                    # Wider than target — crop sides (visually: letterbox top/bottom)
                    vh = ih
                    vw = int(ih * target_ratio)
                else:
                    vw = iw
                    vh = int(iw / target_ratio)
                scale = min(self.width() / vw, self.height() / vh)
                w, h = int(vw * scale), int(vh * scale)
            else:
                scale = min(self.width() / iw, self.height() / ih)
                w, h = int(iw * scale), int(ih * scale)
            x = (self.width() - w) // 2
            y = (self.height() - h) // 2
            if self._aspect_override:
                # Stretch to fill the computed rect (ignores original aspect)
                p.drawImage(x, y, self._image.scaled(
                    w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
            else:
                p.drawImage(x, y, self._image.scaled(
                    w, h, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))

        # Loading spinner overlay
        if self._loading:
            self._draw_spinner(p)

        # Title overlay (top fade with filename, fullscreen reveal)
        if self._title_visible and self._title_text:
            self._draw_title(p)

        # OSD overlay
        if self._osd_text and self._osd_opacity > 0:
            self._draw_osd(p)

        p.end()

    def _draw_spinner(self, p: QPainter) -> None:
        # Subtle vignette so the spinner reads on bright frames
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        cx, cy = self.width() // 2, self.height() // 2
        radius = 22
        dot_r = 4
        lead = (self._spinner_angle // 45) % 8
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(8):
            offset = (i - lead) % 8
            opacity = max(0.18, 1.0 - offset * 0.11)
            theta = math.radians(i * 45 - 90)
            x = int(cx + radius * math.cos(theta))
            y = int(cy + radius * math.sin(theta))
            p.setBrush(QColor(255, 255, 255, int(opacity * 235)))
            p.drawEllipse(QPoint(x, y), dot_r, dot_r)
        # "Loading..." caption
        p.setPen(QColor(220, 220, 220))
        font = QFont("sans-serif", 10)
        p.setFont(font)
        fm = p.fontMetrics()
        text = "Loading…"
        tw = fm.horizontalAdvance(text)
        p.drawText(cx - tw // 2, cy + radius + 24, text)

    def _draw_title(self, p: QPainter) -> None:
        # Top fade band with filename. Sized to ~56px tall.
        h = 56
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(0, 0, 0, 200))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, self.width(), h, grad)
        font = QFont("sans-serif", 12)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(245, 245, 245))
        p.drawText(
            16, 0, self.width() - 32, h,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self._title_text,
        )

    def _draw_osd(self, p: QPainter) -> None:
        p.save()
        p.setOpacity(self._osd_opacity)
        if self._osd_primary:
            # Large centered chip — used for primary actions like
            # play/pause/mute toggles. Rounded background + bold text.
            font = QFont("sans-serif", 22)
            font.setBold(True)
            p.setFont(font)
            fm = p.fontMetrics()
            text_w = fm.horizontalAdvance(self._osd_text)
            text_h = fm.height()
            pad_x, pad_y = 24, 14
            chip_w = text_w + pad_x * 2
            chip_h = text_h + pad_y * 2
            cx = (self.width() - chip_w) // 2
            cy = (self.height() - chip_h) // 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 200))
            p.drawRoundedRect(cx, cy, chip_w, chip_h, 10, 10)
            p.setPen(QColor(255, 255, 255))
            p.drawText(cx + pad_x, cy + pad_y + fm.ascent(), self._osd_text)
        else:
            # Corner toast — used for status updates (volume, seek, speed)
            font = QFont("sans-serif", 13)
            font.setBold(True)
            p.setFont(font)
            fm = p.fontMetrics()
            text_w = fm.horizontalAdvance(self._osd_text)
            text_h = fm.height()
            pad = 9
            ox, oy = 18, 18
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 195))
            p.drawRoundedRect(ox, oy, text_w + pad * 2, text_h + pad * 2,
                              6, 6)
            p.setPen(QColor(245, 245, 245))
            p.drawText(ox + pad, oy + pad + fm.ascent(), self._osd_text)
        p.restore()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Start single-click timer; double-click cancels it
            self._click_timer.start()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        self.mouse_moved.emit()
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        self.wheel_scrolled.emit(event.angleDelta().y())
        event.accept()


class _VideoFilter(QSortFilterProxyModel):
    """Proxy that shows only directories and video files in the file list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._extensions = set(VIDEO_EXTENSIONS)
        self._search_text = ""

    def set_extensions(self, ext_str: str):
        """Update extensions from comma-separated string like '.mp4,.mkv'."""
        self._extensions = {
            e.strip().lower() for e in ext_str.split(",") if e.strip()
        }
        self.invalidateFilter()

    def set_search_text(self, text: str):
        self._search_text = text.lower()
        self.invalidateFilter()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """Add tooltip with duration/resolution for video files (cache-only, non-blocking)."""
        if role == Qt.ItemDataRole.ToolTipRole:
            source_idx = self.mapToSource(index)
            model = self.sourceModel()
            if model and not model.isDir(source_idx):
                path = model.filePath(source_idx)
                if path:
                    # Only use cached probe data — never block UI for tooltips
                    import os as _os
                    try:
                        mtime = _os.path.getmtime(path)
                    except OSError:
                        return super().data(index, role)
                    info = _probe_cache.get((path, mtime))
                    if info:
                        parts = []
                        if info["width"] and info["height"]:
                            parts.append(f"{info['width']}x{info['height']}")
                        if info["duration"]:
                            m = int(info["duration"] // 60)
                            s = info["duration"] - m * 60
                            parts.append(f"{m}:{s:05.2f}")
                        if info["video_codec"]:
                            parts.append(info["video_codec"])
                        sz = info.get("file_size", 0)
                        if sz:
                            parts.append(f"{sz / (1024*1024):.1f} MB")
                        return " | ".join(parts) if parts else None
        return super().data(index, role)

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        model = self.sourceModel()
        idx = model.index(row, 0, parent)
        if model.isDir(idx):
            return True
        name = model.fileName(idx).lower()
        if not any(name.endswith(ext) for ext in self._extensions):
            return False
        if self._search_text and self._search_text not in name:
            return False
        return True


class PlayerMode(QWidget):
    """Full video player with directory browser, playlist, file management."""

    send_to_editor = pyqtSignal(list)  # list of file paths to load in editor
    fullscreen_changed = pyqtSignal(bool)  # emitted when fullscreen toggles
    title_changed = pyqtSignal(str)  # emitted with filename on video load
    volume_changed = pyqtSignal(int)  # emitted on volume slider release

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path: str | None = None
        self._playlist: list[str] = []
        self._playlist_index: int = -1
        self._fullscreen = False
        self._pre_mute_volume = 100
        self._current_speed = 1.0
        self._pre_fs_tree_vis = True
        self._pre_fs_list_vis = True
        self._loop_mode = 0  # 0=off, 1=single, 2=playlist
        self._shuffle = False
        self._loop_a_ms: int | None = None
        self._loop_b_ms: int | None = None
        self._recent_dirs: list[str] = []  # populated from settings
        # Settings refs (set by main_window after construction)
        self._settings: dict = {}
        self._setup_ui()
        self._setup_player()
        self._connect_signals()

        # Fullscreen auto-hide timers
        self._fs_controls_timer = QTimer(self)
        self._fs_controls_timer.setSingleShot(True)
        self._fs_controls_timer.timeout.connect(self._fs_hide_controls)
        self._fs_cursor_timer = QTimer(self)
        self._fs_cursor_timer.setSingleShot(True)
        self._fs_cursor_timer.timeout.connect(self._fs_hide_cursor)

    # ── UI setup ──────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._splitter = splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: directory tree ──────────────────────────────
        self._dir_model = QFileSystemModel()
        self._dir_model.setRootPath("")
        self._dir_model.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs)

        self._dir_tree = QTreeView()
        self._dir_tree.setModel(self._dir_model)
        self._dir_tree.setRootIndex(self._dir_model.index(""))
        # Only show the name column
        for col in range(1, self._dir_model.columnCount()):
            self._dir_tree.hideColumn(col)
        self._dir_tree.setHeaderHidden(True)
        self._dir_tree.setMinimumWidth(200)
        splitter.addWidget(self._dir_tree)

        # ── Center: video player ──────────────────────────────
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)

        self.surface = _PlayerSurface()
        self.surface.setAcceptDrops(True)
        self.surface.dragEnterEvent = self._surface_drag_enter
        self.surface.dropEvent = self._surface_drop
        cv.addWidget(self.surface, stretch=1)

        # Controls bar (wrapped in QWidget for fullscreen hide)
        self._controls_widget = QWidget()
        controls = QHBoxLayout(self._controls_widget)
        controls.setContentsMargins(0, 0, 0, 0)

        self.btn_play = QPushButton()
        self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.setFixedSize(36, 36)
        controls.addWidget(self.btn_play)

        self.lbl_playlist_pos = QLabel("")
        self.lbl_playlist_pos.setStyleSheet(
            "font-family: monospace; font-size: 8pt; color: #888; padding: 0 4px;")
        controls.addWidget(self.lbl_playlist_pos)

        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setStyleSheet("font-family: monospace;")
        controls.addWidget(self.lbl_time)

        # Speed label
        self.lbl_speed = QLabel("")
        self.lbl_speed.setStyleSheet(
            "font-family: monospace; font-size: 9pt; color: #aaa; padding: 0 4px;")
        self.lbl_speed.setFixedWidth(40)
        self.lbl_speed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls.addWidget(self.lbl_speed)

        # Seek bar with hover thumbnail preview + A-B markers + played fill
        self.seek_slider = SeekBar(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.setMouseTracking(True)
        self.seek_slider.installEventFilter(self)
        controls.addWidget(self.seek_slider, stretch=1)

        # Volume — compact control: clickable icon + slider that fades
        # in on hover. Container width is fixed so the seek bar never
        # reflows during the animation.
        self._vol_widget = CompactVolumeControl()
        self._vol_widget.set_icon_pixmap(
            self.style().standardIcon(
                QStyle.StandardPixmap.SP_MediaVolume).pixmap(16, 16)
        )
        # Aliases so existing code keeps working unchanged
        self.vol_slider = self._vol_widget.slider
        self._vol_icon = self._vol_widget.icon
        # Click on icon = toggle mute
        self._vol_widget.icon_clicked.connect(self.toggle_mute)
        controls.addWidget(self._vol_widget)

        cv.addWidget(self._controls_widget)

        # Nav bar (wrapped in QWidget for fullscreen hide)
        self._nav_widget = QWidget()
        nav = QHBoxLayout(self._nav_widget)
        nav.setContentsMargins(0, 0, 0, 0)

        self.btn_prev = QPushButton("\u25C0 Prev")
        self.btn_prev.setFixedWidth(70)
        nav.addWidget(self.btn_prev)

        self.lbl_filename = QLabel("")
        self.lbl_filename.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.lbl_filename, stretch=1)

        self.btn_next = QPushButton("Next \u25B6")
        self.btn_next.setFixedWidth(70)
        nav.addWidget(self.btn_next)

        nav.addSpacing(12)

        self.btn_send = QPushButton("Send to Editor")
        self.btn_send.setToolTip("Load this video in the editor for editing")
        self.btn_send.setEnabled(False)
        nav.addWidget(self.btn_send)

        nav.addSpacing(6)
        self._audio_track_combo = QComboBox()
        self._audio_track_combo.setFixedWidth(90)
        self._audio_track_combo.setVisible(False)
        self._audio_track_combo.setToolTip("Audio track")
        self._audio_track_combo.currentIndexChanged.connect(
            self._on_audio_track_changed)
        nav.addWidget(self._audio_track_combo)

        cv.addWidget(self._nav_widget)

        # File info bar
        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet("font-family: monospace; color: gray; padding: 2px 4px;")
        cv.addWidget(self.lbl_info)

        splitter.addWidget(center)

        # ── Right: file list ──────────────────────────────────
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)

        # Breadcrumb path bar
        self._breadcrumb = QLabel("")
        self._breadcrumb.setStyleSheet(
            "font-size: 8pt; color: #888; padding: 2px 4px;")
        self._breadcrumb.setWordWrap(True)
        self._breadcrumb.setMaximumHeight(36)
        rv.addWidget(self._breadcrumb)

        # Search bar
        from PyQt6.QtWidgets import QLineEdit as _QLE
        self._file_search = _QLE()
        self._file_search.setPlaceholderText("Filter files...")
        self._file_search.setClearButtonEnabled(True)
        rv.addWidget(self._file_search)

        # Sort combo
        sort_row = QHBoxLayout()
        sort_row.setContentsMargins(4, 0, 4, 0)
        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["Name", "Date", "Size"])
        self._sort_combo.setFixedHeight(24)
        sort_row.addWidget(self._sort_combo, stretch=1)
        self._btn_refresh = QPushButton("\u21bb")
        self._btn_refresh.setFixedSize(24, 24)
        self._btn_refresh.setToolTip("Refresh file list")
        sort_row.addWidget(self._btn_refresh)
        rv.addLayout(sort_row)

        self._file_model = QFileSystemModel()
        self._file_model.setRootPath("")
        self._file_model.setFilter(
            QDir.Filter.Files | QDir.Filter.NoDotAndDotDot
        )
        self._file_proxy = _VideoFilter()
        self._file_proxy.setSourceModel(self._file_model)

        self._file_list = QListView()
        self._file_list.setModel(self._file_proxy)
        self._file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        rv.addWidget(self._file_list)

        # File count label
        self.lbl_file_count = QLabel("")
        self.lbl_file_count.setStyleSheet("color: gray; padding: 2px 4px;")
        rv.addWidget(self.lbl_file_count)

        right.setMinimumWidth(180)
        splitter.addWidget(right)

        # Splitter proportions: 20% tree, 60% video, 20% file list
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 1)

        layout.addWidget(splitter)

    def _setup_player(self):
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoSink(self.surface.sink)
        self._duration_ms = 0
        self._seeking = False

        # Background thumbnail extractor for seek-bar hover preview
        self._thumb_worker = ThumbnailWorker(self)
        self._thumb_worker.start()
        self.seek_slider.set_thumbnail_worker(self._thumb_worker)

        # Background pre-extractor for the next playlist file's first frame
        self._preloader = FirstFramePreloader(self)
        self._preloader.ready.connect(self._on_preload_ready)
        self._preloader.start()
        self._preload_cache: dict[str, QImage] = {}  # path -> first frame
        self._preload_requested_for: str | None = None

    def _connect_signals(self):
        self._dir_tree.clicked.connect(self._on_dir_clicked)
        self._file_list.doubleClicked.connect(self._on_file_double_clicked)
        self._file_list.customContextMenuRequested.connect(self._on_file_context_menu)

        # Surface click/double-click (configurable behavior)
        self.surface.clicked.connect(self._on_surface_click)
        self.surface.double_clicked.connect(self._on_surface_double_click)
        self.surface.mouse_moved.connect(self._on_surface_mouse_move)
        self.surface.wheel_scrolled.connect(self._on_surface_wheel)

        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_prev.clicked.connect(self._go_prev)
        self.btn_next.clicked.connect(self._go_next)
        self.btn_send.clicked.connect(self._send_to_editor)

        self.seek_slider.sliderPressed.connect(lambda: setattr(self, '_seeking', True))
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        self.seek_slider.valueChanged.connect(self._on_seek_changed)
        self.vol_slider.valueChanged.connect(self._on_vol_slider_changed)
        self.vol_slider.sliderReleased.connect(
            lambda: self.volume_changed.emit(self.vol_slider.value()))

        self._file_search.textChanged.connect(self._file_proxy.set_search_text)
        self._sort_combo.currentTextChanged.connect(self._on_sort_changed)
        self._btn_refresh.clicked.connect(self.refresh_file_list)

        self.player.bufferProgressChanged.connect(self._on_buffer_progress)
        self.player.tracksChanged.connect(self._on_tracks_changed)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status)

    # ── Directory navigation ──────────────────────────────────

    def _navigate_to_dir(self, path: str):
        """Set the file list to show videos in the given directory."""
        idx = self._file_model.setRootPath(path)
        proxy_idx = self._file_proxy.mapFromSource(idx)
        self._file_list.setRootIndex(proxy_idx)
        self._rebuild_playlist(path)

        # Update breadcrumb
        self._breadcrumb.setText(path.replace("/", " > ").replace("\\", " > "))

        # Track recent directories (max 10, most recent first)
        if path in self._recent_dirs:
            self._recent_dirs.remove(path)
        self._recent_dirs.insert(0, path)
        self._recent_dirs = self._recent_dirs[:10]
        self._settings["player_recent_dirs"] = list(self._recent_dirs)

        # Expand the dir tree to this path
        dir_idx = self._dir_model.index(path)
        self._dir_tree.setCurrentIndex(dir_idx)
        self._dir_tree.scrollTo(dir_idx)
        # Expand parents
        parent = dir_idx.parent()
        while parent.isValid():
            self._dir_tree.expand(parent)
            parent = parent.parent()

    def _rebuild_playlist(self, directory: str):
        """Scan the directory for video files and build the playlist."""
        p = Path(directory)
        exts = self._file_proxy._extensions
        try:
            self._playlist = sorted(
                [str(f).replace("\\", "/") for f in p.iterdir()
                 if f.is_file() and f.suffix.lower() in exts],
                key=lambda x: x.lower(),
            )
        except OSError:
            self._playlist = []
        count = len(self._playlist)
        self.lbl_file_count.setText(f"{count} video{'s' if count != 1 else ''}")
        # Update playlist index if current file is in this directory
        if self._current_path and self._current_path in self._playlist:
            self._playlist_index = self._playlist.index(self._current_path)
        else:
            self._playlist_index = -1
        self._update_nav()

    def _on_dir_clicked(self, index: QModelIndex):
        path = self._dir_model.filePath(index)
        if path:
            self._navigate_to_dir(path)

    def eventFilter(self, obj, event):
        """Intercept wheel events on the seek bar for configurable step seek.
        Hover-time tooltip is now handled by SeekBar's thumbnail popup."""
        if obj is self.seek_slider:
            if event.type() == event.Type.Wheel and self._duration_ms > 0:
                delta = event.angleDelta().y()
                steps = delta // 120
                if steps:
                    step_s = self._settings.get("player_seek_step", 5)
                    self.seek_relative(steps * step_s * 1000)
                event.accept()
                return True
        return super().eventFilter(obj, event)

    # ── Surface click behavior ────────────────────────────────

    def _on_surface_click(self):
        action = self._settings.get("player_click", "play_pause")
        if action == "play_pause":
            self._toggle_play()

    def _on_surface_double_click(self):
        action = self._settings.get("player_double_click", "fullscreen")
        if action == "fullscreen":
            self.toggle_fullscreen()
        elif action == "play_pause":
            self._toggle_play()

    def _on_vol_slider_changed(self, v: int):
        self.audio.setVolume(v / 100.0)
        self._update_mute_icon()

    def _on_surface_wheel(self, delta: int):
        action = self._settings.get("player_wheel", "seek")
        steps = delta // 120
        if action == "seek":
            step_s = self._settings.get("player_seek_step", 5)
            self.seek_relative(steps * step_s * 1000)
        elif action == "volume":
            self.adjust_volume(steps * 5)

    def _on_sort_changed(self, text: str):
        col = {"Name": 0, "Date": 3, "Size": 1}.get(text, 0)
        self._file_model.sort(col, Qt.SortOrder.AscendingOrder)

    def _surface_drag_enter(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    ext = Path(url.toLocalFile()).suffix.lower()
                    if ext in self._file_proxy._extensions:
                        event.acceptProposedAction()
                        return
        event.ignore()

    def _surface_drop(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                ext = Path(path).suffix.lower()
                if ext in self._file_proxy._extensions:
                    self._load_video(path)
                    return

    # ── File list interaction ─────────────────────────────────

    def _on_file_double_clicked(self, proxy_index: QModelIndex):
        source_index = self._file_proxy.mapToSource(proxy_index)
        path = self._file_model.filePath(source_index)
        if path and not self._file_model.isDir(source_index):
            self._load_video(path)

    def _on_file_context_menu(self, pos):
        proxy_index = self._file_list.indexAt(pos)
        if not proxy_index.isValid():
            return
        source_index = self._file_proxy.mapToSource(proxy_index)
        path = self._file_model.filePath(source_index)
        if not path or self._file_model.isDir(source_index):
            return

        menu = QMenu(self)
        menu.addAction("Play", lambda: self._load_video(path))
        menu.addAction("Send to Editor", lambda: self.send_to_editor.emit([path]))
        menu.addSeparator()

        # Send selected files
        selected = self._get_selected_paths()
        if len(selected) > 1:
            menu.addAction(
                f"Send {len(selected)} files to Editor",
                lambda: self.send_to_editor.emit(selected),
            )
            menu.addSeparator()

        menu.addAction("Rename...", lambda: self._rename_file(path))
        menu.addAction("Move to...", lambda: self._move_file(path))
        menu.addAction("Copy Path", lambda: self._copy_file_path(path))
        menu.addAction("Open in Default App", lambda: self._open_in_default(path))
        menu.addAction("Show in Explorer", lambda: self._show_in_explorer(path))
        menu.addSeparator()
        menu.addAction("Delete File", lambda: self._delete_file(path))

        # Playlist reorder
        if path in self._playlist:
            pidx = self._playlist.index(path)
            if pidx > 0:
                menu.addAction("Move Up in Playlist", lambda: self._playlist_move(pidx, -1))
            if pidx < len(self._playlist) - 1:
                menu.addAction("Move Down in Playlist", lambda: self._playlist_move(pidx, 1))

        # Batch actions for multi-selection
        if len(selected) > 1:
            menu.addSeparator()
            menu.addAction(
                f"Delete {len(selected)} files",
                lambda: self._batch_delete(selected))
            menu.addAction(
                f"Move {len(selected)} files to...",
                lambda: self._batch_move(selected))

        menu.exec(self._file_list.viewport().mapToGlobal(pos))

    def _get_selected_paths(self) -> list[str]:
        paths = []
        for proxy_idx in self._file_list.selectionModel().selectedIndexes():
            source_idx = self._file_proxy.mapToSource(proxy_idx)
            p = self._file_model.filePath(source_idx)
            if p and not self._file_model.isDir(source_idx):
                paths.append(p)
        return paths

    def _show_in_explorer(self, path: str):
        import subprocess
        if Path(path).exists():
            subprocess.Popen(['explorer', '/select,', path],
                             creationflags=subprocess.CREATE_NO_WINDOW)

    def _delete_file(self, path: str):
        name = Path(path).name
        reply = QMessageBox.question(
            self, "Delete File",
            f"Permanently delete:\n{name}\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Release the player if this is the current file
        if self._current_path == path:
            self.player.stop()
            self.player.setSource(QUrl())
            QApplication.processEvents()
            self._current_path = None
            self._update_info()
        try:
            Path(path).unlink()
        except OSError as e:
            QMessageBox.warning(self, "Delete Failed", str(e))
            return
        # Rebuild playlist
        parent_dir = str(Path(path).parent)
        self._rebuild_playlist(parent_dir)

    def _rename_file(self, path: str):
        p = Path(path)
        stem = p.stem
        new_name, ok = QInputDialog.getText(
            self, "Rename File", "New name:", text=stem,
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        # Sanitize: remove path separators and reserved Windows characters
        for ch in r'\/:*?"<>|':
            new_name = new_name.replace(ch, "")
        if not new_name:
            return
        # Keep original extension
        new_path = p.parent / (new_name + p.suffix)
        if new_path.exists():
            QMessageBox.warning(self, "Rename Failed",
                                f"A file named '{new_path.name}' already exists.")
            return
        # Release player if this is the current file (Windows file handle)
        was_playing = self._current_path == path
        if was_playing:
            self.player.stop()
            self.player.setSource(QUrl())
            QApplication.processEvents()
        try:
            p.rename(new_path)
        except OSError as e:
            QMessageBox.warning(self, "Rename Failed", str(e))
            # Reload if we released
            if was_playing:
                self._load_video(path)
            return
        # Rebuild playlist and reload if it was playing
        parent_dir = str(p.parent)
        self._rebuild_playlist(parent_dir)
        if was_playing:
            self._load_video(str(new_path).replace("\\", "/"))

    def _move_file(self, path: str):
        dest = QFileDialog.getExistingDirectory(self, "Move to...")
        if not dest:
            return
        src = Path(path)
        dst = Path(dest) / src.name
        if dst.exists():
            QMessageBox.warning(self, "Move Failed",
                                f"'{src.name}' already exists in destination.")
            return
        was_playing = self._current_path == path
        if was_playing:
            self.player.stop()
            self.player.setSource(QUrl())
            QApplication.processEvents()
        try:
            import shutil
            shutil.move(str(src), str(dst))
        except OSError as e:
            QMessageBox.warning(self, "Move Failed", str(e))
            if was_playing:
                self._load_video(path)
            return
        parent_dir = str(src.parent)
        self._rebuild_playlist(parent_dir)
        if was_playing:
            self._current_path = None
            self._update_info()

    def _copy_file_path(self, path: str):
        QApplication.clipboard().setText(path)
        self._show_osd("Path copied")

    def _open_in_default(self, path: str):
        if Path(path).exists():
            import os as _os
            _os.startfile(path)

    def _batch_delete(self, paths: list[str]):
        reply = QMessageBox.question(
            self, "Delete Files",
            f"Permanently delete {len(paths)} files?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._current_path in paths:
            self.player.stop()
            self.player.setSource(QUrl())
            QApplication.processEvents()
            self._current_path = None
            self._update_info()
        failed = 0
        for p in paths:
            try:
                Path(p).unlink()
            except OSError:
                failed += 1
        parent = str(Path(paths[0]).parent)
        self._rebuild_playlist(parent)
        if failed:
            QMessageBox.warning(self, "Delete",
                                f"Failed to delete {failed} file(s).")

    def _batch_move(self, paths: list[str]):
        dest = QFileDialog.getExistingDirectory(self, "Move files to...")
        if not dest:
            return
        if self._current_path in paths:
            self.player.stop()
            self.player.setSource(QUrl())
            QApplication.processEvents()
            self._current_path = None
            self._update_info()
        import shutil
        failed = 0
        for p in paths:
            try:
                src = Path(p)
                dst = Path(dest) / src.name
                if not dst.exists():
                    shutil.move(str(src), str(dst))
                else:
                    failed += 1
            except OSError:
                failed += 1
        parent = str(Path(paths[0]).parent)
        self._rebuild_playlist(parent)
        if failed:
            QMessageBox.warning(self, "Move",
                                f"Failed to move {failed} file(s).")

    def _playlist_move(self, idx: int, direction: int):
        """Swap playlist entry at idx with idx+direction."""
        new_idx = idx + direction
        if 0 <= new_idx < len(self._playlist):
            self._playlist[idx], self._playlist[new_idx] = (
                self._playlist[new_idx], self._playlist[idx])
            if self._playlist_index == idx:
                self._playlist_index = new_idx
            elif self._playlist_index == new_idx:
                self._playlist_index = idx
            self._update_nav()

    # ── Fullscreen ────────────────────────────────────────────

    def toggle_fullscreen(self):
        """Toggle fullscreen mode — hides side panels and controls."""
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            # Save panel state before hiding (preserves compact mode)
            self._pre_fs_tree_vis = self._splitter.widget(0).isVisible()
            self._pre_fs_list_vis = self._splitter.widget(2).isVisible()
            self._splitter.widget(0).setVisible(False)
            self._splitter.widget(2).setVisible(False)
        else:
            # Restore pre-fullscreen panel state
            self._splitter.widget(0).setVisible(self._pre_fs_tree_vis)
            self._splitter.widget(2).setVisible(self._pre_fs_list_vis)
        if self._fullscreen:
            # Start with controls hidden; mouse move will show them
            self._controls_widget.setVisible(False)
            self._nav_widget.setVisible(False)
            self.lbl_info.setVisible(False)
            self.surface.set_title_visible(False)
            self._fs_start_autohide()
        else:
            # Exiting fullscreen — show everything, stop timers
            self._fs_controls_timer.stop()
            self._fs_cursor_timer.stop()
            self._controls_widget.setVisible(True)
            self._nav_widget.setVisible(True)
            self.lbl_info.setVisible(True)
            self.surface.set_title_visible(False)
            self.surface.setCursor(Qt.CursorShape.ArrowCursor)
        self.fullscreen_changed.emit(self._fullscreen)

    @property
    def is_fullscreen(self) -> bool:
        return self._fullscreen

    # ── Fullscreen auto-hide ──────────────────────────────────

    def _fs_start_autohide(self):
        """Start the fullscreen auto-hide timers."""
        if self._settings.get("player_controls_autohide", True):
            self._fs_controls_timer.setInterval(
                self._settings.get("player_controls_autohide_delay", 3000))
            self._fs_controls_timer.start()
        if self._settings.get("player_cursor_hide", True):
            self._fs_cursor_timer.setInterval(
                self._settings.get("player_cursor_hide_delay", 3000))
            self._fs_cursor_timer.start()

    def _fs_hide_controls(self):
        """Timer fired — hide controls in fullscreen (skip if cursor on controls)."""
        if not self._fullscreen:
            return
        # Don't hide if cursor is over controls or nav bar
        from PyQt6.QtGui import QCursor
        gpos = QCursor.pos()
        for w in (self._controls_widget, self._nav_widget):
            if w.isVisible() and w.rect().contains(w.mapFromGlobal(gpos)):
                self._fs_controls_timer.start()  # restart timer
                return
        self._controls_widget.setVisible(False)
        self._nav_widget.setVisible(False)
        self.lbl_info.setVisible(False)
        self.surface.set_title_visible(False)

    def _fs_hide_cursor(self):
        """Timer fired — hide cursor in fullscreen."""
        if self._fullscreen:
            self.surface.setCursor(Qt.CursorShape.BlankCursor)

    def _on_surface_mouse_move(self):
        """Mouse moved on surface — show controls and restart timers."""
        if not self._fullscreen:
            return
        # Show controls + top title overlay
        if self._settings.get("player_controls_autohide", True):
            self._controls_widget.setVisible(True)
            self._nav_widget.setVisible(True)
            self.lbl_info.setVisible(True)
            self.surface.set_title_visible(True)
            self._fs_controls_timer.start()
        # Restore cursor
        if self._settings.get("player_cursor_hide", True):
            self.surface.setCursor(Qt.CursorShape.ArrowCursor)
            self._fs_cursor_timer.start()

    def get_current_directory(self) -> str:
        """Return the directory currently displayed in the file list."""
        return self._file_model.rootPath() or ""

    # ── Video playback ────────────────────────────────────────

    def _load_video(self, path: str):
        # Save position of previous file
        self._save_position()
        self.player.stop()
        self.player.setSource(QUrl())
        self._current_path = path

        # If we preloaded this file's first frame, paint it immediately
        # so the surface doesn't black-flash while QMediaPlayer loads.
        preloaded = self._preload_cache.pop(path, None)
        if preloaded is not None:
            self.surface.set_placeholder_image(preloaded)

        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        # Cancel any in-flight preload request that's no longer relevant
        self._preload_requested_for = None

        # Reset speed and A-B loop for new file
        self._current_speed = 1.0
        self.player.setPlaybackRate(1.0)
        self._update_speed_label()
        self._loop_a_ms = self._loop_b_ms = None

        # Update seek bar context (path + clear markers)
        self.seek_slider.set_current_path(path)
        self.seek_slider.set_ab_markers(None, None)

        # Update playlist index
        if path in self._playlist:
            self._playlist_index = self._playlist.index(path)

        self.btn_send.setEnabled(True)
        self._update_nav()
        self._update_info()
        self.title_changed.emit(Path(path).name)
        self.surface.set_title(Path(path).name)

        # Resume saved position (guard against rapid file switching)
        if self._settings.get("player_remember_positions", False):
            positions = self._settings.get("player_positions", {})
            saved_ms = positions.get(path, 0)
            if saved_ms > 0:
                QTimer.singleShot(200, lambda p=path, ms=saved_ms: (
                    self.player.setPosition(ms) if self._current_path == p else None
                ))

        # Navigate dir tree + file list to this file's directory
        parent = str(Path(path).parent).replace("\\", "/")
        if self._file_model.rootPath() != parent:
            self._navigate_to_dir(parent)

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self._show_osd("Paused", primary=True)
        else:
            if self._current_path:
                self.player.play()
                self._show_osd("Play", primary=True)

    def _go_prev(self):
        if not self._playlist or self._playlist_index <= 0:
            return
        self._playlist_index -= 1
        self._load_video(self._playlist[self._playlist_index])

    def _go_next(self):
        if not self._playlist or self._playlist_index >= len(self._playlist) - 1:
            return
        self._playlist_index += 1
        self._load_video(self._playlist[self._playlist_index])

    def _send_to_editor(self):
        if self._current_path:
            self.send_to_editor.emit([self._current_path])

    # ── Player signals ────────────────────────────────────────

    def _on_position_changed(self, pos_ms: int):
        if not self._seeking:
            self.seek_slider.blockSignals(True)
            if self._duration_ms > 0:
                self.seek_slider.setValue(int(pos_ms / self._duration_ms * 1000))
            self.seek_slider.blockSignals(False)
        self.lbl_time.setText(f"{self._fmt(pos_ms)} / {self._fmt(self._duration_ms)}")
        # A-B loop
        if (self._loop_a_ms is not None and self._loop_b_ms is not None
                and pos_ms >= self._loop_b_ms):
            self.player.setPosition(self._loop_a_ms)
        # Preload next playlist file when current is ≥80% played
        self._maybe_preload_next(pos_ms)

    def _maybe_preload_next(self, pos_ms: int) -> None:
        """Trigger preload of the next playlist file's first frame once
        the current file is well into its tail. Cheap to skip — costs
        nothing if already requested or the file is short."""
        if self._duration_ms <= 0:
            return
        if pos_ms / self._duration_ms < 0.80:
            return
        if (self._playlist_index < 0
                or self._playlist_index >= len(self._playlist) - 1):
            return
        next_path = self._playlist[self._playlist_index + 1]
        if (self._preload_requested_for == next_path
                or next_path in self._preload_cache):
            return
        self._preload_requested_for = next_path
        self._preloader.request(next_path)

    def _on_preload_ready(self, path: str, img: QImage) -> None:
        if not isinstance(img, QImage) or img.isNull():
            return
        # Cap cache at 3 entries (LRU)
        if path not in self._preload_cache and len(self._preload_cache) >= 3:
            oldest = next(iter(self._preload_cache))
            del self._preload_cache[oldest]
        self._preload_cache[path] = img

    def _on_duration_changed(self, dur_ms: int):
        self._duration_ms = dur_ms
        self.seek_slider.set_duration(dur_ms)

    def _on_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _on_buffer_progress(self, progress: float):
        """Show buffering status in seek bar tooltip (mostly for network streams)."""
        if progress < 1.0:
            self.seek_slider.setToolTip(f"Buffering: {int(progress * 100)}%")
        else:
            self.seek_slider.setToolTip("")

    def _on_tracks_changed(self):
        """Update audio track combo when tracks are detected."""
        audio_tracks = self.player.audioTracks()
        self._audio_track_combo.blockSignals(True)
        self._audio_track_combo.clear()
        if len(audio_tracks) > 1:
            for i, track in enumerate(audio_tracks):
                lang = track.stringValue(track.Key.Language) or ""
                title = track.stringValue(track.Key.Title) or ""
                label = title or lang or f"Track {i + 1}"
                self._audio_track_combo.addItem(label)
            self._audio_track_combo.setVisible(True)
        else:
            self._audio_track_combo.setVisible(False)
        self._audio_track_combo.blockSignals(False)

    def _on_audio_track_changed(self, index: int):
        if index >= 0:
            self.player.setActiveAudioTrack(index)

    def _on_media_status(self, status):
        # Loading spinner: show during initial load + stall states.
        # The placeholder image (preload) shows behind the spinner.
        is_loading = status in (
            QMediaPlayer.MediaStatus.LoadingMedia,
            QMediaPlayer.MediaStatus.StalledMedia,
        )
        self.surface.set_loading(is_loading)

        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        if self._loop_mode == 1:
            # Single loop — replay current
            self.player.setPosition(0)
            self.player.play()
        elif self._loop_mode == 2:
            # Playlist loop — next, wrap to start
            if self._playlist_index < len(self._playlist) - 1:
                self._go_next()
            elif self._playlist:
                self._playlist_index = 0
                self._load_video(self._playlist[0])
        else:
            # Off — auto-advance if setting enabled, don't wrap
            if self._settings.get("player_auto_advance", True):
                if self._shuffle and len(self._playlist) > 1:
                    import random
                    choices = [i for i in range(len(self._playlist))
                               if i != self._playlist_index]
                    self._playlist_index = random.choice(choices)
                    self._load_video(self._playlist[self._playlist_index])
                elif self._playlist_index < len(self._playlist) - 1:
                    self._go_next()

    def _on_seek_changed(self, value: int):
        if self._seeking and self._duration_ms > 0:
            pos = int(value / 1000 * self._duration_ms)
            self.player.setPosition(pos)

    def _on_seek_released(self):
        self._seeking = False
        if self._duration_ms > 0:
            pos = int(self.seek_slider.value() / 1000 * self._duration_ms)
            self.player.setPosition(pos)

    # ── UI updates ────────────────────────────────────────────

    def _update_nav(self):
        has_file = self._current_path is not None
        count = len(self._playlist)
        self.btn_prev.setEnabled(has_file and self._playlist_index > 0)
        self.btn_next.setEnabled(has_file and self._playlist_index < count - 1)
        if has_file:
            name = Path(self._current_path).name
            if count > 1:
                self.lbl_filename.setText(
                    f"{self._playlist_index + 1} of {count}  \u2014  {name}"
                )
                self.lbl_playlist_pos.setText(
                    f"{self._playlist_index + 1}/{count}")
            else:
                self.lbl_filename.setText(name)
                self.lbl_playlist_pos.setText("")
        else:
            self.lbl_filename.setText("Double-click a video to play")
            self.lbl_playlist_pos.setText("")

    def _update_info(self):
        if not self._current_path:
            self.lbl_info.setText("")
            return
        info = probe_video(self._current_path)
        if info:
            parts = []
            if info["width"] and info["height"]:
                parts.append(f"{info['width']}x{info['height']}")
            if info["fps"]:
                parts.append(f"{info['fps']:.0f}fps")
            if info["video_codec"]:
                parts.append(info["video_codec"].upper())
            if info["audio_codec"]:
                parts.append(info["audio_codec"].upper())
            if info["duration"]:
                parts.append(self._fmt(int(info["duration"] * 1000)))
            size = info.get("file_size", 0)
            if size:
                parts.append(f"{size / (1024 * 1024):.1f} MB")
            self.lbl_info.setText("  \u2502  ".join(parts))
        else:
            # Fallback: just filename + size
            p = Path(self._current_path)
            parts = [p.name]
            try:
                parts.append(f"{p.stat().st_size / (1024 * 1024):.1f} MB")
            except OSError:
                pass
            self.lbl_info.setText("  \u2502  ".join(parts))

    # ── Playback control methods (called by keybind handlers) ───

    def _show_osd(self, text: str, primary: bool = False):
        """Show OSD if enabled in settings.

        primary=True renders as a large centered chip (used for major
        actions like play/pause/mute); otherwise a small corner toast.
        """
        if self._settings.get("player_osd", True):
            self.surface.show_osd(text, primary=primary)

    def seek_relative(self, delta_ms: int):
        """Seek forward/backward by delta_ms."""
        if self._duration_ms <= 0:
            return
        pos = max(0, min(self._duration_ms, self.player.position() + delta_ms))
        self.player.setPosition(pos)
        self._show_osd(self._fmt(pos))

    def adjust_volume(self, delta: int):
        """Adjust volume by delta (e.g. +5 or -5)."""
        new_val = max(0, min(100, self.vol_slider.value() + delta))
        self.vol_slider.setValue(new_val)
        self.audio.setVolume(new_val / 100.0)
        self._update_mute_icon()
        self._show_osd(f"Volume: {new_val}%")
        self.volume_changed.emit(new_val)

    def toggle_mute(self):
        """Toggle mute — store/restore previous volume."""
        if self.vol_slider.value() > 0:
            self._pre_mute_volume = self.vol_slider.value()
            self.vol_slider.setValue(0)
            self.audio.setVolume(0.0)
            self._show_osd("Muted", primary=True)
        else:
            self.vol_slider.setValue(self._pre_mute_volume)
            self.audio.setVolume(self._pre_mute_volume / 100.0)
            self._show_osd(f"Volume: {self._pre_mute_volume}%", primary=True)
        self._update_mute_icon()
        self.volume_changed.emit(self.vol_slider.value())

    def adjust_speed(self, delta: float):
        """Adjust playback speed by delta (e.g. +0.25)."""
        current = self.player.playbackRate()
        new_rate = max(0.25, min(4.0, round(current + delta, 2)))
        self.player.setPlaybackRate(new_rate)
        self._current_speed = new_rate
        self._update_speed_label()
        self._show_osd(f"Speed: {new_rate:.2f}x")

    def reset_speed(self):
        """Reset playback speed to 1.0x."""
        self.player.setPlaybackRate(1.0)
        self._current_speed = 1.0
        self._update_speed_label()
        self._show_osd("Speed: 1.00x")

    def frame_step(self, direction: int):
        """Step one frame forward (1) or backward (-1)."""
        if not self._current_path or self._duration_ms <= 0:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        # Use probed fps (already cached from _update_info), fallback 30
        fps = 30.0
        info = probe_video(self._current_path)
        if info and info["fps"] > 0:
            fps = info["fps"]
        frame_ms = max(1, int(round(1000 / fps)))
        pos = max(0, min(self._duration_ms,
                         self.player.position() + direction * frame_ms))
        self.player.setPosition(pos)
        self._show_osd(self._fmt(pos))

    def _update_speed_label(self):
        if self._current_speed == 1.0:
            self.lbl_speed.setText("")
        else:
            self.lbl_speed.setText(f"{self._current_speed:.2f}x")

    def _update_mute_icon(self):
        if self.vol_slider.value() == 0:
            self._vol_icon.setPixmap(
                self.style().standardIcon(
                    QStyle.StandardPixmap.SP_MediaVolumeMuted).pixmap(16, 16))
        else:
            self._vol_icon.setPixmap(
                self.style().standardIcon(
                    QStyle.StandardPixmap.SP_MediaVolume).pixmap(16, 16))

    def jump_to_start(self):
        """Jump to beginning of file."""
        self.player.setPosition(0)
        self._show_osd("Start")

    def jump_to_end(self):
        """Jump to near-end of file."""
        if self._duration_ms > 0:
            self.player.setPosition(max(0, self._duration_ms - 100))
            self._show_osd("End")

    def goto_timestamp(self):
        """Open dialog to jump to a specific timestamp."""
        if self._duration_ms <= 0:
            return
        text, ok = QInputDialog.getText(
            self, "Go to Timestamp",
            "Enter time (mm:ss, hh:mm:ss, or seconds):",
        )
        if not ok or not text.strip():
            return
        ms = self._parse_timestamp(text.strip())
        if ms is not None:
            ms = max(0, min(self._duration_ms, ms))
            self.player.setPosition(ms)
            self._show_osd(self._fmt(ms))

    @staticmethod
    def _parse_timestamp(text: str) -> int | None:
        """Parse mm:ss, hh:mm:ss, or raw seconds → milliseconds."""
        try:
            # Try raw seconds first
            if ":" not in text:
                return int(float(text) * 1000)
            parts = text.split(":")
            if len(parts) == 2:
                m, s = int(parts[0]), float(parts[1])
                return int((m * 60 + s) * 1000)
            elif len(parts) == 3:
                h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
                return int((h * 3600 + m * 60 + s) * 1000)
        except (ValueError, IndexError):
            pass
        return None

    def cycle_loop_mode(self):
        """Cycle through loop modes: off → single → playlist → off."""
        self._loop_mode = (self._loop_mode + 1) % 3
        names = ["Loop: Off", "Loop: Single", "Loop: Playlist"]
        self._show_osd(names[self._loop_mode])

    def ab_mark(self):
        """Cycle A-B loop: set A → set B → clear."""
        if self._loop_a_ms is None:
            self._loop_a_ms = self.player.position()
            self._show_osd(f"A: {self._fmt(self._loop_a_ms)}")
        elif self._loop_b_ms is None:
            self._loop_b_ms = self.player.position()
            if self._loop_b_ms <= self._loop_a_ms:
                # Invalid range — clear
                self._loop_a_ms = self._loop_b_ms = None
                self._show_osd("A-B cleared")
            else:
                self._show_osd(
                    f"A-B: {self._fmt(self._loop_a_ms)} → {self._fmt(self._loop_b_ms)}")
        else:
            self._loop_a_ms = self._loop_b_ms = None
            self._show_osd("A-B cleared")
        self.seek_slider.set_ab_markers(self._loop_a_ms, self._loop_b_ms)

    def toggle_shuffle(self):
        """Toggle shuffle mode."""
        self._shuffle = not self._shuffle
        self._show_osd(f"Shuffle: {'On' if self._shuffle else 'Off'}")

    _ASPECT_CYCLE = [
        (None, "Original"),
        ((16, 9), "16:9"),
        ((4, 3), "4:3"),
        ((21, 9), "21:9"),
        ((1, 1), "1:1"),
    ]

    def cycle_aspect(self):
        """Cycle through aspect ratio overrides."""
        current = self.surface._aspect_override
        # Find current in cycle list, advance to next
        current_idx = 0
        for i, (ar, _) in enumerate(self._ASPECT_CYCLE):
            if ar == current:
                current_idx = i
                break
        idx = (current_idx + 1) % len(self._ASPECT_CYCLE)
        ar, name = self._ASPECT_CYCLE[idx]
        self.surface._aspect_override = ar
        self.surface.update()
        self._show_osd(f"Aspect: {name}")

    def get_video_size(self) -> tuple[int, int] | None:
        """Return native video dimensions, or None."""
        if self.surface._image and not self.surface._image.isNull():
            return self.surface._image.width(), self.surface._image.height()
        return None

    def toggle_compact(self):
        """Compact mode — hide side panels but keep controls (windowed)."""
        if self._fullscreen:
            return  # Already in fullscreen, don't mix modes
        tree_vis = self._splitter.widget(0).isVisible()
        self._splitter.widget(0).setVisible(not tree_vis)
        self._splitter.widget(2).setVisible(not tree_vis)
        self._show_osd("Compact" if tree_vis else "Normal")

    def take_screenshot(self):
        """Capture current frame as PNG to clipboard and file."""
        if not self._current_path:
            return
        if self.surface._image and not self.surface._image.isNull():
            QApplication.clipboard().setImage(self.surface._image)
            # Also save to file next to the video
            p = Path(self._current_path)
            pos_s = self.player.position() / 1000.0
            out = p.parent / f"{p.stem}_screenshot_{pos_s:.1f}s.png"
            self.surface._image.save(str(out), "PNG")
            self._show_osd(f"Screenshot saved")

    def copy_path(self):
        """Copy current file path to clipboard."""
        if self._current_path:
            QApplication.clipboard().setText(self._current_path)
            self._show_osd("Path copied")

    def open_external(self):
        """Open current file in default system application."""
        if self._current_path and Path(self._current_path).exists():
            import os as _os
            _os.startfile(self._current_path)

    def open_folder_dialog(self):
        """Open folder picker and navigate to it."""
        folder = QFileDialog.getExistingDirectory(
            self, "Open Folder",
            self._file_model.rootPath() or str(Path.home()))
        if folder:
            self._navigate_to_dir(folder)

    def get_recent_dirs(self) -> list[str]:
        """Return recent directories list."""
        return list(self._recent_dirs)

    def refresh_file_list(self):
        """Refresh the current directory's file list."""
        path = self._file_model.rootPath()
        if path:
            self._rebuild_playlist(path)
            self._show_osd("Refreshed")

    def _save_position(self):
        """Save current playback position for resume."""
        if (not self._current_path
                or not self._settings.get("player_remember_positions", False)):
            return
        pos = self.player.position()
        positions = self._settings.setdefault("player_positions", {})
        if pos > 1000 and self._duration_ms > 0 and pos < self._duration_ms - 1000:
            positions[self._current_path] = pos
        else:
            positions.pop(self._current_path, None)
        # LRU cap at 100 entries
        if len(positions) > 100:
            keys = list(positions.keys())
            for k in keys[:len(keys) - 100]:
                del positions[k]

    def release(self):
        """Release the player resources — called on app shutdown."""
        self._save_position()
        self.player.stop()
        self.player.setSource(QUrl())
        # Stop background extractor threads
        try:
            self.seek_slider.set_thumbnail_worker(None)
            self._thumb_worker.stop()
        except Exception:
            pass
        try:
            self._preloader.stop()
        except Exception:
            pass

    def set_volume(self, value: int):
        """Set volume from persisted settings."""
        self.vol_slider.blockSignals(True)
        self.vol_slider.setValue(max(0, min(100, value)))
        self.vol_slider.blockSignals(False)
        self.audio.setVolume(value / 100.0)
        self._update_mute_icon()

    def navigate_to(self, path: str):
        """Navigate to a specific directory — used by external callers."""
        if Path(path).is_dir():
            self._navigate_to_dir(path)
        elif Path(path).is_file():
            self._load_video(path)

    @staticmethod
    def _fmt(ms: int) -> str:
        s = ms / 1000
        m = int(s // 60)
        s = s - m * 60
        return f"{m:02d}:{s:05.2f}"
