"""Standalone video player mode with directory browser and playlist.

Self-contained — no imports from the editor UI modules (main_window,
crop_overlay, trim_controls, automation_lane). Shares only core modules
and the ClickSlider widget.

The PlayerMode widget is designed to be embedded in a tab or stacked
widget alongside the editor. It manages its own QMediaPlayer instance
so switching modes doesn't disrupt the editor's player state.
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeView, QListView,
    QPushButton, QLabel, QStyle, QAbstractItemView, QMenu, QFileDialog,
    QMessageBox, QApplication,
)
from PyQt6.QtCore import (
    Qt, QUrl, QDir, QSortFilterProxyModel, QModelIndex, pyqtSignal, QTimer,
)
from PyQt6.QtGui import (
    QFileSystemModel, QKeySequence, QPainter, QAction,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QVideoFrame

from src.ui.widgets import ClickSlider

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv'}


class _PlayerSurface(QWidget):
    """Minimal video surface — just renders QVideoSink frames. No crop,
    no stretch, no color overlay, no pan. Clean playback."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self._image = None
        self.setMinimumSize(320, 180)
        self.setStyleSheet("background: black;")

    def _on_frame(self, frame: QVideoFrame):
        self._image = frame.toImage()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._image and not self._image.isNull():
            iw, ih = self._image.width(), self._image.height()
            scale = min(self.width() / iw, self.height() / ih)
            w, h = int(iw * scale), int(ih * scale)
            x = (self.width() - w) // 2
            y = (self.height() - h) // 2
            p.drawImage(x, y, self._image.scaled(
                w, h, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        p.end()


class _VideoFilter(QSortFilterProxyModel):
    """Proxy that shows only directories and video files in the file list."""

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        model = self.sourceModel()
        idx = model.index(row, 0, parent)
        if model.isDir(idx):
            return True
        name = model.fileName(idx).lower()
        return any(name.endswith(ext) for ext in VIDEO_EXTENSIONS)


class PlayerMode(QWidget):
    """Full video player with directory browser, playlist, file management."""

    send_to_editor = pyqtSignal(list)  # list of file paths to load in editor

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path: str | None = None
        self._playlist: list[str] = []
        self._playlist_index: int = -1
        self._setup_ui()
        self._setup_player()
        self._connect_signals()

        # Start in the user's home directory
        home = str(Path.home())
        self._navigate_to_dir(home)

    # ── UI setup ──────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

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
        cv.addWidget(self.surface, stretch=1)

        # Controls bar
        controls = QHBoxLayout()

        self.btn_play = QPushButton()
        self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.setFixedSize(36, 36)
        controls.addWidget(self.btn_play)

        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setStyleSheet("font-family: monospace;")
        controls.addWidget(self.lbl_time)

        # Seek bar
        self.seek_slider = ClickSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 1000)
        controls.addWidget(self.seek_slider, stretch=1)

        # Volume
        vol_icon = QLabel()
        vol_icon.setPixmap(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume).pixmap(16, 16)
        )
        controls.addWidget(vol_icon)
        self.vol_slider = ClickSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        self.vol_slider.setFixedWidth(80)
        self.vol_slider.setFixedHeight(16)
        self.vol_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 3px; }
            QSlider::handle:horizontal { width: 8px; margin: -3px 0; border-radius: 4px; }
        """)
        controls.addWidget(self.vol_slider)

        cv.addLayout(controls)

        # Nav bar
        nav = QHBoxLayout()

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

        cv.addLayout(nav)

        # File info bar
        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet("font-family: monospace; color: gray; padding: 2px 4px;")
        cv.addWidget(self.lbl_info)

        splitter.addWidget(center)

        # ── Right: file list ──────────────────────────────────
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)

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

    def _connect_signals(self):
        self._dir_tree.clicked.connect(self._on_dir_clicked)
        self._file_list.doubleClicked.connect(self._on_file_double_clicked)
        self._file_list.customContextMenuRequested.connect(self._on_file_context_menu)

        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_prev.clicked.connect(self._go_prev)
        self.btn_next.clicked.connect(self._go_next)
        self.btn_send.clicked.connect(self._send_to_editor)

        self.seek_slider.sliderPressed.connect(lambda: setattr(self, '_seeking', True))
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        self.seek_slider.valueChanged.connect(self._on_seek_changed)
        self.vol_slider.valueChanged.connect(
            lambda v: self.audio.setVolume(v / 100.0)
        )

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
        self._playlist = sorted(
            [str(f) for f in p.iterdir()
             if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS],
            key=lambda x: x.lower(),
        )
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

        menu.addAction("Show in Explorer", lambda: self._show_in_explorer(path))
        menu.addSeparator()
        menu.addAction("Delete File", lambda: self._delete_file(path))

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
            subprocess.Popen(['explorer', '/select,', path])

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

    # ── Video playback ────────────────────────────────────────

    def _load_video(self, path: str):
        self.player.stop()
        self.player.setSource(QUrl())
        self._current_path = path
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

        # Update playlist index
        if path in self._playlist:
            self._playlist_index = self._playlist.index(path)

        self.btn_send.setEnabled(True)
        self._update_nav()
        self._update_info()

        # Navigate dir tree + file list to this file's directory
        parent = str(Path(path).parent)
        if self._file_model.rootPath() != parent:
            self._navigate_to_dir(parent)

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self._current_path:
                self.player.play()

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

    def _on_duration_changed(self, dur_ms: int):
        self._duration_ms = dur_ms

    def _on_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _on_media_status(self, status):
        # Auto-advance to next video when current one finishes
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._playlist_index < len(self._playlist) - 1:
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
            else:
                self.lbl_filename.setText(name)
        else:
            self.lbl_filename.setText("Double-click a video to play")

    def _update_info(self):
        if not self._current_path:
            self.lbl_info.setText("")
            return
        p = Path(self._current_path)
        parts = [p.name]
        try:
            size_mb = p.stat().st_size / (1024 * 1024)
            parts.append(f"{size_mb:.1f} MB")
        except OSError:
            pass
        self.lbl_info.setText("  \u2502  ".join(parts))

    def release(self):
        """Release the player resources — called on app shutdown."""
        self.player.stop()
        self.player.setSource(QUrl())

    def set_volume(self, value: int):
        """Set volume from persisted settings."""
        self.vol_slider.blockSignals(True)
        self.vol_slider.setValue(max(0, min(100, value)))
        self.vol_slider.blockSignals(False)
        self.audio.setVolume(value / 100.0)

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
