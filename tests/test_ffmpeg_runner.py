"""Pytest tests for src/core/ffmpeg_runner.py.

Covers the build_command surface (parity with the legacy test_fixes.py) plus
the timeout/safety behavior added in the mass-distribution-readiness pass.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.ffmpeg_runner import (
    _build_speed_audio_filter,
    build_command,
    get_video_duration,
    get_video_fps,
    get_video_resolution,
    loudnorm_filter,
)


# ─── Subprocess timeout tests (Phase 1.2) ──────────────────────────────────

def test_get_video_duration_missing_file_returns_fast():
    start = time.monotonic()
    result = get_video_duration("C:/no-such-file-xyz123.mp4")
    elapsed = time.monotonic() - start
    assert result == 0.0
    assert elapsed < 6.0, f"Probe took {elapsed:.1f}s — should fail fast under 6s"


def test_get_video_resolution_missing_file_returns_fast():
    start = time.monotonic()
    result = get_video_resolution("C:/no-such-file-xyz123.mp4")
    elapsed = time.monotonic() - start
    assert result == (0, 0)
    assert elapsed < 6.0


def test_get_video_fps_missing_file_returns_fast():
    start = time.monotonic()
    result = get_video_fps("C:/no-such-file-xyz123.mp4")
    elapsed = time.monotonic() - start
    assert result == 0.0
    assert elapsed < 6.0


# ─── build_command parity (port from legacy test_fixes.py) ────────────────

def test_build_command_basic_has_y_flag():
    cmd = build_command("in.mp4", "out.mp4")
    assert "-y" in cmd


def test_build_command_basic_stream_copy():
    cmd = build_command("in.mp4", "out.mp4")
    assert cmd[cmd.index("-c:v") + 1] == "copy"


def test_build_command_crop_filter():
    cmd = build_command("in.mp4", "out.mp4", crop_x=100, crop_y=50, crop_w=800, crop_h=600)
    vf = cmd[cmd.index("-vf") + 1]
    assert "crop=800:600:100:50" in vf


def test_build_command_crop_forces_encode():
    cmd = build_command("in.mp4", "out.mp4", crop_x=100, crop_y=50, crop_w=800, crop_h=600)
    assert cmd[cmd.index("-c:v") + 1] != "copy"


def test_build_command_trim_uses_t_not_to():
    cmd = build_command("in.mp4", "out.mp4", trim_start=10.0, trim_end=15.0)
    assert "-t" in cmd
    assert "-to" not in cmd
    assert cmd[cmd.index("-t") + 1] == "5.000"


def test_build_command_trim_ss_before_input():
    cmd = build_command("in.mp4", "out.mp4", trim_start=10.0, trim_end=15.0)
    assert cmd.index("-ss") < cmd.index("-i")


def test_build_command_trim_start_zero_no_ss():
    cmd = build_command("in.mp4", "out.mp4", trim_start=0.0, trim_end=5.0)
    assert "-ss" not in cmd


def test_build_command_speed_setpts():
    cmd = build_command("in.mp4", "out.mp4", speed=2.0)
    vf = cmd[cmd.index("-vf") + 1]
    assert "setpts=PTS/2.0000" in vf


def test_build_command_speed_audio_asetrate():
    cmd = build_command("in.mp4", "out.mp4", speed=2.0)
    af = cmd[cmd.index("-af") + 1]
    assert "asetrate=" in af and "aresample=" in af


def test_build_command_mute_overrides_speed():
    cmd = build_command("in.mp4", "out.mp4", speed=2.0, audio_mode="mute")
    assert "-an" in cmd
    assert "-af" not in cmd


def test_build_command_stretch_scale():
    cmd = build_command("in.mp4", "out.mp4", stretch_h=0.5, stretch_v=1.0)
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=" in vf and "0.5000" in vf


def test_build_command_h265_codec():
    cmd = build_command("in.mp4", "out.mp4", codec="h265", crf=23,
                        crop_x=10, crop_y=10, crop_w=100, crop_h=100)
    assert "libx265" in cmd or "hevc_nvenc" in cmd


def test_build_speed_audio_filter_2x():
    f = _build_speed_audio_filter(2.0)
    assert "asetrate=96000" in f


def test_build_speed_audio_filter_half():
    f = _build_speed_audio_filter(0.5)
    assert "asetrate=24000" in f


def test_build_speed_audio_filter_near_zero():
    f = _build_speed_audio_filter(0.0001)
    # max(1, int(48000*0.0001)) = 4
    assert "asetrate=4" in f or "asetrate=1" in f


# ─── loudnorm_filter injection guard (Phase 1.9) ──────────────────────────

def test_loudnorm_filter_with_normal_measurements():
    """Sanity check: normal numeric measurements produce a valid filter string."""
    measured = {
        "input_i": "-20.0",
        "input_tp": "-2.0",
        "input_lra": "7.0",
        "input_thresh": "-26.0",
        "target_offset": "0.0",
    }
    f = loudnorm_filter(measured, target_lufs=-16.0)
    assert "loudnorm=" in f
    assert "measured_I=-20.00" in f


def test_loudnorm_filter_rejects_filter_injection_payload():
    """Adversarial JSON values from ffmpeg stderr must not flow into the filter."""
    malicious = {
        "input_i": "-20;amovie=/etc/passwd[a]",
        "input_tp": "-2",
        "input_lra": "7",
        "input_thresh": "-26",
        "target_offset": "0",
    }
    f = loudnorm_filter(malicious, target_lufs=-16.0)
    assert "amovie" not in f
    assert "passwd" not in f
    assert "/etc" not in f
    # Should still produce a syntactically valid filter using the default
    assert "loudnorm=" in f


def test_loudnorm_filter_rejects_nan_and_extreme_values():
    measured = {
        "input_i": "nan",
        "input_tp": "1e308",
        "input_lra": "-999",
        "input_thresh": "inf",
        "target_offset": "abc",
    }
    f = loudnorm_filter(measured, target_lufs=-16.0)
    # All defaults applied
    assert "measured_I=-16.00" in f
    assert "measured_TP=-2.00" in f


# ─── Loudnorm + prerender status callback (Phase 1.3) ─────────────────────

def test_loudnorm_analyze_calls_status_cb_on_missing_file():
    from src.core.ffmpeg_runner import loudnorm_analyze
    captured: list[str] = []
    result = loudnorm_analyze(
        "C:/no-such-file-xyz123.mp4",
        status_cb=captured.append,
    )
    assert result is None
    assert captured, "status_cb should have been called on failure"
    assert any("loud" in m.lower() or "skip" in m.lower() or "ffmpeg" in m.lower() for m in captured)


# ─── NVENC encoder status (Phase 1.4) ─────────────────────────────────────

def test_get_encoder_status_returns_tuple(monkeypatch):
    """Whether NVENC is detected or not, get_encoder_status returns (label, reason)."""
    from src.core import ffmpeg_runner
    encoder, reason = ffmpeg_runner.get_encoder_status()
    assert encoder in ("nvenc", "software")
    assert isinstance(reason, str)
    if encoder == "software":
        assert reason  # non-empty when fallback active


def test_encoder_status_reports_reason_on_ffmpeg_missing(monkeypatch):
    from src.core import ffmpeg_runner
    # Reset cache for this test
    ffmpeg_runner._nvenc_available.clear()
    ffmpeg_runner._nvenc_reason.clear()

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(ffmpeg_runner.subprocess, "run", fake_run)
    encoder, reason = ffmpeg_runner.get_encoder_status()
    assert encoder == "software"
    assert "ffmpeg" in reason.lower()
