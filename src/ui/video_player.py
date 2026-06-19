import array
import subprocess
import threading

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QStyle
from src.ui.widgets import ClickSlider
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
        self._volume = 1.0  # Linear 0.0-1.0 — re-applied to each new sink in play()
        self._pos = 0.0  # fractional frame position in input
        self._gen = 0
        self._automation = None  # set to AutomationLane for per-sample speed lookup
        self._extract_proc: subprocess.Popen | None = None  # current ffmpeg PCM extraction
        # Guards _pcm and _extract_proc against races between the daemon
        # extract thread (writes _pcm and _extract_proc) and the main
        # thread (reads them in play/_feed/release).
        self._lock = threading.Lock()

    def extract_audio(self, video_path: str):
        self.stop()
        # Kill any in-progress extraction before starting a new one —
        # without this, rapid navigation spawns N concurrent ffmpeg
        # processes that keep decoding audio for clips already left behind.
        with self._lock:
            if self._extract_proc is not None:
                try:
                    if self._extract_proc.poll() is None:
                        self._extract_proc.terminate()
                except Exception:
                    pass
                self._extract_proc = None
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
            with self._lock:
                # Avoid stomping a newer extraction's proc reference.
                if gen == self._gen:
                    self._extract_proc = proc
            stdout, _ = proc.communicate()
            if stdout:
                # Drop a dangling odd byte rather than crashing on a
                # truncated PCM stream. The audio plays through to within
                # one sample frame of the actual end either way.
                rem = len(stdout) % 2  # array('h') itemsize
                if rem:
                    stdout = stdout[:-rem]
            if not stdout:
                return
            a = array.array('h')
            a.frombytes(stdout)
            with self._lock:
                # Only publish if no newer extraction has already started.
                if gen == self._gen:
                    self._pcm = a
        except Exception:
            pass

    def set_speed(self, speed: float):
        """Update speed smoothly — no sink recreation needed."""
        self._speed = speed

    def set_volume(self, volume: float):
        """Set preview volume (linear 0.0-1.0).

        Stored on the player and re-applied each time a new QAudioSink
        is created in play() — without that, the next play() call would
        start a fresh sink at 100% regardless of the slider position.
        """
        self._volume = max(0.0, min(1.0, volume))
        if self._sink is not None:
            self._sink.setVolume(self._volume)

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
        self._sink.setVolume(self._volume)  # Re-apply persisted slider value
        self._io = self._sink.start()
        self._pos = float(position_ms * self._BASE_RATE / 1000)
        self._feed()  # prime buffer immediately
        self._timer.start(15)

    def _feed(self):
        # Snapshot _pcm under the lock so the daemon thread can't swap it
        # mid-loop. The local `pcm` reference keeps the array alive even if
        # `self._pcm` gets reassigned right after this read.
        with self._lock:
            pcm = self._pcm
        if not self._io or not pcm or not self._sink:
            return
        free = self._sink.bytesFree()
        n = min(960, free // self._FRAME_BYTES)
        if n <= 0:
            return
        total = len(pcm) // self._CHANNELS
        # Preallocate the output (zero-filled) and assign by index instead of
        # growing it with append() — this runs every 15ms on the UI thread,
        # so avoiding 2*n reallocating appends per tick keeps it cheap. Any
        # frames past the end stay silent (the zero fill).
        out = array.array('h', bytes(self._FRAME_BYTES * n))
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
            # Strict bound: we read pcm[b], pcm[b+1], pcm[b+2], pcm[b+3]
            # where b = idx*2, so idx must be <= total-2 for all four to
            # be in range. Past that we leave the rest silent and stop.
            if idx > total - 2:
                break
            frac = pos - idx
            b = idx * 2
            j = i * 2
            s1 = pcm[b];     s2 = pcm[b + 2]
            out[j] = int(s1 + (s2 - s1) * frac)
            s1 = pcm[b + 1]; s2 = pcm[b + 3]
            out[j + 1] = int(s1 + (s2 - s1) * frac)
            pos += speed
        self._pos = pos
        self._io.write(out.tobytes())

    def stop(self):
        self._timer.stop()
        if self._sink:
            self._sink.stop()
            self._sink = None
        self._io = None

    def release(self):
        self.stop()
        # Kill any in-progress audio extraction so the ffmpeg child doesn't
        # outlive pythonw as an orphan. Snapshot under the lock so the
        # daemon thread can't reassign the proc field while we're tearing
        # it down.
        with self._lock:
            proc = self._extract_proc
            self._extract_proc = None
            self._pcm = None
            self._gen += 1
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception:
                pass

    @property
    def ready(self) -> bool:
        with self._lock:
            return bool(self._pcm)


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

        # Color adjustments (preview only — the export pipeline applies
        # precise ffmpeg `eq` and `exposure` filters from the same values).
        # `_brightness` is normalized -0.5..0.5 (matches ffmpeg eq.brightness).
        # `_exposure` is in stops, -3.0..3.0 (matches ffmpeg exposure.exposure).
        self._brightness = 0.0
        self._exposure = 0.0

        # Frame stepping: when True, ignore QVideoSink frames so the
        # accurate ffmpeg-decoded frame isn't overwritten by QMediaPlayer's
        # keyframe-snapped seek. Cleared when playback resumes.
        self._stepping = False

    def _on_frame(self, frame: QVideoFrame):
        if self._stepping:
            return  # Keep the accurate stepped frame on screen
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

    def set_color_adjust(self, brightness: float, exposure: float):
        """Update preview brightness/exposure.

        `brightness` is normalized -0.5..0.5 (matches ffmpeg eq).
        `exposure` is in photographic stops, -3.0..3.0 (matches ffmpeg
        exposure filter). Both are clamped defensively. paintEvent uses
        the combined effect to draw an alpha overlay — see the comment
        in paintEvent for why that's approximate.
        """
        self._brightness = max(-0.5, min(0.5, brightness))
        self._exposure = max(-3.0, min(3.0, exposure))
        self.update()

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
            video_rect = self.video_display_rect()
            p.drawImage(video_rect, self._image)

            # Color adjust overlay — APPROXIMATE preview only. True
            # brightness/exposure math requires per-pixel access which
            # we can't cheaply afford in Python for live 60fps preview.
            # Instead, we alpha-blend a black or white rectangle over
            # the frame with opacity proportional to the combined
            # adjustment. At small magnitudes this matches visually;
            # at large magnitudes it washes toward gray rather than
            # clipping to white like true additive brightness. That's
            # fine for preview feedback — the exported file uses the
            # real ffmpeg filters and will look correct.
            #
            # Combined effect: brightness is already in normalized
            # [-0.5, 0.5]. Exposure is in stops: +1 stop ≈ doubling
            # which feels roughly like +0.25 brightness on mid-grey,
            # so exposure_stops * 0.25 gives a reasonable preview
            # approximation.
            combined = self._brightness + self._exposure * 0.25
            if combined != 0:
                alpha = min(abs(combined), 0.85)  # never fully opaque
                p.setOpacity(alpha)
                color = Qt.GlobalColor.white if combined > 0 else Qt.GlobalColor.black
                p.fillRect(video_rect, color)
                p.setOpacity(1.0)

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
    volume_changed = pyqtSignal(int)    # 0-100, fired on slider release for persistence

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

    def set_volume(self, value: int):
        """Sync the slider from a persisted setting (0-100).

        Doesn't emit volume_changed — this is the "load from disk" path,
        not a user action, so we don't want to trigger a save-to-disk.
        """
        clamped = max(0, min(100, int(value)))
        self.vol_slider.blockSignals(True)
        self.vol_slider.setValue(clamped)
        self.vol_slider.blockSignals(False)
        # Still apply the volume to the audio paths — blockSignals only
        # stops valueChanged from firing, not our explicit apply.
        self._on_volume_changed(clamped)

    def _on_volume_changed(self, value: int):
        """Live apply slider value (0-100) to both audio paths."""
        linear = value / 100.0
        self.audio.setVolume(linear)
        self._pitched.set_volume(linear)

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
        # Monospace so digits don't jitter as the time advances; font
        # size follows the app font so DPI scaling works correctly.
        self.lbl_time.setStyleSheet("font-family: monospace;")
        controls.addWidget(self.lbl_time)
        controls.addStretch()

        # Preview volume slider — affects playback only, never the export.
        vol_icon = QLabel()
        vol_icon.setPixmap(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume).pixmap(16, 16)
        )
        vol_icon.setToolTip("Preview volume (does not affect exports)")
        controls.addWidget(vol_icon)

        self.vol_slider = ClickSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        self.vol_slider.setFixedWidth(80)
        self.vol_slider.setFixedHeight(16)
        self.vol_slider.setToolTip("Preview volume (does not affect exports)")
        self.vol_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 3px; }
            QSlider::handle:horizontal { width: 8px; margin: -3px 0; border-radius: 4px; }
        """)
        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        self.vol_slider.sliderReleased.connect(
            lambda: self.volume_changed.emit(self.vol_slider.value())
        )
        controls.addWidget(self.vol_slider)

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
            self.surface._stepping = False  # allow live frames to render again
            if self._pitched_active and self._pitched.ready:
                # Delay by one tick so MainWindow._on_playback_state can
                # correct the position first (for frame-step → play sync).
                # Guard: only start if still playing when the tick fires
                # (user may have paused in the meantime).
                QTimer.singleShot(0, lambda: (
                    self._pitched.play(self.player.position())
                    if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                    else None
                ))
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

    def scrub(self, ms: int):
        """Video-only seek for live scrubbing — moves the displayed frame
        WITHOUT tearing down and recreating the pitched QAudioSink (which would
        stutter the audio on every drag pixel). Audio is resynced once when the
        scrub settles, via seek()."""
        self.player.setPosition(ms)

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
