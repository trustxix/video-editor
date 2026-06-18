import os
import subprocess
import re
from pathlib import Path
from typing import Callable, Optional

from src.core.paths import get_ffmpeg, get_ffprobe
from src.core.log_setup import log


# Per-encoder probe results: True = available, False = unavailable, value
# is dropped if probe failed entirely (we re-probe next call).
_nvenc_available: dict[str, bool] = {}
# Reason for unavailability (human-readable), set on the first failed probe.
_nvenc_reason: dict[str, str] = {}


def _has_nvenc(encoder: str) -> bool:
    """Probe whether an NVENC encoder is available (cached).

    Caches BOTH the result and (on failure) a human-readable reason
    accessible via get_encoder_status(). Caching the reason means we don't
    silently downgrade to software encoding without an explanation."""
    if encoder in _nvenc_available:
        return _nvenc_available[encoder]
    try:
        result = subprocess.run(
            [get_ffmpeg(), '-hide_banner', '-encoders'],
            capture_output=True, text=True, startupinfo=_hide_window(),
            timeout=10,
        )
        if result.returncode != 0:
            _nvenc_available[encoder] = False
            _nvenc_reason[encoder] = f"ffmpeg -encoders exited {result.returncode}"
            log().warning(f"NVENC probe failed for {encoder}: {_nvenc_reason[encoder]}")
            return False
        if encoder in (result.stdout or ""):
            _nvenc_available[encoder] = True
            return True
        _nvenc_available[encoder] = False
        _nvenc_reason[encoder] = f"{encoder} not listed in ffmpeg -encoders (no NVIDIA GPU?)"
        log().info(f"NVENC unavailable: {_nvenc_reason[encoder]}")
        return False
    except subprocess.TimeoutExpired:
        _nvenc_available[encoder] = False
        _nvenc_reason[encoder] = "ffmpeg encoder probe timed out (10s)"
        log().warning(_nvenc_reason[encoder])
        return False
    except FileNotFoundError:
        _nvenc_available[encoder] = False
        _nvenc_reason[encoder] = "ffmpeg.exe not found"
        log().error(_nvenc_reason[encoder])
        return False
    except OSError as e:
        _nvenc_available[encoder] = False
        _nvenc_reason[encoder] = f"ffmpeg launch failed: {e}"
        log().error(_nvenc_reason[encoder])
        return False


def get_encoder_status() -> tuple[str, str]:
    """Returns ('nvenc', '') if GPU encoding active, or ('software', reason) when
    we've fallen back. Triggers a probe of h264_nvenc if not yet checked."""
    # Use h264_nvenc as the canonical check (hevc_nvenc availability tracks it
    # within driver versions). Prefer to report the first encoder we probed.
    if not _nvenc_available:
        _has_nvenc("h264_nvenc")
    if any(_nvenc_available.values()):
        return ("nvenc", "")
    reason = _nvenc_reason.get("h264_nvenc") or next(iter(_nvenc_reason.values()), "unknown")
    return ("software", reason)


# NVENC hardware-minimum frame sizes (Turing+ generation).
# h264_nvenc width minimum is documented at 145 px on Turing+ NVIDIA Developer
# Forums; the empirical height limit is ~49 px. hevc_nvenc has a higher
# minimum; we use 256×144 to also cover NVENC HEVC's smallest stable profile.
# Below these dims the encoder accepts the frame but silently produces a
# 0-byte output and ffmpeg returns an error — fall back to software here.
NVENC_MIN_DIMS: dict[str, tuple[int, int]] = {
    "h264_nvenc": (145, 49),
    "hevc_nvenc": (256, 144),
}


def _below_nvenc_minimum(encoder: str, output_w: int | None, output_h: int | None) -> bool:
    """True when an NVENC encoder would silently fail at the given output dim."""
    if output_w is None or output_h is None:
        return False
    if "nvenc" not in encoder:
        return False
    min_w, min_h = NVENC_MIN_DIMS.get(encoder, (0, 0))
    return output_w < min_w or output_h < min_h


def _software_encode_args(codec: str, crf: int) -> list[str]:
    sw = "libx265" if codec == "h265" else "libx264"
    return ["-c:v", sw, "-preset", "medium", "-crf", str(crf)]


def _effective_output_dims(
    crop_w: int | None,
    crop_h: int | None,
    stretch_h: float = 1.0,
    stretch_v: float = 1.0,
) -> tuple[int | None, int | None]:
    """Compute the effective post-filter output dimensions when knowable.

    Returns (None, None) when the source dimension would be required (i.e.
    no crop is applied), since callers don't generally probe the source
    here. This matches the ``scale=trunc(iw*sh/2)*2`` filter used by
    build_command — both the floor and the even-rounding apply post-stretch.
    """
    if crop_w is None or crop_h is None:
        return (None, None)
    eff_w = int(crop_w * stretch_h) // 2 * 2
    eff_h = int(crop_h * stretch_v) // 2 * 2
    return (max(eff_w, 0), max(eff_h, 0))


def _encode_args(
    codec: str,
    crf: int,
    auto_preset: dict | None = None,
    output_w: int | None = None,
    output_h: int | None = None,
) -> list[str]:
    """Build encoder arguments — auto-preset > NVENC > software fallback.

    When ``output_w`` and ``output_h`` are supplied and below the NVENC hardware
    minimum (see ``NVENC_MIN_DIMS``), forces software encoding regardless of
    auto-preset or NVENC availability — without this the encoder writes a
    0-byte file and the export silently fails. Callers without dim info pass
    None/None and the historical probe-NVENC-or-software path is preserved.
    """
    # Determine which encoder we'd actually emit, so the dim check is
    # consistent across the auto-preset and probe paths.
    if auto_preset:
        target_encoder = str(auto_preset.get("encoder", "libx264"))
    else:
        target_encoder = "hevc_nvenc" if codec == "h265" else "h264_nvenc"

    if _below_nvenc_minimum(target_encoder, output_w, output_h):
        log().info(
            f"Output {output_w}x{output_h} below {target_encoder} hardware "
            f"minimum {NVENC_MIN_DIMS[target_encoder]}; using software encoder"
        )
        return _software_encode_args(codec, crf)

    # Auto-optimized preset from FFmpeg AutoResearch takes priority
    if auto_preset:
        from src.core.auto_presets import get_encode_args
        return get_encode_args(auto_preset)

    nvenc = target_encoder  # already h264_nvenc / hevc_nvenc here
    if _has_nvenc(nvenc):
        return [
            "-c:v", nvenc,
            "-preset", "p5",
            "-rc", "vbr",
            "-b:v", "0",
            "-cq", str(crf),
        ]
    # Software fallback — works on any machine
    return _software_encode_args(codec, crf)


def _build_speed_audio_filter(speed: float) -> str:
    """Change audio speed with natural pitch shift (no pitch correction).
    Faster = higher pitch, slower = deeper pitch (vinyl effect).
    Only asetrate — no trailing aresample, which would undo the pitch shift."""
    rate = 48000
    adjusted = max(1, int(rate * speed))
    return f"aresample={rate},asetrate={adjusted}"


# librubberband ('rubberband' filter) availability — cached single probe.
# Needed for formant shifting; absent in a stripped ffmpeg build we'd otherwise
# emit an invalid filter graph that fails the whole export.
_rubberband_available: dict[str, bool] = {}


def _has_rubberband() -> bool:
    """Probe whether ffmpeg exposes the librubberband 'rubberband' filter (cached)."""
    if "rb" in _rubberband_available:
        return _rubberband_available["rb"]
    try:
        result = subprocess.run(
            [get_ffmpeg(), "-hide_banner", "-filters"],
            capture_output=True, text=True, startupinfo=_hide_window(),
            timeout=10,
        )
        ok = result.returncode == 0 and "rubberband" in (result.stdout or "")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        _rubberband_available["rb"] = False
        log().warning(f"rubberband probe failed: {e} — formant shift disabled")
        return False
    _rubberband_available["rb"] = ok
    if not ok:
        log().warning("ffmpeg has no 'rubberband' filter — formant shift disabled")
    return ok


# Independent formant shifting is built from a two-pass rubberband chain. The
# bundled rubberband (R2 engine) honours formant=preserved only when pitch is
# shifted UP, so the reliable independent direction is DOWNWARD (deeper/warmer
# timbre). The UI exposes the range the spectral regression test proves
# (tests/test_ffmpeg_runner.py::test_formant_*). Magnitude is clamped so the
# pitch ratio 2**(s/12) stays well inside rubberband's 0.01..100 bounds.
FORMANT_MIN_SEMITONES = -12.0
FORMANT_MAX_SEMITONES = 0.0


def _build_formant_audio_filter(semitones: float) -> str:
    """Independent formant (timbre) shift that preserves pitch and duration.

    Two-pass librubberband chain: the first pass shifts pitch by the target
    ratio (formants follow), the second restores the original pitch while
    *preserving* the now-shifted formant envelope. Net: pitch unchanged,
    spectral envelope (formants) scaled by 2**(semitones/12).

    Negative semitones lower the formants (deeper, 'larger' voice). Returns ''
    for ~0, when rubberband is unavailable, or for a positive shift the bundled
    engine can't deliver (see module note).
    """
    if abs(semitones) < 1e-3:
        return ""
    s = max(FORMANT_MIN_SEMITONES, min(FORMANT_MAX_SEMITONES, float(semitones)))
    if abs(s) < 1e-3:
        return ""
    if not _has_rubberband():
        return ""
    ratio = 2.0 ** (s / 12.0)
    inv = 1.0 / ratio
    return (
        f"rubberband=pitch={ratio:.6f}:formant=shifted,"
        f"rubberband=pitch={inv:.6f}:formant=preserved"
    )


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


StatusCB = Optional[Callable[[str], None]]


def _surface(status_cb: StatusCB, msg: str, level: str = "warning") -> None:
    """Send a message to the status callback (UI) AND the rotating log."""
    getattr(log(), level, log().warning)(msg)
    if status_cb:
        try:
            status_cb(msg)
        except Exception as cb_e:
            log().error(f"status_cb raised: {cb_e}")


def _safe_float(v, default: float, lo: float = -200.0, hi: float = 200.0) -> float:
    """Coerce v to a bounded float. Used to defang FFmpeg loudnorm measurements
    before they're interpolated into a filter string — prevents shell-style
    injection through stderr-parsed JSON values."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f or not (lo <= f <= hi):  # NaN or out-of-range
        return default
    return f


def loudnorm_analyze(input_path: str, trim_start: float = 0,
                     trim_duration: float = 0,
                     target_lufs: float = -14.0,
                     status_cb: StatusCB = None) -> dict | None:
    """First pass of EBU R128 loudness normalization.

    Returns a dict with measured values for the second pass, or None on
    failure. On failure, surfaces a human-readable message via `status_cb`
    and logs to the rotating log so the user knows normalization was skipped
    (instead of silently producing wrong loudness)."""
    tp = min(-1.0, target_lufs + 2)
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
    except subprocess.TimeoutExpired:
        _surface(status_cb, "Loudness analysis timed out (>2 min) — normalization skipped")
        return None
    except FileNotFoundError:
        _surface(status_cb, "ffmpeg.exe not found — loudness analysis skipped", "error")
        return None
    except OSError as e:
        _surface(status_cb, f"ffmpeg failed to start for loudness analysis: {e}", "error")
        return None

    if result.returncode != 0:
        _surface(status_cb,
                 f"Loudness analysis failed (ffmpeg exit {result.returncode}) — normalization skipped")
        return None

    # loudnorm JSON is in stderr after the standard log output
    import json as _json
    text = result.stderr or ""
    brace = text.rfind('{')
    if brace < 0:
        _surface(status_cb, "Loudness analysis: no JSON in ffmpeg output — normalization skipped")
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
        _surface(status_cb, "Loudness analysis: malformed JSON braces — normalization skipped")
        return None
    try:
        data = _json.loads(text[brace:end + 1])
    except _json.JSONDecodeError as e:
        _surface(status_cb, f"Loudness analysis: invalid JSON ({e}) — normalization skipped")
        return None

    for k in ('input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset'):
        if k not in data:
            _surface(status_cb,
                     f"Loudness analysis: missing key {k!r} — normalization skipped")
            return None
    return data


def loudnorm_filter(measured: dict, target_lufs: float = -14.0) -> str:
    """Build the second-pass loudnorm filter string from first-pass measurements.

    All measurements are coerced through `_safe_float` with bounded ranges.
    This blocks filter injection via crafted media that could cause ffmpeg
    to emit malicious-looking JSON through its stderr output."""
    tp = min(-1.0, target_lufs + 2)
    input_i = _safe_float(measured.get('input_i'), -16.0)
    input_tp = _safe_float(measured.get('input_tp'), -2.0)
    input_lra = _safe_float(measured.get('input_lra'), 7.0, lo=0.0, hi=50.0)
    input_thresh = _safe_float(measured.get('input_thresh'), -26.0)
    target_offset = _safe_float(measured.get('target_offset'), 0.0, lo=-99.0, hi=99.0)
    return (
        f"loudnorm=I={target_lufs:.1f}:TP={tp:.1f}:LRA=11"
        f":measured_I={input_i:.2f}"
        f":measured_TP={input_tp:.2f}"
        f":measured_LRA={input_lra:.2f}"
        f":measured_thresh={input_thresh:.2f}"
        f":offset={target_offset:.2f}"
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
    formant: float = 0.0,
    auto_preset: dict | None = None,
    source_w: int = 0,
    source_h: int = 0,
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
        # Compute the effective output dimensions when known so _encode_args
        # can detect NVENC's hardware minimum and fall back to software.
        # Fall back to the source dimensions when there's no crop so the
        # NVENC minimum is still enforced for stretch-only (and tiny-source)
        # outputs — without this a heavy downscale-stretch silently produces
        # a 0-byte NVENC file.
        base_w = crop_w if crop_w is not None else (source_w or None)
        base_h = crop_h if crop_h is not None else (source_h or None)
        eff_w, eff_h = _effective_output_dims(base_w, base_h, stretch_h, stretch_v)
        cmd += _encode_args(codec, crf, auto_preset, output_w=eff_w, output_h=eff_h)
    else:
        # No video modifications — stream copy (zero quality loss)
        cmd += ["-c:v", "copy"]

    # ── Audio ─────────────────────────────────────────────────
    # Build the audio filter chain in signal order: speed (vinyl pitch) →
    # formant (independent timbre shift) → loudnorm (final loudness). Any
    # non-empty chain forces a re-encode; otherwise honour mute/reencode/copy.
    if audio_mode == "mute":
        cmd += ["-an"]
    else:
        af_parts: list[str] = []
        if speed != 1.0:
            af_parts.append(_build_speed_audio_filter(speed))
        formant_filter = _build_formant_audio_filter(formant)
        if formant_filter:
            af_parts.append(formant_filter)
        if normalize_data:
            af_parts.append(loudnorm_filter(normalize_data, target_lufs))
        if af_parts:
            cmd += ["-af", ",".join(af_parts)]
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
    status_cb: StatusCB = None,
) -> bool:
    """Pre-render audio with per-sample speed automation — identical to preview.

    Reads raw PCM extracted by FFmpeg, runs the same resampling loop as the
    live PitchedAudioPlayer, writes a WAV file.

    Surfaces failures via status_cb + log so the user knows audio prerender
    failed (instead of silently producing a video with no audio track)."""
    import array, wave

    from src.core.speed_curve import SpeedCurve
    lane = SpeedCurve(keyframes, base_speed)

    BASE_RATE = 48000

    # Extract raw PCM from video
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        r = subprocess.run(
            [get_ffmpeg(), '-i', pcm_path, '-vn',
             '-f', 's16le', '-acodec', 'pcm_s16le',
             '-ac', '2', '-ar', str(BASE_RATE), '-'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=si,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        _surface(status_cb, "Audio extract timed out (>5 min) — export will have no audio")
        return False
    except (FileNotFoundError, OSError) as e:
        _surface(status_cb, f"ffmpeg failed for audio extract: {e} — export will have no audio", "error")
        return False
    if r.returncode != 0:
        stderr_tail = (r.stderr or b"")[-200:].decode("utf-8", errors="replace") if r.stderr else ""
        _surface(status_cb,
                 f"Audio extract failed (ffmpeg exit {r.returncode}): {stderr_tail.strip()} — export will have no audio",
                 "error")
        return False
    if not r.stdout:
        _surface(status_cb,
                 "Audio extract produced no PCM data — export will have no audio")
        return False
    pcm = array.array('h')
    # frombytes raises ValueError if len(stdout) is not a multiple of the
    # element size — happens with truncated/corrupted PCM streams. Drop the
    # tail bytes and surface a warning instead of crashing the export.
    raw = r.stdout
    elem = pcm.itemsize  # 2 for 'h'
    rem = len(raw) % elem
    if rem:
        _surface(status_cb,
                 f"Audio PCM has {rem} dangling byte(s); trimming — export will have audio")
        raw = raw[:-rem]
    pcm.frombytes(raw)
    # Each audio frame is 2 channels * 2 bytes/channel = 4 bytes per stereo frame.
    # In array('h') terms that's 2 array elements per frame.
    total_frames = len(pcm) // 2

    # Resample with automation — same algorithm as PitchedAudioPlayer._feed
    pos = float(trim_start_ms * BASE_RATE / 1000)
    end_pos = float(trim_end_ms * BASE_RATE / 1000)
    out = array.array('h')

    # Loop bound: idx must be <= total_frames-2 so pcm[b+2]/pcm[b+3] are
    # in range — the inner accesses use indices b, b+1, b+2, b+3 where
    # b = idx*2. With idx == total_frames-2, b+3 == 2*total_frames-1, the
    # last valid array index. Without this strict bound, an exact landing
    # at idx == total_frames-1 reads past the array.
    while pos < end_pos and int(pos) <= total_frames - 2:
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
    formant: float = 0.0,
    progress_callback=None,
    process_callback=None,
    auto_preset: dict | None = None,
    status_cb: StatusCB = None,
    source_w: int = 0,
    source_h: int = 0,
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
                                       trim_start_ms, trim_end_ms, status_cb=status_cb)
            if not audio_ok:
                # Surface to export_error.log via the same sanitized + size-
                # capped writer used by run_export, so the user can see why
                # audio is missing (and so admin/multi-user log inspection
                # doesn't reveal usernames).
                try:
                    from src.core.log_setup import sanitize_path
                    _write_export_error(
                        ["(audio pre-render)", sanitize_path(input_path)],
                        returncode=-1,
                        stderr_tail=[
                            "WARNING: Audio pre-render failed; export will "
                            "continue with video only (no audio).\n"
                        ],
                    )
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
        # Same hardware-min guard as build_command: when the effective output
        # dim is below NVENC's threshold, software encode instead of silently
        # producing 0-byte output. Fall back to source dims when uncropped so
        # stretch-only downscales are covered too.
        base_w = crop_w if crop_w is not None else (source_w or None)
        base_h = crop_h if crop_h is not None else (source_h or None)
        eff_w, eff_h = _effective_output_dims(base_w, base_h, stretch_h, stretch_v)
        cmd += _encode_args(codec, crf, auto_preset, output_w=eff_w, output_h=eff_h)
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
                wav_norm = loudnorm_analyze(audio_wav, target_lufs=target_lufs,
                                            status_cb=status_cb)
            cmd = [ffmpeg, "-y", "-i", video_out, "-i", audio_wav,
                   "-c:v", "copy"]
            # Audio chain on the pre-rendered WAV: formant shift → loudnorm.
            af_parts: list[str] = []
            formant_filter = _build_formant_audio_filter(formant)
            if formant_filter:
                af_parts.append(formant_filter)
            if wav_norm:
                af_parts.append(loudnorm_filter(wav_norm, target_lufs))
            if af_parts:
                cmd += ["-af", ",".join(af_parts)]
            cmd += ["-c:a", "aac", "-b:a", "320k",
                    "-map", "0:v:0", "-map", "1:a:0", output_path]
            # Cap the mux at 10 minutes — stream-copy + AAC re-encode of an
            # already-rendered file is fast (a few seconds typically), and a
            # hung ffmpeg here would otherwise block the export thread forever.
            try:
                proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, startupinfo=_hide_window(),
                    timeout=600, encoding="utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                _surface(status_cb, "Audio mux timed out (>10 min) — export aborted", "error")
                _write_export_error(cmd, -1, ["[mux] timed out after 10 min\n"])
                return False
            if proc.returncode != 0:
                # Without capturing stderr the user got "FFmpeg returned an
                # error" with no underlying message. Record the tail to the
                # sanitized export_error.log and surface a pointer to it.
                tail = (proc.stderr or "").splitlines()[-40:]
                _write_export_error(cmd, proc.returncode, [ln + "\n" for ln in tail])
                _surface(status_cb,
                         f"Audio mux failed (ffmpeg exit {proc.returncode}) — see export_error.log",
                         "error")
                return False

        if progress_callback:
            progress_callback(100)
        return True

    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


_INVALID_FILENAME_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _sanitize_suffix(suffix: str) -> str:
    """Strip path separators and other invalid filename chars from a suffix.

    This prevents an output_suffix like ``_edited/../../../evil`` from
    redirecting the export outside the intended directory."""
    if not suffix:
        return ""
    cleaned = _INVALID_FILENAME_CHARS_RE.sub("_", suffix)
    # Collapse repeated underscores from the substitution
    return re.sub(r"_+", "_", cleaned)[:64] if cleaned else ""


def get_output_path(input_path: str, suffix: str = "_edited") -> str:
    """Return ``<input_dir>/<stem><sanitized_suffix><ext>``.

    The suffix is sanitized so it cannot redirect the path outside the
    input directory."""
    p = Path(input_path)
    return str(p.with_stem(p.stem + _sanitize_suffix(suffix)))


def safe_output_path(output_dir: str, input_path: str, suffix: str = "_edited") -> str:
    """Build the final output path, guaranteeing it stays inside ``output_dir``.

    Resolves both paths to absolutes and verifies the candidate is contained
    in the resolved output_dir. Falls back to a safe name if traversal is
    detected. Used when the user has set a custom output directory; the
    suffix is sanitized either way."""
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    base = Path(input_path)
    safe_suffix = _sanitize_suffix(suffix)
    candidate_name = f"{base.stem}{safe_suffix}{base.suffix}"
    candidate = (out_dir / candidate_name).resolve()
    try:
        candidate.relative_to(out_dir)
    except ValueError:
        # Belt-and-suspenders: if sanitization missed something, force the
        # name into out_dir with a known-safe pattern.
        candidate = out_dir / f"{base.stem}_export{base.suffix}"
    return str(candidate)


def _hide_window():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si


def _probe_safe(args: list[str], timeout: float = 5.0) -> str | None:
    """Run an ffprobe command with timeout. Returns stdout text on success,
    None on any failure (timeout, non-zero exit, OSError). Never raises."""
    try:
        result = subprocess.run(
            args,
            capture_output=True, text=True,
            startupinfo=_hide_window(),
            timeout=timeout,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return None


def get_video_duration(input_path: str) -> float:
    out = _probe_safe(
        [get_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
    )
    if out is None:
        return 0.0
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def get_video_resolution(input_path: str) -> tuple[int, int]:
    out = _probe_safe(
        [get_ffprobe(), "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=p=0", input_path],
    )
    if out is None:
        return 0, 0
    try:
        parts = out.strip().split(",")
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
    out = _probe_safe(
        [get_ffprobe(), "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
    )
    if out is None:
        return 0.0
    raw = out.strip()
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


_probe_cache: dict[tuple[str, float], dict] = {}


def probe_video(input_path: str) -> dict | None:
    """Probe video metadata with ffprobe. Returns dict with:
    duration, width, height, fps, video_codec, audio_codec, bitrate, file_size.
    Results are cached by (path, mtime)."""
    import json as _json
    try:
        mtime = os.path.getmtime(input_path)
    except OSError:
        return None
    key = (input_path, mtime)
    if key in _probe_cache:
        return _probe_cache[key]
    try:
        result = subprocess.run(
            [get_ffprobe(), "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", input_path],
            capture_output=True, text=True, startupinfo=_hide_window(),
            timeout=3,
        )
        data = _json.loads(result.stdout)
    except (subprocess.TimeoutExpired, _json.JSONDecodeError, OSError):
        return None

    info = {"duration": 0.0, "width": 0, "height": 0, "fps": 0.0,
            "video_codec": "", "audio_codec": "", "bitrate": 0, "file_size": 0}

    fmt = data.get("format", {})
    try:
        info["duration"] = float(fmt.get("duration", 0))
    except (ValueError, TypeError):
        pass
    try:
        info["bitrate"] = int(fmt.get("bit_rate", 0))
    except (ValueError, TypeError):
        pass
    try:
        info["file_size"] = int(fmt.get("size", 0))
    except (ValueError, TypeError):
        pass

    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not info["video_codec"]:
            info["video_codec"] = s.get("codec_name", "")
            try:
                info["width"] = int(s.get("width", 0))
                info["height"] = int(s.get("height", 0))
            except (ValueError, TypeError):
                pass
            raw_fps = s.get("r_frame_rate", "0/1")
            if "/" in raw_fps:
                n, _, d = raw_fps.partition("/")
                try:
                    info["fps"] = float(n) / float(d) if float(d) else 0.0
                except ValueError:
                    pass
            else:
                try:
                    info["fps"] = float(raw_fps)
                except ValueError:
                    pass
        elif s.get("codec_type") == "audio" and not info["audio_codec"]:
            info["audio_codec"] = s.get("codec_name", "")

    _probe_cache[key] = info
    # Cap cache at 500 entries to prevent unbounded memory growth
    if len(_probe_cache) > 500:
        # Remove oldest entries (first inserted)
        excess = len(_probe_cache) - 500
        for k in list(_probe_cache)[:excess]:
            del _probe_cache[k]
    return info


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


def extract_thumbnail(input_path: str, timestamp_s: float,
                      width: int = 160) -> bytes | None:
    """Extract a downscaled thumbnail at the given timestamp.

    Optimized for seek-bar hover preview: keyframe-only seek (no decode-
    forward), scaled to `width` px keeping aspect, JPEG-encoded for size.
    Faster than extract_frame() — typical 30-80 ms vs 200-500 ms for HD.
    Trades frame accuracy for speed (snaps to nearest keyframe).

    Returns raw JPEG bytes for QImage.loadFromData(), or None.
    """
    cmd = [
        get_ffmpeg(), '-loglevel', 'quiet',
        '-ss', f'{timestamp_s:.3f}',
        '-i', input_path,
        '-frames:v', '1',
        '-vf', f'scale={width}:-2',
        '-q:v', '5',
        '-f', 'image2pipe', '-vcodec', 'mjpeg',
        'pipe:1',
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, startupinfo=_hide_window(), timeout=3,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


# Cap export_error.log size — without this, a user who batch-exports
# hundreds of clips with intermittent failures grows the log indefinitely.
# 1 MB main + 1 MB rotated backup = 2 MB worst case on disk.
_EXPORT_ERROR_LOG_MAX = 1_000_000
# Hard ceiling on a single export run. Even a 10-minute clip on slow
# hardware finishes within ~30x its duration; a process that's still
# running after this cap is hung and would block the export thread forever.
_EXPORT_RUNTIME_HARD_CAP_S = 60 * 60 * 4  # 4 hours absolute max


def _rotate_log_if_full(path) -> None:
    """If `path` exceeds _EXPORT_ERROR_LOG_MAX, rename it to `<path>.1` and
    let the next write start a fresh file. Single backup; older content is
    discarded. Best-effort — failures here must never raise during an
    error-logging path."""
    try:
        if path.exists() and path.stat().st_size >= _EXPORT_ERROR_LOG_MAX:
            backup = path.with_suffix(path.suffix + ".1")
            try:
                if backup.exists():
                    backup.unlink()
            except OSError:
                pass
            try:
                path.rename(backup)
            except OSError:
                pass
    except OSError:
        pass


def _write_export_error(cmd: list[str], returncode: int, stderr_tail: list[str]) -> None:
    """Append a single failure record to config/export_error.log.

    Sanitizes every argv element through `sanitize_path` so the log can be
    shared for debugging without leaking the user's username or directory
    layout. Rotates the file at _EXPORT_ERROR_LOG_MAX bytes.
    """
    try:
        import datetime
        from src.core.paths import get_config_dir
        from src.core.log_setup import sanitize_path

        log_path = get_config_dir() / "export_error.log"
        _rotate_log_if_full(log_path)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write("=" * 72 + "\n")
            f.write(f"TIMESTAMP: {datetime.datetime.now().isoformat()}\n")
            f.write(f"EXIT CODE: {returncode}\n")
            f.write("COMMAND:\n")
            for arg in cmd:
                clean = sanitize_path(arg)
                # Quote args containing spaces so the log can be
                # copy-pasted into a shell for manual reproduction.
                if " " in clean or "(" in clean or "[" in clean:
                    f.write(f'  "{clean}"\n')
                else:
                    f.write(f"  {clean}\n")
            f.write("STDERR TAIL:\n")
            f.write(sanitize_path("".join(stderr_tail)))
            f.write("\n")
    except Exception:
        pass


def run_export(cmd: list[str], duration: float, progress_callback=None, process_callback=None) -> bool:
    """Run ffmpeg and report progress via callback(percent: float).

    If process_callback is provided, it receives the Popen object for
    external cancellation. A wall-clock watchdog guarantees the call
    returns even if ffmpeg hangs (rare but observed on driver
    crashes / pipe stalls): after `max(60s, duration*30, hard cap)` of
    runtime the child process is terminated and run_export returns False.

    On non-zero exit, writes a sanitized + size-capped failure record to
    config/export_error.log so the user (and us) can see why ffmpeg
    failed — the UI's "FFmpeg returned an error" dialog alone is useless
    without the underlying message.
    """
    import threading

    process = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
        universal_newlines=True, encoding="utf-8", errors="replace",
        startupinfo=_hide_window(),
    )
    if process_callback:
        process_callback(process)

    # Watchdog: terminates the child if export runs longer than
    # max(60s, duration * 30, hard cap). 30x is generous (worst real-world
    # ratio observed: ~25x for 4K HEVC software encode on slow CPU); the
    # hard cap protects long jobs from a runaway driver.
    wd_seconds = max(60.0, duration * 30.0)
    wd_seconds = min(wd_seconds, _EXPORT_RUNTIME_HARD_CAP_S)
    watchdog_fired = {"value": False}

    def _kill_runaway():
        watchdog_fired["value"] = True
        try:
            if process.poll() is None:
                log().error(f"Export watchdog fired after {wd_seconds:.0f}s — killing ffmpeg")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        except OSError:
            pass

    watchdog = threading.Timer(wd_seconds, _kill_runaway)
    watchdog.daemon = True
    watchdog.start()

    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")

    # Bounded stderr tail — we only need the tail for failure diagnosis,
    # and ffmpeg stderr is chatty (one line per frame for verbose builds).
    stderr_tail: list[str] = []
    MAX_TAIL = 80

    try:
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
    finally:
        watchdog.cancel()

    if watchdog_fired["value"]:
        # Treat watchdog kill as a failure with a synthetic stderr line so
        # the user sees something meaningful in the error log.
        stderr_tail.append(f"[watchdog] export aborted after {wd_seconds:.0f}s\n")
        _write_export_error(cmd, process.returncode if process.returncode is not None else -1,
                            stderr_tail)
        return False

    if process.returncode != 0:
        _write_export_error(cmd, process.returncode, stderr_tail)
        return False

    return True
