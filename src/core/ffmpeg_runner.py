import subprocess
import re
from pathlib import Path

from src.core.paths import get_ffmpeg, get_ffprobe


def _build_speed_audio_filter(speed: float) -> str:
    """Change audio speed with natural pitch shift (no pitch correction).
    Faster = higher pitch, slower = deeper pitch (vinyl effect).
    Only asetrate — no trailing aresample, which would undo the pitch shift."""
    rate = 48000
    adjusted = max(1, int(rate * speed))
    return f"aresample={rate},asetrate={adjusted}"


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

    if vfilters:
        cmd += ["-vf", ",".join(vfilters)]
        if codec == "h265":
            # Try NVENC first, fall back to software
            cmd += ["-c:v", "hevc_nvenc", "-preset", "p1", "-cq", str(crf)]
        else:
            cmd += ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", str(crf)]
    else:
        # No video modifications — stream copy (zero quality loss)
        cmd += ["-c:v", "copy"]

    # ── Audio ─────────────────────────────────────────────────
    # Precedence: mute > speed change (forces re-encode with pitch shift) > reencode > copy
    if audio_mode == "mute":
        cmd += ["-an"]
    elif speed != 1.0:
        cmd += ["-af", _build_speed_audio_filter(speed)]
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
    import array, struct, wave

    from src.ui.automation_lane import AutomationLane
    lane = AutomationLane()
    lane.set_keyframes(keyframes)
    lane.set_base_speed(base_speed)
    lane._max_ms = trim_end_ms

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
    from src.ui.automation_lane import AutomationLane
    lane = AutomationLane()
    lane.set_keyframes(keyframes)
    lane.set_base_speed(base_speed)
    lane._max_ms = trim_end_ms

    segments = []
    t = trim_start_ms
    while t < trim_end_ms:
        t_end = min(t + step_ms, trim_end_ms)
        mid = (t + t_end) // 2
        speed = lane.get_speed_at(mid)
        speed = max(0.25, min(2.0, speed))
        segments.append((t / 1000, t_end / 1000, speed))
        t = t_end

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
    progress_callback=None,
    process_callback=None,
) -> bool:
    """Export with speed automation.

    Audio: pre-rendered with per-sample resampling (identical to preview).
    Video: segment-based export at keyframe boundaries.
    Final: mux pre-rendered audio with concatenated video.
    """
    import os, tempfile, shutil
    ffmpeg = get_ffmpeg()

    has_crop = all(v is not None for v in (crop_x, crop_y, crop_w, crop_h))
    encoder = "hevc_nvenc" if codec == "h265" else "h264_nvenc"

    temp_dir = tempfile.mkdtemp(prefix="ve_export_")
    segments = build_video_segments(keyframes, base_speed, trim_start_ms, trim_end_ms)
    total_out_dur = sum((e - s) / spd for s, e, spd in segments)

    try:
        # ── Step 1: Pre-render audio ────────────────────────
        audio_wav = os.path.join(temp_dir, "audio.wav")
        if audio_mode != "mute":
            if progress_callback:
                progress_callback(0)
            prerender_audio(input_path, audio_wav, keyframes, base_speed,
                            trim_start_ms, trim_end_ms)

        # ── Step 2: Export video segments (no audio) ────────
        temp_files = []
        exported_dur = 0.0

        for i, (seg_start, seg_end, speed) in enumerate(segments):
            seg_path = os.path.join(temp_dir, f"seg_{i:04d}.mp4")
            temp_files.append(seg_path)

            cmd = [ffmpeg, "-y", "-ss", f"{seg_start:.3f}",
                   "-t", f"{seg_end - seg_start:.3f}", "-i", input_path]

            vf = []
            if has_crop:
                vf.append(f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}")
            if stretch_h != 1.0 or stretch_v != 1.0:
                vf.append(f"scale=trunc(iw*{stretch_h:.4f}/2)*2:trunc(ih*{stretch_v:.4f}/2)*2")
            if speed != 1.0:
                vf.append(f"setpts=PTS/{speed:.4f}")
            if vf:
                cmd += ["-vf", ",".join(vf)]
            cmd += ["-c:v", encoder, "-preset", "p1", "-cq", str(crf)]
            cmd += ["-an"]  # no audio in segments
            cmd.append(seg_path)

            seg_dur = (seg_end - seg_start) / speed
            ok = run_export(cmd, seg_dur,
                progress_callback=lambda p, _ed=exported_dur, _sd=seg_dur: (
                    progress_callback((_ed + _sd * p / 100) / total_out_dur * 90 + 5)
                ) if progress_callback else None,
                process_callback=process_callback,
            )
            if not ok:
                return False
            exported_dur += seg_dur

        # ── Step 3: Concat video segments ───────────────────
        list_path = os.path.join(temp_dir, "concat.txt")
        with open(list_path, "w") as f:
            for tp in temp_files:
                f.write(f"file '{tp}'\n")

        video_concat = os.path.join(temp_dir, "video.mp4")
        cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0",
               "-i", list_path, "-c:v", "copy", "-an", video_concat]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, startupinfo=_hide_window())
        if proc.returncode != 0:
            return False

        # ── Step 4: Mux video + pre-rendered audio ──────────
        if audio_mode == "mute" or not os.path.exists(audio_wav):
            os.replace(video_concat, output_path)
        else:
            cmd = [ffmpeg, "-y", "-i", video_concat, "-i", audio_wav,
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "320k",
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


def run_export(cmd: list[str], duration: float, progress_callback=None, process_callback=None) -> bool:
    """Run ffmpeg and report progress via callback(percent: float).
    If process_callback is provided, it receives the Popen object for external cancellation."""
    process = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
        universal_newlines=True, encoding="utf-8", errors="replace",
        startupinfo=_hide_window(),
    )
    if process_callback:
        process_callback(process)

    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")

    for line in process.stderr:
        match = time_pattern.search(line)
        if match and duration > 0 and progress_callback:
            h, m, s = int(match.group(1)), int(match.group(2)), int(match.group(3))
            frac_str = match.group(4)
            frac = int(frac_str) / (10 ** len(frac_str))
            current = h * 3600 + m * 60 + s + frac
            pct = min(current / duration * 100, 100.0)
            progress_callback(pct)

    process.wait()
    return process.returncode == 0
