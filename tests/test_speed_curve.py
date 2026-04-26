"""Tests for src/core/speed_curve.py — pure-Python keyframe interpolation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.speed_curve import SpeedCurve


def test_empty_curve_returns_base_speed():
    c = SpeedCurve([], base_speed=1.0)
    assert c.get_speed_at(0) == 1.0
    assert c.get_speed_at(5000) == 1.0


def test_empty_curve_with_non_default_base():
    c = SpeedCurve([], base_speed=0.5)
    assert c.get_speed_at(1234) == 0.5


def test_single_keyframe_at_zero_returns_keyframe_value():
    c = SpeedCurve([(0, 2.0)], base_speed=1.0)
    assert c.get_speed_at(0) == 2.0
    # Past the only keyframe, value is held flat
    assert c.get_speed_at(5000) == 2.0


def test_single_keyframe_late_interpolates_from_base():
    """First keyframe at t>0 → before it, value ramps from base_speed to keyframe value."""
    c = SpeedCurve([(1000, 2.0)], base_speed=1.0)
    assert c.get_speed_at(1000) == 2.0
    # At t=500 (halfway), should be linear interpolation between 1.0 and 2.0
    assert abs(c.get_speed_at(500) - 1.5) < 0.001


def test_two_keyframes_linear_interpolation():
    c = SpeedCurve([(0, 1.0), (1000, 2.0)], base_speed=1.0)
    assert c.get_speed_at(0) == 1.0
    assert c.get_speed_at(1000) == 2.0
    assert abs(c.get_speed_at(500) - 1.5) < 0.001
    assert abs(c.get_speed_at(250) - 1.25) < 0.001


def test_after_last_keyframe_held_flat():
    c = SpeedCurve([(0, 1.0), (1000, 2.0)])
    assert c.get_speed_at(2000) == 2.0
    assert c.get_speed_at(99999) == 2.0


def test_keyframes_unsorted_input_handled():
    """Input keyframes might be in any order — class must sort internally."""
    c = SpeedCurve([(1000, 2.0), (500, 0.5), (0, 1.0)])
    assert c.get_speed_at(0) == 1.0
    assert c.get_speed_at(500) == 0.5
    assert c.get_speed_at(1000) == 2.0


def test_duplicate_timestamp_keyframes():
    """Two keyframes at the same time: span=0, must not divide-by-zero."""
    c = SpeedCurve([(500, 1.0), (500, 2.0)])
    # Should return one of them (the implementation picks the second)
    result = c.get_speed_at(500)
    assert result in (1.0, 2.0)


def test_three_keyframes_all_segments_interpolated():
    c = SpeedCurve([(0, 1.0), (1000, 2.0), (2000, 0.5)])
    assert abs(c.get_speed_at(500) - 1.5) < 0.001
    assert abs(c.get_speed_at(1500) - 1.25) < 0.001  # midpoint of 2.0 → 0.5
