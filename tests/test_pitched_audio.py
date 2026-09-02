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
    """Stands in for QAudioSink. Never opens an audio device."""

    def __init__(self, free, size=None):
        self._free = free
        self._size = size if size is not None else free

    def bufferSize(self):
        return self._size

    def bytesFree(self):
        return self._free

    def stop(self):
        pass


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

    `free` is set so the write is smaller than the PCM buffer — neither path
    then hits the end-of-stream boundary, where they legitimately differ by one
    frame (the slow path drops the final frame because interpolation needs
    idx+1)."""
    pcm = [((i * 37) % 65536) - 32768 for i in range(4000)]  # 2000 frames
    fast, _ = _run_feed(pcm, 0, 1.0, free=960 * 4)           # 960 frames
    n = len(fast) // 2
    assert n == 960, n
    # Slow path at 1.0x on integer pos: out[i] = pcm[2i] (frac == 0).
    assert list(fast) == pcm[:2 * n]


def test_feed_fills_the_whole_sink_not_a_fixed_cap(qapp):
    """Regression: _feed used to cap each write at 960 frames — 20 ms of audio
    per 15 ms tick, so a drained buffer refilled at +5 ms/tick and took ~750 ms
    to recover. It must write everything the sink reports as free."""
    free_frames = 48000 // 2                       # a full 500 ms buffer
    # More source than the sink can take, kept inside the int16 range.
    pcm = [(i % 30000) - 15000 for i in range(2 * (free_frames + 1000))]
    out, newpos = _run_feed(pcm, 0, 1.0, free=free_frames * 4)
    assert len(out) // 2 == free_frames
    assert newpos == float(free_frames)


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


# ── A/V lock: the write cursor is not the playback position ──────────────
#
# A Windows QAudioSink hands out a 250 ms buffer. _feed writes ahead into it,
# so the read cursor (_pos) legitimately leads the audio the user is hearing
# by the queued amount — and in SOURCE time that lead is (queued ms x speed).
# Treating the lead as A/V drift is what made playback stutter above ~1.35x.

_BUF_FRAMES = 48000 // 4          # 250 ms at 48 kHz
_BUF_BYTES = _BUF_FRAMES * 4


def _primed_player(speed, queued_frames, pcm_seconds=20):
    """A PitchedAudioPlayer that has written `queued_frames` still sitting in
    a full-size sink buffer, at a constant read rate."""
    from src.ui.video_player import PitchedAudioPlayer
    pa = PitchedAudioPlayer()
    pa._pcm = array.array('h', bytes(4 * 48000 * pcm_seconds))
    pa._speed = speed
    pa._automation = None
    free = (_BUF_FRAMES - queued_frames) * 4
    pa._sink = _FakeSink(free, _BUF_BYTES)
    pa._io = type("IO", (), {"write": lambda self, b: None})()
    return pa


def test_queued_frames_reads_the_sink_backlog(qapp):
    pa = _primed_player(1.0, 5000)
    pa._written = 10000
    assert pa.queued_frames() == 5000
    # Never reports more than has actually been written.
    pa._written = 100
    assert pa.queued_frames() == 100


def test_playback_position_subtracts_the_queued_lead(qapp):
    """At 1.35x a full 250 ms buffer puts the write cursor 337 ms of source
    time ahead. playback_position_ms must report the heard position, not that."""
    speed = 1.35
    pa = _primed_player(speed, _BUF_FRAMES)
    # Simulate 60 ticks of 800 output frames each, written at a constant rate.
    pos = 0.0
    for _ in range(60):
        pa._marks.append((pa._written, pos))
        pa._written += 800
        pos += 800 * speed
    pa._pos = pos

    write_ms = pa._pos * 1000.0 / 48000
    heard_ms = pa.playback_position_ms()
    lead_ms = write_ms - heard_ms
    expected_lead = _BUF_FRAMES / 48000 * 1000 * speed   # 250 ms x 1.35
    assert abs(lead_ms - expected_lead) < 5, (lead_ms, expected_lead)
    # And the bug: the raw write cursor is way past the 80 ms resync threshold.
    assert lead_ms > 80


@pytest.fixture
def playing_player(qapp):
    """A VideoPlayer whose QMediaPlayer is stubbed to report PlayingState, with
    pitched audio primed and a full sink backlog. No media is loaded and no
    audio device is ever opened."""
    from PyQt6.QtMultimedia import QMediaPlayer
    from src.ui.video_player import VideoPlayer

    vp = VideoPlayer()
    real_player = vp.player
    vp.player = type("P", (), {
        "playbackState": lambda self: QMediaPlayer.PlaybackState.PlayingState,
    })()
    yield vp
    vp.player = real_player
    vp.shutdown()          # terminal teardown: also stops the conversion thread
    vp.deleteLater()


def _prime(vp, speed, ticks=60, frames=800):
    pa = vp._pitched
    vp._pitched_active = True
    pa._pcm = array.array('h', bytes(4 * 48000 * 20))
    pa._speed = speed
    pa._sink = _FakeSink(0, _BUF_BYTES)      # buffer completely full
    pa._io = type("IO", (), {"write": lambda self, b: None})()
    pos = 0.0
    for _ in range(ticks):
        pa._marks.append((pa._written, pos))
        pa._written += frames
        pos += frames * speed
    pa._pos = pos
    return pa


@pytest.mark.parametrize("speed", [1.0, 1.2, 1.35, 1.5, 2.0])
def test_resync_does_not_fire_on_buffer_lead(playing_player, speed):
    """Regression: the write-cursor comparison fired on ~97% of position ticks
    at every speed, yanking the read cursor backwards each time. With the
    playback position it must not fire at all when audio is actually in sync."""
    pa = _prime(playing_player, speed)
    video_ms = pa.playback_position_ms()      # audio and video agree
    before = pa._pos
    playing_player.resync_audio_if_drifting(int(round(video_ms)))
    assert pa._pos == before, "in-sync audio must not be resynced"


def test_resync_shifts_by_the_error_and_keeps_the_lead(playing_player):
    """A genuine drift must still be corrected — by the measured error, so the
    buffer lead survives (slamming _pos to video_ms destroyed it)."""
    pa = _prime(playing_player, 1.35)
    heard_ms = pa.playback_position_ms()
    before = pa._pos
    drift = 300.0                              # video is 300 ms ahead
    playing_player.resync_audio_if_drifting(int(round(heard_ms + drift)))

    moved_ms = (pa._pos - before) * 1000.0 / 48000
    assert abs(moved_ms - drift) < 1.0, moved_ms
    # The lead over the queued audio is preserved, not thrown away.
    assert pa._pos > before


def test_block_speed_lookup_tracks_the_per_sample_curve(qapp):
    """Sampling the curve once per SPEED_LOOKUP_BLOCK frames instead of per
    frame must not measurably change where the read cursor ends up. The block
    holds the speed from the block's start, so on a rising ramp it lags very
    slightly; the bound below is what that costs over the steepest ramp the
    automation lane can produce (0.05x -> 2.0x across 500 ms)."""
    from src.core.speed_curve import SPEED_LOOKUP_BLOCK, SpeedCurve

    kf = [(0, 0.05), (500, 2.0)]
    per_sample = SpeedCurve(kf, 1.0)
    blocked = SpeedCurve(kf, 1.0)
    rate = 48000
    n = rate  # one second of output

    pos = 0.0
    for _ in range(n):
        pos += per_sample.get_speed_at(int(pos * 1000 / rate))

    bpos = 0.0
    i = 0
    while i < n:
        speed = blocked.get_speed_at(int(bpos * 1000 / rate))
        for _ in range(min(SPEED_LOOKUP_BLOCK, n - i)):
            bpos += speed
            i += 1

    diff_ms = abs(pos - bpos) * 1000.0 / rate
    assert diff_ms < 5.0, diff_ms
