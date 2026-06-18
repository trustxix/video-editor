"""Tests for output container/format conversion on export.

Unit tests cover the codec-selection + remux/re-encode logic; the E2E tests
actually render the fixture video into each container and ffprobe the result
to prove the container + codecs are correct (not just that ffmpeg didn't error).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import ffmpeg_runner
from src.core.ffmpeg_runner import (
    build_command,
    get_output_path,
    safe_output_path,
    normalize_container,
    _target_video_codec,
    _can_copy_video,
    run_export,
)


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _probe_codec(path: Path, stream: str) -> str:
    """codec_name of the first video ('v:0') or audio ('a:0') stream.

    MPEG-TS makes ffprobe echo the stream's codec more than once, so take the
    first non-empty line rather than the whole output."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=20,
    )
    lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    return lines[0] if lines else ""


# ─── unit: helpers ─────────────────────────────────────────────────────────

def test_normalize_container():
    assert normalize_container(".MP4") == "mp4"
    assert normalize_container("matroska") == "mkv"
    assert normalize_container("QuickTime") == "mov"
    assert normalize_container("") == ""


def test_target_video_codec():
    assert _target_video_codec("webm", "h265") == "vp9"      # webm can't hold h265
    assert _target_video_codec("flv", "h265") == "h264"      # hevc-in-flv non-standard
    assert _target_video_codec("mp4", "h265") == "h265"
    assert _target_video_codec("mkv", "h264") == "h264"


def test_can_copy_video():
    assert _can_copy_video("mkv", "h264")
    assert _can_copy_video("mp4", "hevc")
    assert not _can_copy_video("webm", "h264")   # webm needs vp9/av1
    assert not _can_copy_video("flv", "hevc")
    assert not _can_copy_video("mp4", "")        # unknown → can't assume


def test_output_path_container_override():
    assert get_output_path("C:/x/clip.mp4", "_edited", "mkv").endswith("clip_edited.mkv")
    assert get_output_path("C:/x/clip.mp4", "_edited", "").endswith("clip_edited.mp4")
    assert get_output_path("C:/x/clip.webm", "_e", "mp4").endswith("clip_e.mp4")


def test_safe_output_path_container_override(tmp_path):
    p = safe_output_path(str(tmp_path), "C:/x/clip.mov", "_edited", "mp4")
    assert p.endswith("clip_edited.mp4")
    assert str(tmp_path) in p


# ─── unit: build_command codec selection ───────────────────────────────────

def test_build_command_webm_uses_vp9_and_opus():
    cmd = build_command("in.mp4", "out.webm", crop_x=0, crop_y=0, crop_w=320,
                        crop_h=240, container="webm")
    assert "libvpx-vp9" in cmd
    assert "libopus" in cmd
    assert "aac" not in cmd


def test_build_command_flv_forces_h264():
    cmd = build_command("in.mp4", "out.flv", crop_x=0, crop_y=0, crop_w=320,
                        crop_h=240, codec="h265", container="flv")
    assert "libx265" not in cmd and "hevc_nvenc" not in cmd


def test_build_command_remux_copies_compatible_source():
    # No edits, h264/aac source into mkv → lossless stream copy.
    cmd = build_command("in.mp4", "out.mkv", container="mkv",
                        source_vcodec="h264", source_acodec="aac")
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "copy"


def test_build_command_reencodes_incompatible_source():
    # No edits, vp9/opus source into mp4 → must re-encode (mp4 holds neither).
    cmd = build_command("in.webm", "out.mp4", container="mp4",
                        source_vcodec="vp9", source_acodec="opus",
                        source_w=320, source_h=240)
    vci = cmd.index("-c:v")
    assert cmd[vci + 1] != "copy"   # video re-encoded
    assert "aac" in cmd            # opus → aac for mp4


# ─── E2E: render + ffprobe ──────────────────────────────────────────────────

@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")
@pytest.mark.parametrize("ext", ["mp4", "mkv", "mov", "flv", "ts", "avi"])
def test_export_h264_family_containers(fixture_video, tmp_path, ext):
    """A crop forces re-encode; output must be h264+aac in the chosen container."""
    out = tmp_path / f"out.{ext}"
    cmd = build_command(
        str(fixture_video), str(out), trim_start=0.0, trim_end=1.0,
        crop_x=0, crop_y=0, crop_w=320, crop_h=240, codec="h264", crf=30,
        container=ext, source_vcodec="h264", source_acodec="aac",
    )
    assert run_export(cmd, 1.0), f"{ext} export failed"
    assert out.exists() and out.stat().st_size > 0
    assert _probe_codec(out, "v:0") == "h264"
    assert _probe_codec(out, "a:0") == "aac"


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")
def test_export_webm_is_vp9_opus(fixture_video, tmp_path):
    out = tmp_path / "out.webm"
    cmd = build_command(
        str(fixture_video), str(out), trim_start=0.0, trim_end=1.0,
        crop_x=0, crop_y=0, crop_w=320, crop_h=240, crf=34,
        container="webm", source_vcodec="h264", source_acodec="aac",
    )
    assert run_export(cmd, 1.0), "webm export failed"
    assert out.exists() and out.stat().st_size > 0
    assert _probe_codec(out, "v:0") == "vp9"
    assert _probe_codec(out, "a:0") == "opus"


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")
def test_export_lossless_remux_mp4_to_mkv(fixture_video, tmp_path):
    """No edits + compatible codec → lossless remux (copy), not a re-encode."""
    out = tmp_path / "out.mkv"
    cmd = build_command(str(fixture_video), str(out), container="mkv",
                        source_vcodec="h264", source_acodec="aac")
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert run_export(cmd, 5.0), "remux failed"
    assert _probe_codec(out, "v:0") == "h264"
    assert _probe_codec(out, "a:0") == "aac"


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")
def test_automation_export_to_mkv(fixture_video, tmp_path):
    """Speed-automation export into a non-source container: .mkv intermediate
    must mux into mkv as h264+aac."""
    from src.core.ffmpeg_runner import export_with_automation
    out = tmp_path / "auto.mkv"
    ok = export_with_automation(
        str(fixture_video), str(out),
        keyframes=[(0, 1.0), (500, 1.5)], base_speed=1.0,
        trim_start_ms=0, trim_end_ms=1000,
        codec="h264", crf=30, source_fps=30.0, container="mkv",
    )
    assert ok and out.exists() and out.stat().st_size > 0
    assert _probe_codec(out, "v:0") == "h264"
    assert _probe_codec(out, "a:0") == "aac"


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")
def test_automation_export_to_webm(fixture_video, tmp_path):
    """Speed-automation export to webm: vp9 intermediate muxed with opus."""
    from src.core.ffmpeg_runner import export_with_automation
    out = tmp_path / "auto.webm"
    ok = export_with_automation(
        str(fixture_video), str(out),
        keyframes=[(0, 1.0), (500, 1.5)], base_speed=1.0,
        trim_start_ms=0, trim_end_ms=1000,
        codec="h264", crf=34, source_fps=30.0, container="webm",
    )
    assert ok and out.exists() and out.stat().st_size > 0
    assert _probe_codec(out, "v:0") == "vp9"
    assert _probe_codec(out, "a:0") == "opus"
