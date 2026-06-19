"""Tests for PitchedAudioPlayer._feed — the 1.0x fast-path must produce the
exact same samples the per-sample interpolation loop would, and the resync
helper must map a source-ms to the right read cursor.
"""
from __future__ import annotations

import array
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeSink:
    def __init__(self, free):
        self._free = free

    def bytesFree(self):
        return self._free


def _run_feed(pcm_list, pos, speed, free=100000):
    from src.ui.video_player import PitchedAudioPlayer
    pa = PitchedAudioPlayer()
    pa._pcm = array.array('h', pcm_list)
    pa._pos = float(pos)
    pa._speed = speed
    pa._automation = None
    cap = []
    pa._sink = _FakeSink(free)
    pa._io = type("IO", (), {"write": lambda self, b: cap.append(b)})()
    pa._feed()
    out = array.array('h')
    if cap:
        out.frombytes(cap[0])
    return out, pa._pos


def test_feed_1x_copies_verbatim_then_silence(qapp):
    pcm = list(range(200))  # 100 interleaved-stereo frames
    out, newpos = _run_feed(pcm, 0, 1.0)
    assert list(out[:200]) == pcm           # frames copied verbatim
    assert all(v == 0 for v in out[200:])   # tail padded with silence
    assert newpos == 100.0                  # advanced exactly 100 frames


def test_feed_1x_from_offset(qapp):
    pcm = list(range(200))
    out, newpos = _run_feed(pcm, 10, 1.0)   # start at frame 10 (elem 20)
    assert list(out[:180]) == pcm[20:200]
    assert newpos == 100.0                  # advanced the remaining 90 frames


def test_feed_1x_fastpath_equals_slowpath(qapp):
    """The fast path must be sample-identical to the interpolation loop at 1.0x.

    Use a buffer larger than one feed tick (960 frames) so neither path hits
    the end-of-stream boundary (where they legitimately differ by one frame:
    the slow path drops the final frame because interpolation needs idx+1)."""
    pcm = [((i * 37) % 65536) - 32768 for i in range(4000)]  # 2000 frames > tick
    fast, _ = _run_feed(pcm, 0, 1.0)
    n = len(fast) // 2  # one tick = 960 frames, well inside the buffer
    # Slow path at 1.0x on integer pos: out[i] = pcm[2i] (frac == 0).
    assert list(fast) == pcm[:2 * n]


def test_feed_2x_still_runs_slowpath(qapp):
    pcm = list(range(2000))  # 1000 frames
    out, newpos = _run_feed(pcm, 0, 2.0)
    assert len(out) > 0
    assert newpos > 0  # consumed input faster than 1x


def test_resync_to_maps_source_ms_to_cursor(qapp):
    from src.ui.video_player import PitchedAudioPlayer
    pa = PitchedAudioPlayer()
    pa.resync_to(1000)  # 1.0s at 48000 Hz -> frame 48000
    assert abs(pa._pos - 48000.0) < 1e-6
    pa.resync_to(0)
    assert pa._pos == 0.0
