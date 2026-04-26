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
    _encode_args,
    _rotate_log_if_full,
    _sanitize_suffix,
    _write_export_error,
    build_command,
    get_output_path,
    get_video_duration,
    get_video_fps,
    get_video_resolution,
    loudnorm_filter,
    safe_output_path,
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


# ─── NVENC hardware-minimum frame-size fallback ───────────────────────────
# NVENC silently produces 0-byte output when frame dimensions are below the
# hardware minimum. Per NVIDIA Video Codec SDK (Turing+), h264_nvenc requires
# width ≥ 145 px; hevc_nvenc requires roughly double (≥ 256 wide × 144 tall)
# to cover NVENC's HEVC profile minimum.
# Source: NVIDIA Developer Forums "Minimum Width in Turing GPUs?" + the
# project's empirical 100×100 / 145×49 testing during the 2026-04-26
# mass-distribution-readiness pass.

def test_encode_args_falls_back_to_software_below_h264_nvenc_min(monkeypatch):
    """Output below h264_nvenc 145×49 minimum must use libx264 even when NVENC
    is available — otherwise the encoder silently produces 0-byte output."""
    from src.core import ffmpeg_runner
    monkeypatch.setitem(ffmpeg_runner._nvenc_available, "h264_nvenc", True)
    args = _encode_args("h264", crf=17, output_w=100, output_h=100)
    assert "h264_nvenc" not in args, f"Expected software fallback for 100×100, got {args}"
    assert "libx264" in args


def test_encode_args_falls_back_to_software_below_hevc_nvenc_min(monkeypatch):
    """hevc_nvenc has a higher minimum than h264_nvenc (≥256×144). A 200×100
    output must use libx265 when h265 is requested."""
    from src.core import ffmpeg_runner
    monkeypatch.setitem(ffmpeg_runner._nvenc_available, "hevc_nvenc", True)
    args = _encode_args("h265", crf=20, output_w=200, output_h=100)
    assert "hevc_nvenc" not in args, f"Expected software fallback for 200×100 hevc, got {args}"
    assert "libx265" in args


def test_encode_args_uses_nvenc_when_above_min_dim(monkeypatch):
    """Sanity / regression guard: 1920×1080 with NVENC available must still use
    h264_nvenc — the dim-aware fallback must not over-trigger."""
    from src.core import ffmpeg_runner
    monkeypatch.setitem(ffmpeg_runner._nvenc_available, "h264_nvenc", True)
    args = _encode_args("h264", crf=17, output_w=1920, output_h=1080)
    assert "h264_nvenc" in args


def test_encode_args_no_dim_hint_preserves_existing_behavior(monkeypatch):
    """When the caller does not pass output dims, _encode_args falls through to
    its original probe-NVENC-or-software logic — backward compatibility."""
    from src.core import ffmpeg_runner
    monkeypatch.setitem(ffmpeg_runner._nvenc_available, "h264_nvenc", True)
    # No output_w/output_h passed
    args = _encode_args("h264", crf=17)
    assert "h264_nvenc" in args


def test_encode_args_auto_preset_with_small_dim_falls_back_to_software(monkeypatch):
    """Auto-preset path also respects the hardware minimum: a small output with
    an NVENC auto-preset still falls back to software."""
    from src.core import ffmpeg_runner
    monkeypatch.setitem(ffmpeg_runner._nvenc_available, "h264_nvenc", True)
    auto_preset = {
        "codec": "h264",
        "encoder": "h264_nvenc",
        "encoder_preset": "p5",
        "crf": 23,
    }
    args = _encode_args("h264", crf=17, auto_preset=auto_preset, output_w=100, output_h=100)
    assert "h264_nvenc" not in args, f"Expected fallback for auto-preset NVENC + small dim, got {args}"
    assert "libx264" in args


def test_encode_args_auto_preset_software_unaffected_by_dim_check(monkeypatch):
    """A software auto-preset (libx264) with a small output should pass through
    unchanged — the dim check only redirects NVENC encoders."""
    from src.core import ffmpeg_runner
    monkeypatch.setitem(ffmpeg_runner._nvenc_available, "h264_nvenc", True)
    auto_preset = {
        "codec": "h264",
        "encoder": "libx264",
        "encoder_preset": "slow",
        "crf": 23,
    }
    args = _encode_args("h264", crf=17, auto_preset=auto_preset, output_w=100, output_h=100)
    assert "libx264" in args
    # The encoder_preset from the auto-preset should be preserved
    assert "slow" in args


def test_build_command_small_crop_uses_software_encoder(monkeypatch):
    """End-to-end: build_command with a 100×100 crop must produce a libx264
    argv even when NVENC is available — covers the user-visible bug path."""
    from src.core import ffmpeg_runner
    monkeypatch.setitem(ffmpeg_runner._nvenc_available, "h264_nvenc", True)
    cmd = build_command(
        "in.mp4", "out.mp4",
        crop_x=10, crop_y=10, crop_w=100, crop_h=100,
        codec="h264", crf=17,
    )
    assert "libx264" in cmd, f"Expected libx264 for 100×100 crop, got: {cmd}"
    assert "h264_nvenc" not in cmd


def test_build_command_full_hd_crop_uses_nvenc(monkeypatch):
    """Regression guard: a normal 1280×720 crop with NVENC available must still use NVENC."""
    from src.core import ffmpeg_runner
    monkeypatch.setitem(ffmpeg_runner._nvenc_available, "h264_nvenc", True)
    cmd = build_command(
        "in.mp4", "out.mp4",
        crop_x=0, crop_y=0, crop_w=1280, crop_h=720,
        codec="h264", crf=17,
    )
    assert "h264_nvenc" in cmd


def test_build_command_small_crop_with_stretch_uses_software(monkeypatch):
    """A 200×200 crop stretched to 0.5×0.5 → 100×100 effective → must use software."""
    from src.core import ffmpeg_runner
    monkeypatch.setitem(ffmpeg_runner._nvenc_available, "h264_nvenc", True)
    cmd = build_command(
        "in.mp4", "out.mp4",
        crop_x=0, crop_y=0, crop_w=200, crop_h=200,
        stretch_h=0.5, stretch_v=0.5,
        codec="h264", crf=17,
    )
    assert "libx264" in cmd, f"Expected libx264 for 100×100 effective output, got: {cmd}"
    assert "h264_nvenc" not in cmd


# ─── export_error.log sanitization + rotation ─────────────────────────────

def test_write_export_error_sanitizes_paths(tmp_path, monkeypatch):
    """export_error.log entries must redact the user's home and username so
    a shared log doesn't leak filesystem layout."""
    from src.core import paths as paths_mod
    monkeypatch.setattr(paths_mod, "get_config_dir", lambda: tmp_path)

    fake_username = "fakeuser"
    monkeypatch.setenv("USERNAME", fake_username)

    cmd = [
        "ffmpeg", "-i", f"C:\\Users\\{fake_username}\\videos\\private clip.mkv",
        "-c:v", "libx264", "out.mp4",
    ]
    _write_export_error(cmd, returncode=1, stderr_tail=[
        f"failed reading C:\\Users\\{fake_username}\\videos\\private clip.mkv\n",
    ])

    log_path = tmp_path / "export_error.log"
    assert log_path.exists(), "log was not written — check monkeypatch target"
    log_text = log_path.read_text(encoding="utf-8")
    assert fake_username not in log_text, f"username leaked: {log_text!r}"


def test_rotate_log_if_full_renames_when_over_cap(tmp_path):
    """When the log exceeds the cap, the next write rotates to .1 backup."""
    from src.core.ffmpeg_runner import _EXPORT_ERROR_LOG_MAX
    log = tmp_path / "export_error.log"
    log.write_bytes(b"X" * (_EXPORT_ERROR_LOG_MAX + 100))
    _rotate_log_if_full(log)
    assert not log.exists()
    assert (tmp_path / "export_error.log.1").exists()


def test_rotate_log_if_full_no_op_when_small(tmp_path):
    log = tmp_path / "export_error.log"
    log.write_text("small content")
    _rotate_log_if_full(log)
    assert log.exists()
    assert not (tmp_path / "export_error.log.1").exists()


# ─── Output path traversal guard (Phase 1.6) ──────────────────────────────

def test_sanitize_suffix_strips_path_separators():
    assert "/" not in _sanitize_suffix("evil/../../traversal")
    assert "\\" not in _sanitize_suffix(r"evil\..\..\traversal")


def test_sanitize_suffix_preserves_normal_text():
    assert _sanitize_suffix("_edited") == "_edited"
    assert _sanitize_suffix("_v2") == "_v2"


def test_sanitize_suffix_strips_invalid_filename_chars():
    out = _sanitize_suffix('a:b*c?d"e<f>g|h')
    for bad in ':*?"<>|':
        assert bad not in out


def test_get_output_path_blocks_traversal_via_suffix():
    """A traversal-attempting suffix must NOT escape the input dir."""
    out = get_output_path(r"C:\Users\alice\clip.mp4", suffix=r"/../../Windows/System32/evil")
    out_path = Path(out)
    # Output must still be in C:\Users\alice
    assert out_path.parent == Path(r"C:\Users\alice")


def test_safe_output_path_keeps_output_inside_output_dir(tmp_path):
    result = safe_output_path(str(tmp_path), r"C:\source\clip.mp4", suffix="_edited")
    assert str(tmp_path) in result
    assert result.endswith(".mp4")


def test_safe_output_path_blocks_suffix_traversal(tmp_path):
    """Hand-edited output_suffix with .. cannot redirect output."""
    result = safe_output_path(str(tmp_path), r"C:\source\clip.mp4", suffix=r"/../../Windows/System32/evil")
    rp = Path(result).resolve()
    # Result must be a descendant of tmp_path
    rp.relative_to(tmp_path.resolve())  # raises ValueError if not contained


def test_safe_output_path_creates_output_dir(tmp_path):
    new_dir = tmp_path / "new" / "nested" / "dir"
    result = safe_output_path(str(new_dir), r"C:\source\clip.mp4", suffix="_v")
    assert new_dir.exists()
    assert str(new_dir.resolve()) in str(Path(result).resolve())
