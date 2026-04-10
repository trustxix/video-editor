import array
import subprocess
import threading

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QStyle
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QVideoFrame, QAudioSink, QAudioFormat
from PyQt6.QtCore import Qt, QRect, QPointF, pyqtSignal, QUrl, QTimer
from PyQt6.QtGui import QPainter

from src.core.paths import get_ffmpeg


class PitchedAudioPlayer:
    """Vinyl-style audio via push-mode QAudioSink at fixed 48 kHz.

    Resamples PCM on the fly using the current speed value.  Speed can change
    smoothly (e.g. during automation ramps) without recreating the sink —
    just update self._speed and the next _feed() tick uses the new rate.
    """

    _BASE_RATE = 48000
    _CHANNELS = 2
    _FRAME_BYTES = _CHANNELS * 2

    def __init__(self):
        self._pcm: array.array | None = None  # array('h'), interleaved stereo
        self._sink: QAudioSink | None = None
        self._io = None
        self._timer = QTimer()
        self._timer.timeout.connect(self._feed)
        self._speed = 1.0
        self._pos = 0.0  # fractional frame position in input
        self._gen = 0
        self._automation = None  # set to AutomationLane for per-sample speed lookup
        self._extract_proc: subprocess.Popen | None = None  # current ffmpeg PCM extraction

    def extract_audio(self, video_path: str):
        self.stop()
        self._pcm = None
        self._gen += 1
        gen = self._gen
        threading.Thread(
            target=self._do_extract, args=(video_path, gen), daemon=True
        ).start()

    def _do_extract(self, path: str, gen: int):
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            # Use Popen (not run) so release() can terminate an in-progress extraction.
            proc = subprocess.Popen(
                [get_ffmpeg(), '-i', path, '-vn',
                 '-f', 's16le', '-acodec', 'pcm_s16le',
                 '-ac', '2', '-ar', '48000', '-'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                startupinfo=si,
            )
            self._extract_proc = proc
            stdout, _ = proc.communicate()
            if gen == self._gen and stdout:
                a = array.array('h')
                a.frombytes(stdout)
                self._pcm = a
        except Exception:
            pass

    def set_speed(self, speed: float):
        """Update speed smoothly — no sink recreation needed."""
        self._speed = speed

    def play(self, position_ms: int = 0):
        """Start push-mode playback at fixed 48 kHz."""
        self.stop()
        if not self._pcm:
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(self._BASE_RATE)
        fmt.setChannelCount(self._CHANNELS)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._sink = QAudioSink(fmt)
        self._io = self._sink.start()
        self._pos = float(position_ms * self._BASE_RATE / 1000)
        self._feed()  # prime buffer immediately
        self._timer.start(15)

    def _feed(self):
        if not self._io or not self._pcm or not self._sink:
            return
        free = self._sink.bytesFree()
        n = min(960, free // self._FRAME_BYTES)
        if n <= 0:
            return
        total = len(self._pcm) // self._CHANNELS
        out = array.array('h')
        pcm = self._pcm
        pos = self._pos
        auto = self._automation
        has_auto = auto is not None and auto.get_keyframes()

        # Per-sample speed: query automation for each sample's position
        for i in range(n):
            if has_auto:
                time_ms = pos * 1000 / self._BASE_RATE
                speed = auto.get_speed_at(int(time_ms))
            else:
                speed = self._speed
            idx = int(pos)
            frac = pos - idx
            if idx >= total - 1:
                out.extend([0] * (n - i) * 2)
                break
            b = idx * 2
            s1 = pcm[b];     s2 = pcm[b + 2]
            out.append(int(s1 + (s2 - s1) * frac))
            s1 = pcm[b + 1]; s2 = pcm[b + 3]
            out.append(int(s1 + (s2 - s1) * frac))
            pos += speed
        self._pos = pos
        self._io.write(out.tobytes())

    def stop(self):
        self._timer.stop()
        if self._sink:
            self._sink.stop()
            self._sink = None
        self._io = None

    @property
    def ready(self) -> bool:
        return bool(self._pcm)

    def release(self):
        self.stop()
        # Kill any in-progress audio extraction so the ffmpeg child doesn't
        # outlive pythonw as an orphan.
        if self._extract_proc is not None:
            try:
                if self._extract_proc.poll() is None:
                    self._extract_proc.terminate()
                    try:
                        self._extract_proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self._extract_proc.kill()
            except Exception:
                pass
            self._extract_proc = None
        self._pcm = None
        self._gen += 1


class VideoSurface(QWidget):
    """Renders video frames via QVideoSink with view zoom/pan support."""

    zoom_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self._image = None
        self.setMinimumSize(640, 360)
        self.setStyleSheet("background: black;")

        # Content positioning (affects export)
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._stretch_h = 1.0
        self._stretch_v = 1.0

        # View transform (purely visual, does not affect export)
        self._view_zoom = 1.0
        self._view_pan_x = 0.0
        self._view_pan_y = 0.0

    def _on_frame(self, frame: QVideoFrame):
        first = self._image is None
        self._image = frame.toImage()
        self.update()
        if first:
            self.zoom_changed.emit()

    def video_display_rect(self) -> QRect:
        """Video rect with content pan and stretch (for painting)."""
        if self._image is None or self._image.isNull():
            return self.rect()
        iw, ih = self._image.width(), self._image.height()
        base_scale = min(self.width() / iw, self.height() / ih)
        w = iw * base_scale * self._stretch_h
        h = ih * base_scale * self._stretch_v
        x = (self.width() - w) / 2 + self._pan_x
        y = (self.height() - h) / 2 + self._pan_y
        return QRect(int(x), int(y), int(w), int(h))

    def video_crop_rect(self) -> QRect:
        """Video rect with content pan, WITHOUT stretch (for crop mapping)."""
        if self._image is None or self._image.isNull():
            return self.rect()
        iw, ih = self._image.width(), self._image.height()
        base_scale = min(self.width() / iw, self.height() / ih)
        w = iw * base_scale
        h = ih * base_scale
        x = (self.width() - w) / 2 + self._pan_x
        y = (self.height() - h) / 2 + self._pan_y
        return QRect(int(x), int(y), int(w), int(h))

    def set_stretch(self, h: float, v: float):
        self._stretch_h = h
        self._stretch_v = v
        self.update()
        self.zoom_changed.emit()

    def reset_zoom(self):
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._stretch_h = 1.0
        self._stretch_v = 1.0
        self._view_zoom = 1.0
        self._view_pan_x = 0.0
        self._view_pan_y = 0.0
        self.update()
        self.zoom_changed.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._image is not None and not self._image.isNull():
            p.save()
            p.translate(self._view_pan_x, self._view_pan_y)
            p.scale(self._view_zoom, self._view_zoom)
            p.drawImage(self.video_display_rect(), self._image)
            p.restore()
        p.end()

    # ── View zoom (Ctrl + scroll) ────────────────────────────

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta == 0:
                return
            factor = 1.15 if delta > 0 else (1 / 1.15)
            new_zoom = max(0.1, self._view_zoom * factor)

            if new_zoom != self._view_zoom:
                # Zoom toward cursor: keep the point under the cursor fixed
                cx, cy = event.position().x(), event.position().y()
                # Cursor's logical position before zoom
                lx = (cx - self._view_pan_x) / self._view_zoom
                ly = (cy - self._view_pan_y) / self._view_zoom
                self._view_zoom = new_zoom
                # Adjust pan so the same logical point stays under cursor
                self._view_pan_x = cx - lx * self._view_zoom
                self._view_pan_y = cy - ly * self._view_zoom

                if abs(self._view_zoom - 1.0) < 0.01:
                    self._view_zoom = 1.0
                    self._view_pan_x = 0.0
                    self._view_pan_y = 0.0

                self.update()
                self.zoom_changed.emit()

            event.accept()
        else:
            super().wheelEvent(event)


class VideoPlayer(QWidget):
    position_changed = pyqtSignal(int)  # ms
    duration_changed = pyqtSignal(int)  # ms

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._setup_player()
        self._pitched = PitchedAudioPlayer()
        self._speed = 1.0
        self._pitched_active = False

    def set_automation(self, automation_lane):
        """Give the pitched audio player a reference to the automation lane for per-sample speed."""
        self._pitched._automation = automation_lane

    def _setup_ui(self):
        self.layout_main = QVBoxLayout(self)
        self.layout_main.setContentsMargins(0, 0, 0, 0)

        self.surface = VideoSurface()
        self.layout_main.addWidget(self.surface, stretch=1)

        controls = QHBoxLayout()
        self.btn_play = QPushButton()
        self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.setFixedSize(36, 36)
        self.btn_play.clicked.connect(self._toggle_play)
        controls.addWidget(self.btn_play)

        self.lbl_time = QLabel("00:00.00 / 00:00.00")
        self.lbl_time.setStyleSheet("font-family: monospace; font-size: 13px;")
        controls.addWidget(self.lbl_time)
        controls.addStretch()
        self.layout_main.addLayout(controls)

    def _setup_player(self):
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoSink(self.surface.sink)

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_state_changed)

        self._duration_ms = 0

    def release(self):
        """Release all file handles and resources."""
        self.player.stop()
        self.player.setSource(QUrl())
        self.surface._image = None
        self._pitched.release()

    def load(self, path: str):
        self.player.stop()
        self.player.setSource(QUrl())
        self.surface.reset_zoom()
        self._pitched.extract_audio(path)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            # Ensure pitched audio is active before play starts
            if self._pitched_active and not self._pitched._sink:
                self.player.setAudioOutput(None)
            self.player.play()

    def _on_position_changed(self, pos_ms: int):
        self.position_changed.emit(pos_ms)
        self.lbl_time.setText(f"{self._fmt(pos_ms)} / {self._fmt(self._duration_ms)}")

    def _on_duration_changed(self, dur_ms: int):
        self._duration_ms = dur_ms
        self.duration_changed.emit(dur_ms)

    @property
    def _use_pitched(self) -> bool:
        """Whether pitched audio should be active (speed != 1.0 or automation exists)."""
        return self._pitched_active

    def _on_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            if self._pitched_active and self._pitched.ready:
                self._pitched.play(self.player.position())
        else:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self._pitched.stop()

    def enable_pitched_audio(self, enabled: bool):
        """Switch between pitched audio and QMediaPlayer audio. Call once, not per-frame."""
        if enabled == self._pitched_active:
            return
        self._pitched_active = enabled
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if enabled:
            self.player.setAudioOutput(None)
            if playing and self._pitched.ready:
                self._pitched.play(self.player.position())
        else:
            self._pitched.stop()
            self.player.setAudioOutput(self.audio)

    def set_playback_rate(self, rate: float):
        """Update video + audio speed. Does NOT touch audio pipeline — call enable_pitched_audio separately."""
        self._speed = rate
        self._pitched.set_speed(rate)
        self.player.setPlaybackRate(rate)

    def seek(self, ms: int):
        self.player.setPosition(ms)
        if (self._pitched_active and self._pitched.ready
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState):
            self._pitched.play(ms)

    def get_duration_ms(self) -> int:
        return self._duration_ms

    @staticmethod
    def _fmt(ms: int) -> str:
        s = ms / 1000
        m = int(s // 60)
        s = s - m * 60
        return f"{m:02d}:{s:05.2f}"
