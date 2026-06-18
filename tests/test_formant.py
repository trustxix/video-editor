"""Tests for the independent formant-shift audio feature.

Two layers:
  1. Pure unit tests on `_build_formant_audio_filter` / `build_command` wiring
     (deterministic; rubberband availability is forced via the cache).
  2. A spectral regression test that actually renders audio through the shipped
     filter chain and measures it — this is the *arbiter* of which slider range
     the feature exposes. It proves the downward (deepening) shift lowers the
     formant envelope by 2**(s/12) while leaving the pitch (F0) untouched.

Rationale: the bundled librubberband (R2 engine) only honours
formant=preserved when pitch is shifted UP, so independent formant *raising*
is unreachable; the builder clamps to <= 0 and this test locks that contract
in so a future ffmpeg/rubberband bump that changes the behaviour is caught.
"""
from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import ffmpeg_runner
from src.core.ffmpeg_runner import (
    FORMANT_MAX_SEMITONES,
    FORMANT_MIN_SEMITONES,
    _build_formant_audio_filter,
    build_command,
)


# ─── unit tests (no ffmpeg needed) ────────────────────────────────────────

@pytest.fixture
def _rubberband_present(monkeypatch):
    """Force the rubberband-availability cache to True so the pure builder
    tests don't depend on a real ffmpeg probe."""
    monkeypatch.setitem(ffmpeg_runner._rubberband_available, "rb", True)


def test_formant_zero_is_noop():
    assert _build_formant_audio_filter(0.0) == ""
    assert _build_formant_audio_filter(0.0004) == ""


def test_formant_positive_clamped_to_noop(_rubberband_present):
    # Upward formant shift is not deliverable by the bundled engine; the
    # builder clamps positive values to the 0 ceiling and emits nothing.
    assert FORMANT_MAX_SEMITONES == 0.0
    assert _build_formant_audio_filter(7.0) == ""
    assert _build_formant_audio_filter(0.5) == ""


def test_formant_negative_builds_two_pass_chain(_rubberband_present):
    chain = _build_formant_audio_filter(-7.0)
    assert chain.count("rubberband=") == 2
    assert "formant=shifted" in chain
    assert "formant=preserved" in chain
    # ratio = 2**(-7/12) ≈ 0.6674 ; inverse ≈ 1.4983
    assert "pitch=0.667" in chain
    assert "pitch=1.498" in chain


def test_formant_clamped_to_min(_rubberband_present):
    # -99 clamps to FORMANT_MIN_SEMITONES; the emitted ratio matches the clamp.
    chain = _build_formant_audio_filter(-99.0)
    ratio = 2.0 ** (FORMANT_MIN_SEMITONES / 12.0)
    assert f"pitch={ratio:.6f}" in chain


def test_formant_unavailable_returns_empty(monkeypatch):
    monkeypatch.setitem(ffmpeg_runner._rubberband_available, "rb", False)
    assert _build_formant_audio_filter(-7.0) == ""


def test_build_command_formant_forces_aac(_rubberband_present):
    cmd = build_command("in.mp4", "out.mp4", formant=-5.0)
    assert "-af" in cmd
    af = cmd[cmd.index("-af") + 1]
    assert "rubberband=" in af
    assert cmd[cmd.index("-c:a") + 1] == "aac"


def test_build_command_no_formant_keeps_copy(_rubberband_present):
    cmd = build_command("in.mp4", "out.mp4", formant=0.0)
    assert cmd[cmd.index("-c:a") + 1] == "copy"


def test_build_command_formant_chained_after_speed(_rubberband_present):
    cmd = build_command("in.mp4", "out.mp4", speed=1.5, formant=-5.0)
    af = cmd[cmd.index("-af") + 1]
    # speed (asetrate) must come before the formant rubberband passes
    assert af.index("asetrate") < af.index("rubberband")


# ─── spectral regression (renders real audio) ─────────────────────────────

def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _goertzel_mag(samples, sr: int, freq: float) -> float:
    """Single-frequency DFT magnitude (O(N)) — used to probe pitch/formant."""
    w = 2.0 * math.pi * freq / sr
    cw, sw = math.cos(w), math.sin(w)
    re = im = 0.0
    for n, s in enumerate(samples):
        re += s * math.cos(-w * n)
        im += s * math.sin(-w * n)
    return math.hypot(re, im)


def _peak_freq(samples, sr: int, lo: float, hi: float, step: float = 5.0) -> float:
    best_f, best_m = lo, -1.0
    f = lo
    while f <= hi:
        m = _goertzel_mag(samples, sr, f)
        if m > best_m:
            best_m, best_f = m, f
        f += step
    return best_f


def _render_and_load(filter_str: str, tmp_path: Path) -> tuple[list[int], int]:
    """Generate a 150 Hz buzz with a 1500 Hz formant resonance, run it through
    `filter_str`, and return (mono samples, sample_rate)."""
    src = tmp_path / "src.wav"
    out = tmp_path / "out.wav"
    gen = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", "aevalsrc='0.3*(2*(150*t - floor(150*t)) - 1)':s=48000:d=1.5:c=mono",
        "-af", "equalizer=f=1500:t=q:w=2:g=22",
        str(src),
    ]
    assert subprocess.run(gen, capture_output=True, timeout=30).returncode == 0
    af = ["-af", filter_str] if filter_str else []
    assert subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
        + af + [str(out)],
        capture_output=True, timeout=30,
    ).returncode == 0
    with wave.open(str(out), "rb") as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        raw = w.readframes(w.getnframes())
    samples = list(struct.unpack(f"<{len(raw) // 2}h", raw))
    if nch == 2:
        samples = samples[0::2]
    return samples, sr


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")
def test_formant_down_lowers_envelope_preserving_pitch(tmp_path):
    """The shipped -7 st chain must drop the formant peak toward
    1500*2**(-7/12) ≈ 1001 Hz while keeping the 150 Hz fundamental."""
    # Force availability so the builder emits the chain regardless of probe.
    ffmpeg_runner._rubberband_available["rb"] = True
    chain = _build_formant_audio_filter(-7.0)
    if not chain:
        pytest.skip("rubberband filter unavailable in this ffmpeg build")

    src_samples, sr = _render_and_load("", tmp_path)
    out_samples, sr2 = _render_and_load(chain, tmp_path)
    assert sr == sr2 == 48000

    # Use a mid window to avoid rubberband edge transients.
    def mid(xs, n=8192):
        s = max(0, len(xs) // 2 - n // 2)
        return xs[s:s + n]

    src_w, out_w = mid(src_samples), mid(out_samples)

    src_f0 = _peak_freq(src_w, sr, 80, 400)
    out_f0 = _peak_freq(out_w, sr, 80, 400)
    src_formant = _peak_freq(src_w, sr, 700, 2400)
    out_formant = _peak_freq(out_w, sr, 700, 2400)

    # Pitch preserved (within ~a semitone of the original fundamental).
    assert abs(out_f0 - src_f0) < 12, f"F0 drifted {src_f0}->{out_f0}"
    assert abs(src_formant - 1500) < 120, f"source formant off: {src_formant}"
    # Formant clearly lowered toward ~1001 Hz.
    assert out_formant < 1300, f"formant not lowered: {out_formant}"
    assert out_formant < src_formant - 200


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not on PATH")
def test_formant_filter_preserves_duration(tmp_path):
    ffmpeg_runner._rubberband_available["rb"] = True
    chain = _build_formant_audio_filter(-7.0)
    if not chain:
        pytest.skip("rubberband unavailable")
    src_samples, sr = _render_and_load("", tmp_path)
    out_samples, _ = _render_and_load(chain, tmp_path)
    # rubberband keeps tempo=1; allow a small (<5%) edge-latency difference.
    ratio = len(out_samples) / len(src_samples)
    assert 0.95 <= ratio <= 1.05, f"duration ratio {ratio:.3f}"
