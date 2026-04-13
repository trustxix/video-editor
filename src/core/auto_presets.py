"""
Auto-optimized encoding presets from FFmpeg AutoResearch.

Reads presets.json written by the Auto Research optimizer at:
    %LOCALAPPDATA%/ffmpeg-autoresearch/presets.json

When available, these appear as additional quality options in Settings.
When unavailable (optimizer hasn't run yet), everything silently degrades
to the editor's built-in defaults — no errors, no UI changes.
"""

import json
import os

PRESETS_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "ffmpeg-autoresearch",
    "presets.json",
)


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
    """
    encoder = preset.get("encoder", "libx264")
    crf = preset.get("crf", 23)
    encoder_preset = preset.get("encoder_preset", "medium")

    if "nvenc" in encoder:
        return [
            "-c:v", encoder,
            "-preset", encoder_preset,
            "-rc", "vbr",
            "-b:v", "0",
            "-cq", str(crf),
        ]
    else:
        return [
            "-c:v", encoder,
            "-preset", encoder_preset,
            "-crf", str(crf),
        ]
