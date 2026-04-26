"""End-to-end export tests: real ffmpeg pipeline, verified with ffprobe.

These tests ensure the export pipeline doesn't silently produce wrong
output — the highest-stakes regression to catch for a video editor.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _ffprobe(path: Path) -> dict:
    """Return ffprobe JSON for path."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"ffprobe failed: {result.stderr}"
    return json.loads(result.stdout)


def _video_stream(info: dict) -> dict | None:
    return next((s for s in info["streams"] if s["codec_type"] == "video"), None)


def test_export_basic_streamcopy(fixture_video, tmp_path):
    """No filters → stream copy → output exists, video stream present, ~5s duration."""
    from src.core.ffmpeg_runner import build_command, run_export
    out = tmp_path / "out.mp4"
    cmd = build_command(str(fixture_video), str(out))
    rc = run_export(cmd, duration=5.0)
    assert rc, "export reported failure"
    assert out.exists() and out.stat().st_size > 0
    info = _ffprobe(out)
    assert _video_stream(info) is not None
    duration = float(info["format"]["duration"])
    assert 4.5 < duration < 5.5, f"expected ~5s, got {duration}"


def test_export_with_trim(fixture_video, tmp_path):
    """Trim 1.0..3.0 → output ~2s."""
    from src.core.ffmpeg_runner import build_command, run_export
    out = tmp_path / "out_trim.mp4"
    cmd = build_command(str(fixture_video), str(out), trim_start=1.0, trim_end=3.0)
    rc = run_export(cmd, duration=5.0)
    assert rc
    info = _ffprobe(out)
    duration = float(info["format"]["duration"])
    assert 1.7 < duration < 2.3, f"expected ~2s, got {duration}"


def test_export_with_crop(fixture_video, tmp_path):
    """Crop 160x160 → output exactly 160x160.

    Use 160x160 not 100x100 because NVENC has a hardware minimum frame size
    (~145x49 for h264_nvenc). The smaller crop succeeds with libx264 but
    fails on NVIDIA boxes with 'Frame Dimension less than the minimum
    supported value'. See ffmpeg_runner._encode_args TODO."""
    from src.core.ffmpeg_runner import build_command, run_export
    out = tmp_path / "out_crop.mp4"
    cmd = build_command(str(fixture_video), str(out),
                        crop_x=10, crop_y=10, crop_w=160, crop_h=160)
    rc = run_export(cmd, duration=5.0)
    assert rc
    vstream = _video_stream(_ffprobe(out))
    assert vstream is not None
    assert vstream["width"] == 160
    assert vstream["height"] == 160


def test_export_with_speed_2x(fixture_video, tmp_path):
    """2x speed → output ~2.5s (half of 5s)."""
    from src.core.ffmpeg_runner import build_command, run_export
    out = tmp_path / "out_speed.mp4"
    cmd = build_command(str(fixture_video), str(out), speed=2.0,
                        audio_mode="reencode")
    rc = run_export(cmd, duration=5.0)
    assert rc
    info = _ffprobe(out)
    duration = float(info["format"]["duration"])
    assert 2.2 < duration < 2.8


def test_export_silent_video_does_not_break(fixture_video_silent, tmp_path):
    """Source has no audio track → export must succeed (no crash on missing audio)."""
    from src.core.ffmpeg_runner import build_command, run_export
    out = tmp_path / "out_silent.mp4"
    cmd = build_command(str(fixture_video_silent), str(out),
                        crop_x=10, crop_y=10, crop_w=160, crop_h=160)
    rc = run_export(cmd, duration=5.0)
    assert rc
    assert _video_stream(_ffprobe(out)) is not None


def test_export_h265_codec(fixture_video, tmp_path):
    """h265 codec → output is HEVC (libx265 or hevc_nvenc, depending on machine)."""
    from src.core.ffmpeg_runner import build_command, run_export
    out = tmp_path / "out_h265.mp4"
    cmd = build_command(str(fixture_video), str(out),
                        codec="h265", crop_x=0, crop_y=0, crop_w=320, crop_h=240)
    rc = run_export(cmd, duration=5.0)
    assert rc
    vstream = _video_stream(_ffprobe(out))
    # ffprobe reports codec_name as 'hevc' for both libx265 and hevc_nvenc
    assert vstream["codec_name"] == "hevc"
