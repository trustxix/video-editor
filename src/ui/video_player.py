import array
import subprocess
import threading
from collections import deque

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QStyle
from src.ui.widgets import ClickSlider
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QVideoFrame, QAudioSink, QAudioFormat
from PyQt6.QtCore import Qt, QRect, QPointF, pyqtSignal, QUrl, QTimer
from PyQt6.QtGui import QPainter, QImage

from src.core.paths import get_ffmpeg
from src.core.speed_curve import SPEED_LOOKUP_BLOCK
from src.ui.frame_converter import FrameConverter


class PitchedAudioPlayer:
    """Vinyl-style audio via push-mode QAudioSink at fixed 48 kHz.

    Resamples PCM on the fly using the current speed value.  Speed can change
    smoothly (e.g. during automation ramps) without recreating the sink —
    just update self._speed and the next _feed() tick uses the new rate.
    """

    _BASE_RATE = 48000
    _CHANNELS = 2
    _FRAME_BYTES = _CHANNELS * 2
    # NOTE: the sink depth is deliberately left at the platform default
    # (250 ms on Windows). Requesting 500 ms was tried and measured: with the
    # frame conversion off the GUI thread the feed timer no longer misses
    # ticks, so the extra depth changed nothing (feed health 96-98% and a
    # 16-19 ms worst-case deficit either way) while making two things worse —
    # a manual speed change mid-playback leaves a whole buffer of audio still
    # running at the old rate, so the transient before `resync_audio_if_drifting`
    # settles it doubles; and the buffer prime doubles in cost. Depth is read
    # from `bufferSize()` everywhere, so nothing here assumes a value.
    #
    # Output frames between position marks on the resampling path. 1024 frames
    # is 21 ms of output, so the linear interpolation `playback_position_ms`
    # does between marks stays a good fit even across the steepest speed ramp,
    # and 128 marks still span 2.7 s of output against the sink queue.
    _MARK_FRAMES = 1024

    def __init__(self):
        self._pcm: array.array | None = None  # array('h'), interleaved stereo
        self._sink: QAudioSink | None = None
        self._io = None
        self._timer = QTimer()
        self._timer.timeout.connect(self._feed)
        self._speed = 1.0
        self._volume = 1.0  # Linear 0.0-1.0 — re-applied to each new sink in play()
        self._pos = 0.0  # fractional frame position in input (WRITE cursor)
        # Output frames handed to the sink since play(), plus a short history
        # of (written_frames, source_pos) so the source position of the audio
        # currently being HEARD can be recovered — the write cursor above runs
        # ahead of it by whatever is still queued in the sink.
        self._written = 0
        self._marks: deque[tuple[int, float]] = deque(maxlen=128)
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

    def resync_to(self, source_ms: int):
        """Snap the read cursor to the given SOURCE timeline position (ms).

        Used to relock audio to the video clock when they drift — the audio
        runs off its own QAudioSink cadence while video runs off QMediaPlayer,
        so over long playback at speed != 1 the two can creep apart."""
        self._pos = max(0.0, float(source_ms) * self._BASE_RATE / 1000.0)

    def shift_cursor(self, delta_ms: float):
        """Nudge the write cursor by delta_ms of SOURCE time.

        Unlike resync_to this preserves the cursor's lead over the sink's
        queued audio, which is what keeps the heard audio aligned with the
        displayed frame — it corrects the error without discarding the lead.
        """
        self._pos = max(0.0, self._pos + delta_ms * self._BASE_RATE / 1000.0)

    def queued_frames(self) -> int:
        """Output frames handed to the sink but not yet played."""
        if self._sink is None:
            return 0
        size = self._sink.bufferSize()
        if size <= 0:
            return 0
        queued = (size - self._sink.bytesFree()) // self._FRAME_BYTES
        return max(0, min(self._written, queued))

    def _source_pos_at(self, out_frame: int) -> float:
        """Source frame position that output frame `out_frame` was built from."""
        marks = self._marks
        if not marks:
            return self._pos
        prev_w, prev_p = marks[0]
        if out_frame <= prev_w:
            return prev_p
        for w, p in marks:
            if w > out_frame:
                span = w - prev_w
                if span <= 0:
                    return prev_p
                return prev_p + (p - prev_p) * (out_frame - prev_w) / span
            prev_w, prev_p = w, p
        # Newer than the last mark — extrapolate at the current read rate.
        return prev_p + (out_frame - prev_w) * self._speed

    def playback_position_ms(self) -> float:
        """Source-timeline position (ms) of the audio being heard right now.

        `self._pos` is the WRITE cursor. It legitimately runs ahead of what the
        listener hears by however much audio is sitting in the sink's buffer —
        in source time that lead is (queued ms x speed), and a Windows
        QAudioSink buffer is 250 ms, so at 1.35x the write cursor is a third of
        a second ahead. That lead is correct: it is exactly what makes the
        audio land in sync when it finally plays. Only this position may be
        compared against the video clock.
        """
        played = max(0, self._written - self.queued_frames())
        return self._source_pos_at(played) * 1000.0 / self._BASE_RATE

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
        self._written = 0
        self._marks.clear()
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
        # Fill everything the sink will take. There used to be a 960-frame cap
        # here, which is 20 ms of audio per 15 ms tick: the buffer refilled at
        # only +5 ms per tick, so recovering from empty took ~750 ms and any
        # missed tick left it dry. In steady state a tick frees fewer frames
        # than that anyway, so lifting the cap only changes the recovery path
        # and the initial prime, which now fills the buffer in one write —
        # ~2.8 ms for a 250 ms buffer through the interpolation loop, once per
        # play/seek, and effectively free on the 1.0x fast path.
        # `bytesFree()` is bounded by the sink's own buffer, so this is bounded.
        free = self._sink.bytesFree()
        n = free // self._FRAME_BYTES
        if n <= 0:
            return
        total = len(pcm) // self._CHANNELS
        pos = self._pos
        auto = self._automation
        has_auto = auto is not None and auto.get_keyframes()

        # Fast path: at exactly 1.0x with no automation there is NO resampling,
        # so copy a contiguous PCM slice instead of running the per-sample
        # Python interpolation loop. This is the common playback case and the
        # single biggest cost on the 15ms UI-thread tick.
        # One mark is exact here: output maps to source at a constant rate, so
        # the straight line playback_position_ms draws between marks IS the
        # mapping.
        if not has_auto and self._speed == 1.0:
            self._marks.append((self._written, pos))
            idx = int(pos)
            take = max(0, min(n, total - idx))
            b = idx * 2
            chunk = pcm[b:b + take * 2] if take > 0 else array.array('h')
            if take < n:  # pad the tail with silence
                chunk = chunk + array.array('h', bytes((n - take) * self._FRAME_BYTES))
            self._pos = pos + take
            self._written += n
            self._io.write(chunk.tobytes())
            return

        # Preallocate the output (zero-filled) and assign by index instead of
        # growing it with append() — this runs every 15ms on the UI thread,
        # so avoiding 2*n reallocating appends per tick keeps it cheap. Any
        # frames past the end stay silent (the zero fill).
        out = array.array('h', bytes(self._FRAME_BYTES * n))

        # Speed is sampled once per SPEED_LOOKUP_BLOCK output frames rather
        # than once per frame: the curve moves imperceptibly over 1.33ms of
        # audio, and the lookup was the dominant cost of this UI-thread tick.
        #
        # Marks are recorded INSIDE the loop, every _MARK_FRAMES, not once per
        # call. `playback_position_ms` maps the sink's play cursor back to a
        # source position by interpolating linearly between marks, and this
        # path is the one where the read rate bends mid-write. A whole-buffer
        # write (which play() now does) under a single mark draws one chord
        # right across the curve: measured 194-419 ms of phantom position error
        # over a whole-buffer prime, versus the 80 ms threshold in
        # `resync_audio_if_drifting`. It reads that as drift, and — because the
        # reported position comes from these marks, not from `_pos` —
        # `shift_cursor` cannot reduce it, so it re-fires every tick. Marking
        # every 1024 frames bounds the chord to ~21 ms of output no matter how
        # much is written at once (measured error 0.5-0.7 ms), and the
        # 128-entry ring still spans 2.7 s against a quarter-second queue.
        i = 0
        ended = False
        next_mark = 0
        while i < n and not ended:
            if i >= next_mark:
                self._marks.append((self._written + i, pos))
                next_mark = i + self._MARK_FRAMES
            if has_auto:
                speed = auto.get_speed_at(int(pos * 1000 / self._BASE_RATE))
            else:
                speed = self._speed
            stop = min(i + SPEED_LOOKUP_BLOCK, n)
            while i < stop:
                idx = int(pos)
                # Strict bound: we read pcm[b], pcm[b+1], pcm[b+2], pcm[b+3]
                # where b = idx*2, so idx must be <= total-2 for all four to
                # be in range. Past that we leave the rest silent and stop.
                if idx > total - 2:
                    ended = True
                    break
                frac = pos - idx
                b = idx * 2
                j = i * 2
                s1 = pcm[b];     s2 = pcm[b + 2]
                out[j] = int(s1 + (s2 - s1) * frac)
                s1 = pcm[b + 1]; s2 = pcm[b + 3]
                out[j + 1] = int(s1 + (s2 - s1) * frac)
                pos += speed
                i += 1
        self._pos = pos
        self._written += n
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

        # Frame stepping: when True, ignore QVideoSink frames so the
        # accurate ffmpeg-decoded frame isn't overwritten by QMediaPlayer's
        # keyframe-snapped seek. Cleared when playback resumes.
        #
        # Set up before the converter's first use: assigning `_stepping`
        # goes through the property below, which touches both of these.
        self.__stepping = False
        self._frame_gen = 0

        # QVideoFrame -> QImage runs on a worker thread and is coalesced to
        # the display cadence. Doing it inline here saturated the GUI thread
        # at 1440p120 and starved the pitched-audio feed timer — see
        # frame_converter.py.
        self._converter = FrameConverter(self)
        self._converter.image_ready.connect(self._on_image_ready)

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

    @property
    def _stepping(self) -> bool:
        return self.__stepping

    @_stepping.setter
    def _stepping(self, value: bool):
        """Assigning this flag also invalidates any conversion in flight.

        Callers set `_stepping = True` and then assign `_image` directly (an
        ffmpeg-decoded frame-step or colour-preview image). A conversion that
        started before that would land afterwards and overwrite it, so the
        generation token is bumped here and stale results are dropped in
        `_on_image_ready`. Going back to False invalidates too: the held
        image is being abandoned and the next live frame supersedes it.

        Re-assigning the same value is a no-op: the colour sliders clear the
        flag on every change tick, and throwing away a queued frame each time
        would visibly thin out playback during a drag.
        """
        value = bool(value)
        if value == self.__stepping:
            return
        self.__stepping = value
        self._frame_gen += 1
        self._converter.discard_pending()

    def _on_frame(self, frame: QVideoFrame):
        if self._stepping:
            return  # Keep the accurate stepped frame on screen
        # Just a refcounted handle — no pixel work on the GUI thread.
        self._converter.submit(frame, self._frame_gen)

    def _on_image_ready(self, img: QImage, gen: int):
        if gen != self._frame_gen or self._stepping:
            return  # Superseded by a frame-step, colour preview or new clip
        if img.isNull():
            # QVideoSink emits a null frame when a source is dropped, and
            # release() drops one every time the queue is emptied. Storing it
            # would burn the one-shot below: `first` is computed from
            # `_image is None`, so the null would consume the only
            # zoom_changed the CropOverlay gets, and it would fire while
            # video_crop_rect() still reports the bare widget rect — leaving
            # the crop box mapped to the full widget, black bars included,
            # for the clip loaded next. _PlayerSurface has always had this
            # guard; VideoSurface needs it for the same reason.
            return
        first = self._image is None
        self._image = img
        self.update()
        if first:
            self.zoom_changed.emit()

    def clear_image(self):
        """Drop the displayed frame and invalidate anything in flight."""
        self._frame_gen += 1
        self._converter.discard_pending()
        self._image = None
        self.update()

    def shutdown(self):
        """Stop the conversion thread. Call once, on application shutdown."""
        self._converter.shutdown()

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
        """Release all file handles and resources.

        Reusable — the editor calls this when the queue is emptied and then
        loads clips again, so the frame-conversion thread stays alive. Use
        shutdown() for the terminal teardown."""
        self.player.stop()
        self.player.setSource(QUrl())
        self.surface.clear_image()
        self._pitched.release()

    def shutdown(self):
        """Terminal teardown — release, then stop the conversion thread."""
        self.release()
        self.surface.shutdown()

    def load(self, path: str):
        self.player.stop()
        self.player.setSource(QUrl())
        self.surface.reset_zoom()
        # A fresh clip must always render live frames — clear any leftover
        # frame-step / colour-preview hold so the new video isn't frozen.
        self.surface._stepping = False
        self._pitched.extract_audio(path)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()

    def resync_audio_if_drifting(self, video_ms: int, threshold_ms: int = 80):
        """Relock pitched audio to the video clock when drift exceeds the
        threshold. Only acts while pitched audio is the active, playing source;
        the threshold keeps a tiny, rare correction from becoming a constant
        audible nudge.

        The comparison uses the pitched player's PLAYBACK position, never its
        write cursor. The write cursor legitimately leads by whatever is queued
        in the sink, and in source time that lead is (queued ms x speed) — with
        a 250 ms Windows QAudioSink buffer that is 250 ms at 1.0x and 500 ms at
        2.0x. Comparing the write cursor read the lead as drift, so above
        ~1.3x this fired on essentially every position tick and yanked the read
        cursor backwards each time, chopping playback into restarting fragments
        (the "stutter above 1.35x" bug). Correcting by the measured error keeps
        the lead intact."""
        if not (self._pitched_active and self._pitched.ready):
            return
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        error_ms = video_ms - self._pitched.playback_position_ms()
        if abs(error_ms) > threshold_ms:
            self._pitched.shift_cursor(error_ms)

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
