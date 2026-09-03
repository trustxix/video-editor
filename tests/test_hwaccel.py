"""GPU decode wiring + the loudnorm true-peak fix.

Two separate things this file locks:

1. `-hwaccel cuda` is placed correctly and only where frames are actually
   decoded. Getting the position wrong is silent: ffmpeg treats `-hwaccel`
   after `-i` as an output option and ignores it, so the export still works,
   just entirely on the CPU.

2. loudnorm's TP stays inside the range ffmpeg accepts. It did not: the
   shipped default of -14 LUFS produced TP=-12, outside ffmpeg's [-9, 0], so
   BOTH loudnorm passes failed and "Normalize audio" silently never ran.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import ffmpeg_runner as fr   # noqa: E402


# ── loudnorm true peak ────────────────────────────────────────────────────

# ffmpeg's own documented bounds for the loudnorm filter's TP option.
FFMPEG_TP_MIN, FFMPEG_TP_MAX = -9.0, 0.0


@pytest.mark.parametrize("target_lufs", [-23.0, -16.0, -14.0, -11.0, -5.0])
def test_loudnorm_true_peak_is_always_valid(target_lufs):
    """Regression: TP was `min(-1.0, target_lufs + 2)`, which treats an
    absolute dBTP ceiling as an offset from the loudness target. At the shipped
    default of -14 LUFS that is -12 — ffmpeg rejects it with "Result too
    large", so normalization was dead at every realistic setting."""
    tp = fr.loudnorm_true_peak()
    assert FFMPEG_TP_MIN <= tp <= FFMPEG_TP_MAX, tp

    f = fr.loudnorm_filter({
        "input_i": -22.3, "input_tp": -2.0, "input_lra": 7.0,
        "input_thresh": -32.5, "target_offset": 0.5,
    }, target_lufs=target_lufs)
    tp_str = [p for p in f.split(":") if p.startswith("TP=")][0]
    assert FFMPEG_TP_MIN <= float(tp_str[3:]) <= FFMPEG_TP_MAX, f


def test_both_loudnorm_passes_use_the_same_true_peak():
    """The second pass consumes the first pass's measurements; if the two
    passes disagree on TP the normalization result is silently wrong."""
    src = Path(fr.__file__).read_text(encoding="utf-8")
    # Strip comments — the fix is documented in prose that quotes the old
    # expression, and matching that would make this test un-passable.
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    assert "min(-1.0, target_lufs + 2)" not in code, \
        "the old relative-TP expression is back"
    assert code.count("loudnorm_true_peak()") >= 2, \
        "both loudnorm passes must call the shared helper"


def test_loudnorm_analysis_does_not_decode_video():
    """This pass measures audio. Without -vn ffmpeg decodes the whole video
    stream and discards it — measured at 7.16 s vs 0.26 s on a 15 s window of
    2560x1440 HEVC 10-bit."""
    src = Path(fr.__file__).read_text(encoding="utf-8")
    start = src.index("def loudnorm_analyze")
    body = src[start:src.index("def loudnorm_filter")]
    assert "'-vn'" in body or '"-vn"' in body, \
        "loudnorm_analyze must pass -vn or it decodes the whole video"


# ── hwaccel placement ─────────────────────────────────────────────────────

def _idx(cmd, item):
    return cmd.index(item) if item in cmd else -1


def test_hwaccel_args_shape():
    """Either empty, or exactly the input-side flag pair."""
    args = fr.hwaccel_args()
    assert args in ([], ["-hwaccel", "cuda"]), args


def test_decoder_status_agrees_with_hwaccel_args():
    kind, reason = fr.get_decoder_status()
    assert kind in ("gpu", "cpu")
    if kind == "gpu":
        assert fr.hwaccel_args() == ["-hwaccel", "cuda"]
    else:
        assert fr.hwaccel_args() == []
        assert reason, "a CPU-decode fallback must carry a reason"


_GPU = bool(fr.cuvid_args("hevc", 2560, 1440, 100, 100, 1920, 1080))


@pytest.mark.skipif(not _GPU, reason="no GPU decode on this machine")
def test_crop_only_export_crops_inside_the_decoder():
    """The app's core operation — trim + crop to an aspect ratio. NVDEC does
    the crop while decoding, so nothing ever leaves VRAM. Measured 5.19 s /
    0.48 CPU cores vs 5.64 s / 8.59 cores for the CPU filter, pixel-identical
    (PSNR = inf)."""
    cmd = fr.build_command(
        "in.mp4", "out.mp4", trim_start=1.0, trim_end=5.0,
        crop_x=100, crop_y=100, crop_w=1920, crop_h=1080,
        source_w=2560, source_h=1440, container="mp4",
        source_vcodec="hevc", source_acodec="aac",
    )
    assert "-c:v" in cmd and cmd[_idx(cmd, "-c:v") + 1] == "hevc_cuvid", cmd
    # top x bottom x left x right
    assert cmd[_idx(cmd, "-crop") + 1] == "100x260x100x540", cmd
    assert _idx(cmd, "-crop") < _idx(cmd, "-i"), "decoder args must precede -i"
    # The CPU crop filter must be gone — doing it twice would crop twice.
    assert "-vf" not in cmd, cmd
    # And an encoder must still be selected — the branch that adds it keys off
    # the filter list, which the decoder crop empties, so this is exactly the
    # thing that breaks if that branch is written carelessly.
    last_cv = len(cmd) - 1 - cmd[::-1].index("-c:v")
    assert cmd[last_cv + 1] in ("h264_nvenc", "libx264"), cmd


@pytest.mark.skipif(not _GPU, reason="no GPU decode on this machine")
@pytest.mark.parametrize("extra", [
    {"stretch_h": 1.2},          # scale is a different scaler than NVDEC resize
    {"speed": 1.5},              # setpts
    {"brightness": 0.1},         # eq
    {"exposure": 0.5},
])
def test_anything_beyond_a_plain_crop_keeps_the_cpu_path(extra):
    """A CPU filter after a GPU decode forces the frames back out of VRAM,
    which measured SLOWER than not using the GPU at all (8.61 s vs 5.64 s).
    And NVDEC's -resize is a different scaler, so using it for stretch would
    silently change exported pixels."""
    cmd = fr.build_command(
        "in.mp4", "out.mp4", trim_start=1.0, trim_end=5.0,
        crop_x=100, crop_y=100, crop_w=1920, crop_h=1080,
        source_w=2560, source_h=1440, container="mp4",
        source_vcodec="hevc", source_acodec="aac", **extra,
    )
    assert "-crop" not in cmd, cmd
    assert "hevc_cuvid" not in cmd, cmd
    assert "-vf" in cmd and "crop=1920:1080:100:100" in cmd[cmd.index("-vf") + 1]


def test_no_gpu_decoder_for_a_lossless_remux():
    """A stream copy decodes nothing at all."""
    cmd = fr.build_command(
        "in.mp4", "out.mp4", trim_start=1.0, trim_end=5.0,
        source_w=1920, source_h=1080, container="mp4",
        source_vcodec="h264", source_acodec="aac",
    )
    assert cmd[cmd.index("-c:v") + 1] == "copy", cmd
    assert "-crop" not in cmd and "-hwaccel" not in cmd


def test_cuvid_declines_an_out_of_bounds_or_unknown_source():
    # crop bigger than the frame — must fall back rather than emit a bad -crop
    assert fr.cuvid_args("hevc", 1920, 1080, 100, 100, 1920, 1080) == []
    # full-frame crop is a no-op
    assert fr.cuvid_args("hevc", 1920, 1080, 0, 0, 1920, 1080) == []
    # codec NVDEC has no decoder for
    assert fr.cuvid_args("prores", 2560, 1440, 100, 100, 1920, 1080) == []
    # unknown source dimensions
    assert fr.cuvid_args("hevc", 0, 0, 100, 100, 1920, 1080) == []


def test_automation_export_stays_on_the_cpu():
    """Its filter_complex is all CPU filters, so a GPU decode would round-trip
    every frame through system memory and end up slower."""
    src = Path(fr.__file__).read_text(encoding="utf-8")
    start = src.index("def export_with_automation")
    rest = src[start + 1:]
    end = rest.index("\ndef ") if "\ndef " in rest else len(rest)
    body = rest[:end]
    assert "hwaccel_args()" not in body, \
        "the automation path must not use the VRAM round-trip"
    assert "cuvid_args(" not in body, \
        "the automation crop lives inside a re-timing filter graph"


@pytest.mark.skipif(not fr.hwaccel_args(), reason="no GPU decode on this machine")
def test_frame_and_thumbnail_extraction_use_gpu_decode():
    """Frame-step, colour preview and the seek-bar hover thumbnails all decode
    a GOP of 1440p HEVC per request. Interleaved measurement puts the latency
    win at only ~1.04x (696 -> 672 ms) — it is kept for the CPU saving, which
    is what keeps the UI responsive while the user scrubs the seek bar."""
    src = Path(fr.__file__).read_text(encoding="utf-8")
    for fn, nxt in (("def extract_frame", "def extract_thumbnail"),
                    ("def extract_thumbnail", None)):
        start = src.index(fn)
        body = src[start:src.index(nxt)] if nxt else src[start:start + 2000]
        assert "hwaccel_args()" in body, f"{fn} does not use GPU decode"


def test_probe_is_cached_not_rerun_per_command():
    """The probe spawns ffmpeg; doing that per exported clip would add ~150 ms
    to every export and hammer the GPU driver during a batch."""
    fr._has_cuda_decode()
    assert fr._cuda_decode is not None, "probe result was not cached"
    before = fr._cuda_decode
    for _ in range(50):
        fr.hwaccel_args()
    assert fr._cuda_decode is before
