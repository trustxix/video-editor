"""
Auto-optimized encoding presets from FFmpeg AutoResearch.

Reads presets.json written by the Auto Research optimizer at:
    %LOCALAPPDATA%/ffmpeg-autoresearch/presets.json

When available, these appear as additional quality options in Settings.
When unavailable (optimizer hasn't run yet), everything silently degrades
to the editor's built-in defaults — no errors, no UI changes.

Security: the presets file lives in a user-writable location, so we treat
it as untrusted input. All values that flow into ffmpeg argv are validated
against allowlists (encoder name, encoder preset, integer crf in range).
"""

import json
import os

PRESETS_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "ffmpeg-autoresearch",
    "presets.json",
)


# Encoder names that are allowed to flow directly into the ffmpeg argv.
# Anything else falls back to libx264.
ALLOWED_ENCODERS = frozenset({
    "libx264", "libx265",
    "h264_nvenc", "hevc_nvenc",
    "h264_amf", "hevc_amf",            # AMD VCN
    "h264_qsv", "hevc_qsv",            # Intel QuickSync
    "h264_videotoolbox", "hevc_videotoolbox",  # mac, future
    "libsvtav1", "libaom-av1",         # AV1
    "libvpx-vp9",
})

# Encoder presets — covers libx264/libx265's named presets, NVENC's pN, AMF's
# 'quality'/'balanced'/'speed', QuickSync's 'veryslow'..'veryfast'.
ALLOWED_ENCODER_PRESETS = frozenset({
    # x264/x265
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow", "placebo",
    # NVENC
    "p1", "p2", "p3", "p4", "p5", "p6", "p7",
    "default", "hq", "ll", "llhq", "llhp", "lossless", "losslesshp",
    # AMF / QSV common
    "quality", "balanced", "speed",
})


def load_auto_presets() -> dict | None:
    """Load auto-optimized presets. Returns None if file missing or invalid."""
    try:
        with open(PRESETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != 1 or "presets" not in data:
            return None
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def get_quality_presets() -> dict[str, dict]:
    """
    Return auto-presets formatted for the settings dialog.

    Returns dict mapping display name → preset info:
        {
            "Auto: Best Efficiency (CRF 24 h265)": {
                "codec": "h265",
                "encoder": "libx265",
                "crf": 24,
                "encoder_preset": "slow",
                "metrics": { ... },
            },
            ...
        }

    Returns empty dict if no auto-presets available.
    """
    data = load_auto_presets()
    if not data:
        return {}

    result = {}
    for _key, preset in data["presets"].items():
        label = preset.get("label", _key)
        crf = preset.get("crf", "?")
        codec = preset.get("codec", "h264")
        display = f"{label} (CRF {crf} {codec})"
        result[display] = preset

    return result


def get_encode_args(preset: dict) -> list[str]:
    """
    Build FFmpeg encoder arguments from an auto-preset.

    Bypasses the editor's default _encode_args() to use exactly
    what the optimizer found to be optimal.

    All preset values are validated against allowlists before flowing into
    the argv — the presets file is in a user-writable location and treated
    as untrusted.
    """
    encoder = preset.get("encoder", "libx264")
    if encoder not in ALLOWED_ENCODERS:
        encoder = "libx264"

    encoder_preset = preset.get("encoder_preset", "medium")
    if encoder_preset not in ALLOWED_ENCODER_PRESETS:
        encoder_preset = "medium" if "nvenc" not in encoder else "p5"

    # crf clamped to ffmpeg's accepted range. Different codecs have different
    # ranges (x264: 0-51, x265: 0-51, AV1: 0-63) so use the union [0..63].
    raw_crf = preset.get("crf", 23)
    try:
        crf = int(raw_crf)
    except (TypeError, ValueError):
        crf = 23
    crf = max(0, min(63, crf))

    if "nvenc" in encoder:
        return [
            "-c:v", encoder,
            "-preset", encoder_preset,
            "-rc", "vbr",
            "-b:v", "0",
            "-cq", str(crf),
            "-maxrate", "200M",
        ]
    else:
        return [
            "-c:v", encoder,
            "-preset", encoder_preset,
            "-crf", str(crf),
        ]
