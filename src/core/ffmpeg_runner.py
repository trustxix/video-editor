import subprocess
import re
from pathlib import Path

from src.core.paths import get_ffmpeg, get_ffprobe


_nvenc_available: dict[str, bool] = {}  # cached per-encoder probe results


def _has_nvenc(encoder: str) -> bool:
    """Probe whether an NVENC encoder is available (cached)."""
    if encoder not in _nvenc_available:
        try:
            result = subprocess.run(
                [get_ffmpeg(), '-hide_banner', '-encoders'],
                capture_output=True, text=True, startupinfo=_hide_window(),
                timeout=10,
            )
            _nvenc_available[encoder] = encoder in (result.stdout or "")
        except Exception:
            _nvenc_available[encoder] = False
    return _nvenc_available[encoder]


def _encode_args(codec: str, crf: int) -> list[str]:
    """Build encoder arguments — NVENC if available, software fallback otherwise."""
    nvenc = "hevc_nvenc" if codec == "h265" else "h264_nvenc"
    if _has_nvenc(nvenc):
        return [
            "-c:v", nvenc,
            "-preset", "p5",
            "-rc", "vbr",
            "-b:v", "0",
            "-cq", str(crf),
        ]
    # Software fallback — works on any machine
    sw = "libx265" if codec == "h265" else "libx264"
    return ["-c:v", sw, "-preset", "medium", "-crf", str(crf)]


def _build_speed_audio_filter(speed: float) -> str:
    """Change audio speed with natural pitch shift (no pitch correction).
    Faster = higher pitch, slower = deeper pitch (vinyl effect).
    Only asetrate — no trailing aresample, which would undo the pitch shift."""
    rate = 48000
    adjusted = max(1, int(rate * speed))
    return f"aresample={rate},asetrate={adjusted}"


def _build_color_filters(brightness: float, exposure: float) -> list[str]:
    """Return the ffmpeg video filter fragments for brightness/exposure.

    - `brightness` is expected in [-0.5, 0.5] (eq.brightness native range).
    - `exposure` is in photographic stops, [-3.0, 3.0].

    Returns an empty list if both are effectively zero so we don't
    bloat the filter chain with no-op filters.
    """
    filters: list[str] = []
    # Use 1e-4 dead zones so tiny rounding-error values don't trigger
    # an actual filter.
    if abs(brightness) > 1e-4:
        # Clamp to eq.brightness's supported range just in case.
        b = max(-1.0, min(1.0, brightness))
        filters.append(f"eq=brightness={b:.4f}")
    if abs(exposure) > 1e-4:
        e = max(-3.0, min(3.0, exposure))
        filters.append(f"exposure=exposure={e:.4f}")
    return filters


# Frame interpolation was removed. Stock ffmpeg has no GPU-accelerated
# motion-compensated frame interpolator — libplacebo's frame_mixer is
# crossfade only (produces flickering at segment boundaries in multi-
# segment filter graphs) and minterpolate is CPU-only. Since this editor
# targets a strict GPU-only export pipeline (NVDEC → NVENC), slow-mo
# just produces whatever effective frame rate setpts gives you. At 0.05x
# from 120fps source that's 6fps — choppy but correct. Batch export
# makes up for the lack of smoothness by letting the user queue long
# unattended jobs.


def loudnorm_analyze(input_path: str, trim_start: float = 0,
                     trim_duration: float = 0,
                     target_lufs: float = -14.0) -> dict | None:
    """First pass of EBU R128 loudness normalization.

    Returns a dict with measured values for the second pass, or None on
    failure. `target_lufs` sets the integrated loudness target (e.g. -14
    for YouTube/Spotify, -23 for broadcast).
    """
    tp = min(-1.0, target_lufs + 2)  # true peak ceiling, always above target
    cmd = [get_ffmpeg(), '-hide_banner']
    if trim_start > 0:
        cmd += ['-ss', f'{trim_start:.3f}']
    if trim_duration > 0:
        cmd += ['-t', f'{trim_duration:.3f}']
    cmd += [
        '-i', input_path,
        '-af', f'loudnorm=I={target_lufs:.1f}:TP={tp:.1f}:LRA=11:print_format=json',
        '-f', 'null', '-',
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8',
            errors='replace', startupinfo=_hide_window(), timeout=120,
        )
        # loudnorm JSON is in stderr after the standard log output
        import json as _json
        text = result.stderr
        # Find the last JSON object in stderr with matched braces
        brace = text.rfind('{')
        if brace < 0:
            return None
        depth = 0
        end = -1
        for i in range(brace, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return None
        data = _json.loads(text[brace:end + 1])
        # Validate required keys
        for k in ('input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset'):
            if k not in data:
                return None
        return data
    except Exception:
        return None


def loudnorm_filter(measured: dict, target_lufs: float = -14.0) -> str:
    """Build the second-pass loudnorm filter string from first-pass measurements."""
    tp = min(-1.0, target_lufs + 2)
    return (
        f"loudnorm=I={target_lufs:.1f}:TP={tp:.1f}:LRA=11"
        f":measured_I={measured['input_i']}"
        f":measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}"
        f":measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}"
        f":linear=true"
    )


def build_command(
    input_path: str,
    output_path: str,
    trim_start: float | None = None,
    trim_end: float | None = None,
    crop_x: int | None = None,
    crop_y: int | None = None,
    crop_w: int | None = None,
    crop_h: int | None = None,
    codec: str = "h264",
    crf: int = 17,
    speed: float = 1.0,
    stretch_h: float = 1.0,
    stretch_v: float = 1.0,
    audio_mode: str = "copy",
    brightness: float = 0.0,
    exposure: float = 0.0,
    normalize_data: dict | None = None,
    target_lufs: float = -14.0,
) -> list[str]:
    cmd = [get_ffmpeg(), "-y"]

    if trim_start is not None and trim_start > 0:
        cmd += ["-ss", f"{trim_start:.3f}"]
    if trim_end is not None and trim_end > 0:
        # With -ss before -i, use -t (duration) not -to (absolute timestamp)
        duration = trim_end - (trim_start or 0)
        cmd += ["-t", f"{duration:.3f}"]

    cmd += ["-i", input_path]

    # ── Video filters ─────────────────────────────────────────
    vfilters = []
    has_crop = all(v is not None for v in (crop_x, crop_y, crop_w, crop_h))
    if has_crop:
        vfilters.append(f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}")
    if stretch_h != 1.0 or stretch_v != 1.0:
        vfilters.append(
            f"scale=trunc(iw*{stretch_h:.4f}/2)*2:trunc(ih*{stretch_v:.4f}/2)*2"
        )
    if speed != 1.0:
        vfilters.append(f"setpts=PTS/{speed:.4f}")
    # Color adjustments come after geometry and speed so they operate
    # on the final composited frames. Order within color filters (eq
    # then exposure) doesn't matter much — they commute for these
    # specific params — but we apply eq first for consistency.
    vfilters.extend(_build_color_filters(brightness, exposure))

    if vfilters:
        cmd += ["-vf", ",".join(vfilters)]
        cmd += _encode_args(codec, crf)
    else:
        # No video modifications — stream copy (zero quality loss)
        cmd += ["-c:v", "copy"]

    # ── Audio ─────────────────────────────────────────────────
    # Precedence: mute > speed change (forces re-encode with pitch shift) > reencode > copy
    # Normalization is chained after any speed filter when present.
    norm_filter = loudnorm_filter(normalize_data, target_lufs) if normalize_data else ""
    if audio_mode == "mute":
        cmd += ["-an"]
    elif speed != 1.0:
        af = _build_speed_audio_filter(speed)
        if norm_filter:
            af += "," + norm_filter
        cmd += ["-af", af]
        cmd += ["-c:a", "aac", "-b:a", "320k"]
    elif norm_filter:
        cmd += ["-af", norm_filter]
        cmd += ["-c:a", "aac", "-b:a", "320k"]
    elif audio_mode == "reencode":
        cmd += ["-c:a", "aac", "-b:a", "320k"]
    else:
        cmd += ["-c:a", "copy"]

    cmd += [output_path]
    return cmd


def prerender_audio(
    pcm_path: str,
    output_wav: str,
    keyframes: list[tuple[int, float]],
    base_speed: float,
    trim_start_ms: int,
    trim_end_ms: int,
) -> bool:
    """Pre-render audio with per-sample speed automation — identical to preview.

    Reads raw PCM extracted by FFmpeg, runs the same resampling loop as the
    live PitchedAudioPlayer, writes a WAV file.
    """
    import array, wave

    from src.core.speed_curve import SpeedCurve
    lane = SpeedCurve(keyframes, base_speed)

    BASE_RATE = 48000

    # Extract raw PCM from video
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    r = subprocess.run(
        [get_ffmpeg(), '-i', pcm_path, '-vn',
         '-f', 's16le', '-acodec', 'pcm_s16le',
         '-ac', '2', '-ar', str(BASE_RATE), '-'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, startupinfo=si,
    )
    if not r.stdout:
        return False
    pcm = array.array('h')
    pcm.frombytes(r.stdout)
    total_frames = len(pcm) // 2

    # Resample with automation — same algorithm as PitchedAudioPlayer._feed
    pos = float(trim_start_ms * BASE_RATE / 1000)
    end_pos = float(trim_end_ms * BASE_RATE / 1000)
    out = array.array('h')

    while pos < end_pos and int(pos) < total_frames - 1:
        time_ms = pos * 1000 / BASE_RATE
        speed = lane.get_speed_at(int(time_ms))
        idx = int(pos)
        frac = pos - idx
        b = idx * 2
        s1 = pcm[b];     s2 = pcm[b + 2]
        out.append(int(s1 + (s2 - s1) * frac))
        s1 = pcm[b + 1]; s2 = pcm[b + 3]
        out.append(int(s1 + (s2 - s1) * frac))
        pos += speed

    with wave.open(output_wav, 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(BASE_RATE)
        w.writeframes(out.tobytes())
    return True


def build_video_segments(
    keyframes: list[tuple[int, float]],
    base_speed: float,
    trim_start_ms: int,
    trim_end_ms: int,
    step_ms: int = 500,
) -> list[tuple[float, float, float]]:
    """Build (start_s, end_s, speed) segments for video-only export.

    Coarser than audio (500ms steps) since video speed changes are less
    perceptible than audio pitch changes.
    """
    from src.core.speed_curve import SpeedCurve
    lane = SpeedCurve(keyframes, base_speed)

    segments = []
    t = trim_start_ms
    while t < trim_end_ms:
        t_end = min(t + step_ms, trim_end_ms)
        mid = (t + t_end) // 2
        speed = lane.get_speed_at(mid)
        # Must match the UI's min/max speed range — if the UI
        # floor is lowered without lowering this too, the export will
        # silently clamp every keyframe back up and the user will think
        # the slow-down didn't work.
        speed = max(0.05, min(2.0, speed))
        segments.append((t / 1000, t_end / 1000, speed))
        t = t_end

    if not segments:
        return []
    merged = [segments[0]]
    for seg in segments[1:]:
        prev_start, prev_end, prev_speed = merged[-1]
        if abs(seg[2] - prev_speed) < 0.005:
            merged[-1] = (prev_start, seg[1], prev_speed)
        else:
            merged.append(seg)

    return merged


def export_with_automation(
    input_path: str,
    output_path: str,
    keyframes: list[tuple[int, float]],
    base_speed: float,
    trim_start_ms: int,
    trim_end_ms: int,
    crop_x=None, crop_y=None, crop_w=None, crop_h=None,
    codec: str = "h264",
    crf: int = 17,
    stretch_h: float = 1.0,
    stretch_v: float = 1.0,
    audio_mode: str = "copy",
    source_fps: float = 0.0,
    brightness: float = 0.0,
    exposure: float = 0.0,
    normalize_data: dict | None = None,
    target_lufs: float = -14.0,
    progress_callback=None,
    process_callback=None,
) -> bool:
    """Export with speed automation (pure GPU pipeline, no interpolation).

    Architecture — single-pass filter_complex:

      Input → [filter_complex:
                  for each segment i:
                      [0:v]trim=start=S:end=E,
                           setpts=(PTS-STARTPTS)/speed
                           [vi]
                  [v0][v1]...[vN]concat=n=N:v=1:a=0[vc]
                  [vc] (optional crop, stretch, fps normalize) [vout]
               ] → NVENC → muxed with pre-rendered audio → output

    No frame interpolation — at <1.0x speed the output just has fewer
    frames-per-second (e.g. 0.05x on 120fps source → 6fps effective).
    Choppy but correct. NVENC handles the encode on GPU.
    """
    import os, tempfile, shutil
    ffmpeg = get_ffmpeg()

    has_crop = all(v is not None for v in (crop_x, crop_y, crop_w, crop_h))

    temp_dir = tempfile.mkdtemp(prefix="ve_export_")
    segments = build_video_segments(keyframes, base_speed, trim_start_ms, trim_end_ms)
    if not segments:
        return False
    total_out_dur = sum((e - s) / spd for s, e, spd in segments)

    try:
        # ── Step 1: Pre-render audio (unchanged) ────────────
        audio_wav = os.path.join(temp_dir, "audio.wav")
        if audio_mode != "mute":
            if progress_callback:
                progress_callback(0)
            audio_ok = prerender_audio(input_path, audio_wav, keyframes, base_speed,
                                       trim_start_ms, trim_end_ms)
            if not audio_ok:
                # Log to export_error.log so the user can see why audio
                # is missing — logger alone goes nowhere under pythonw.
                try:
                    import datetime
                    from src.core.paths import get_config_dir
                    log_path = get_config_dir() / "export_error.log"
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"{'=' * 72}\n")
                        f.write(f"TIMESTAMP: {datetime.datetime.now().isoformat()}\n")
                        f.write(f"WARNING: Audio pre-render failed for {input_path}\n")
                        f.write("Export will continue with video only (no audio).\n\n")
                except Exception:
                    pass

        # ── Step 2: Build single filter_complex for all segments ──
        #
        # Per-segment chain:
        #   [0:v]trim=start=S:end=E,setpts=(PTS-STARTPTS)/speed[vi]
        filter_parts: list[str] = []
        concat_labels: list[str] = []
        for i, (seg_start, seg_end, speed) in enumerate(segments):
            label = f"v{i}"
            concat_labels.append(f"[{label}]")
            filter_parts.append(
                f"[0:v]trim=start={seg_start:.4f}:end={seg_end:.4f},"
                f"setpts=(PTS-STARTPTS)/{speed:.4f}[{label}]"
            )

        # Concat all segment streams. With N segments, the concat filter
        # joins them into a single output with continuous PTS. `v=1:a=0`
        # says each segment contributes 1 video stream and 0 audio.
        filter_parts.append(
            "".join(concat_labels) + f"concat=n={len(segments)}:v=1:a=0[vc]"
        )

        # Post-concat filters: crop, stretch, and fps normalization.
        # Applying these to the concatenated stream (instead of per-segment)
        # is more efficient and guarantees the encoder sees one consistent
        # pixel format / resolution / rate throughout.
        post = []
        if has_crop:
            post.append(f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}")
        if stretch_h != 1.0 or stretch_v != 1.0:
            post.append(
                f"scale=trunc(iw*{stretch_h:.4f}/2)*2:trunc(ih*{stretch_v:.4f}/2)*2"
            )
        if source_fps > 0:
            # Normalize to source fps so slow/fast segments have a
            # consistent rate through the encoder. Slow segments get
            # frames duplicated; fast segments get frames dropped.
            post.append(f"fps={source_fps:.3f}")
        # Color adjustments — applied post-concat so they work on the
        # final composited frame, same as in the non-automation path.
        post.extend(_build_color_filters(brightness, exposure))

        if post:
            filter_parts.append(f"[vc]{','.join(post)}[vout]")
            final_map = "[vout]"
        else:
            final_map = "[vc]"

        filter_complex = ";".join(filter_parts)

        # ── Step 3: Single NVENC encode pass ────────────────
        video_out = os.path.join(temp_dir, "video.mp4")
        cmd = [
            ffmpeg, "-y", "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", final_map,
        ]
        cmd += _encode_args(codec, crf)
        cmd += ["-an", video_out]

        ok = run_export(
            cmd, total_out_dur,
            progress_callback=(
                (lambda p: progress_callback(5 + p * 0.9)) if progress_callback else None
            ),
            process_callback=process_callback,
        )
        if not ok:
            return False

        # ── Step 4: Mux video + pre-rendered audio ──────────
        if audio_mode == "mute" or not os.path.exists(audio_wav):
            os.replace(video_out, output_path)
        else:
            # Re-analyze the pre-rendered WAV (not the source) for
            # normalization — speed automation changes the loudness.
            wav_norm = None
            if normalize_data:
                wav_norm = loudnorm_analyze(audio_wav, target_lufs=target_lufs)
            cmd = [ffmpeg, "-y", "-i", video_out, "-i", audio_wav,
                   "-c:v", "copy"]
            if wav_norm:
                cmd += ["-af", loudnorm_filter(wav_norm, target_lufs)]
            cmd += ["-c:a", "aac", "-b:a", "320k",
                    "-map", "0:v:0", "-map", "1:a:0", output_path]
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, startupinfo=_hide_window())
            if proc.returncode != 0:
                return False

        if progress_callback:
            progress_callback(100)
        return True

    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def get_output_path(input_path: str, suffix: str = "_edited") -> str:
    p = Path(input_path)
    return str(p.with_stem(p.stem + suffix))


def _hide_window():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si


def get_video_duration(input_path: str) -> float:
    result = subprocess.run(
        [get_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True, text=True, startupinfo=_hide_window(),
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def get_video_resolution(input_path: str) -> tuple[int, int]:
    result = subprocess.run(
        [get_ffprobe(), "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=p=0", input_path],
        capture_output=True, text=True, startupinfo=_hide_window(),
    )
    try:
        parts = result.stdout.strip().split(",")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 0, 0


def get_video_fps(input_path: str) -> float:
    """Probe the source video's frame rate.

    ffprobe reports r_frame_rate as a fraction like "120/1" or "30000/1001"
    (NTSC 29.97). We want the evaluated float. Falls back to 0.0 on any
    failure — callers should treat 0.0 as "unknown, skip fps-dependent
    features" rather than an error.
    """
    result = subprocess.run(
        [get_ffprobe(), "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True, text=True, startupinfo=_hide_window(),
    )
    raw = result.stdout.strip()
    if not raw or "/" not in raw:
        try:
            return float(raw)
        except ValueError:
            return 0.0
    num_str, _, den_str = raw.partition("/")
    try:
        num = float(num_str)
        den = float(den_str)
        if den == 0:
            return 0.0
        return num / den
    except ValueError:
        return 0.0


def extract_frame(input_path: str, timestamp_s: float) -> bytes | None:
    """Extract a single frame at the exact timestamp as BMP bytes.

    Uses `-ss` before `-i` for fast seeking: ffmpeg jumps to the nearest
    keyframe then decodes forward to the target frame. This is frame-
    accurate in modern ffmpeg (4+), unlike QMediaPlayer.setPosition()
    which snaps to the keyframe on Windows.

    Returns raw BMP data suitable for QImage.loadFromData(), or None.
    """
    cmd = [
        get_ffmpeg(), '-loglevel', 'quiet',
        '-ss', f'{timestamp_s:.6f}',
        '-i', input_path,
        '-frames:v', '1',
        '-f', 'image2pipe', '-vcodec', 'bmp',
        'pipe:1',
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, startupinfo=_hide_window(), timeout=5,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def run_export(cmd: list[str], duration: float, progress_callback=None, process_callback=None) -> bool:
    """Run ffmpeg and report progress via callback(percent: float).

    If process_callback is provided, it receives the Popen object for
    external cancellation. On non-zero exit, writes the full command
    and stderr tail to config/export_error.log so the user (and us)
    can actually see why ffmpeg failed — the UI's "FFmpeg returned an
    error" dialog alone is useless without the underlying message.
    """
    process = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
        universal_newlines=True, encoding="utf-8", errors="replace",
        startupinfo=_hide_window(),
    )
    if process_callback:
        process_callback(process)

    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")

    # Bounded stderr tail — we only need the tail for failure diagnosis,
    # and ffmpeg stderr is chatty (one line per frame for verbose builds).
    stderr_tail: list[str] = []
    MAX_TAIL = 80

    for line in process.stderr:
        stderr_tail.append(line)
        if len(stderr_tail) > MAX_TAIL:
            stderr_tail.pop(0)
        match = time_pattern.search(line)
        if match and duration > 0 and progress_callback:
            h, m, s = int(match.group(1)), int(match.group(2)), int(match.group(3))
            frac_str = match.group(4)
            frac = int(frac_str) / (10 ** len(frac_str))
            current = h * 3600 + m * 60 + s + frac
            pct = min(current / duration * 100, 100.0)
            progress_callback(pct)

    process.wait()

    if process.returncode != 0:
        # Persist the failure so the user can report it or we can debug.
        try:
            import datetime
            from src.core.paths import get_config_dir
            log_path = get_config_dir() / "export_error.log"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("=" * 72 + "\n")
                f.write(f"TIMESTAMP: {datetime.datetime.now().isoformat()}\n")
                f.write(f"EXIT CODE: {process.returncode}\n")
                f.write("COMMAND:\n")
                for arg in cmd:
                    # Quote args containing spaces so the log can be
                    # copy-pasted into a shell for manual reproduction.
                    if " " in arg or "(" in arg or "[" in arg:
                        f.write(f'  "{arg}"\n')
                    else:
                        f.write(f"  {arg}\n")
                f.write("STDERR TAIL:\n")
                f.write("".join(stderr_tail))
                f.write("\n")
        except Exception:
            pass
        return False

    return True
