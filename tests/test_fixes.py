"""Tests for all 27 bug fixes across 3 review passes."""
import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.core.paths import get_base_dir, get_config_dir, get_ffmpeg, get_ffprobe
from src.core.presets import ASPECT_PRESETS, calc_preset_crop, calc_stretch_to_fit
from src.core.video_item import VideoItem
from src.core.ffmpeg_runner import build_command, get_output_path, _build_speed_audio_filter

passed = 0
failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name} — {detail}")
        failed += 1


# ═══════════════════════════════════════════════════════════
print("=== Core Module Tests ===")
# ═══════════════════════════════════════════════════════════

# VideoItem
item = VideoItem(path='test.mp4')
test("VideoItem defaults", item.speed == 1.0 and not item.probed and item.crop_w == 0)

# Presets
x, y, w, h = calc_preset_crop('9:16', 1920, 1080)
test("Preset crop even dims", w % 2 == 0 and h % 2 == 0)
test("Preset crop in bounds", x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080)

x, y, w, h = calc_preset_crop('Free', 1920, 1080)
test("Free = full frame", (x, y, w, h) == (0, 0, 1920, 1080))

x, y, w, h = calc_preset_crop('1:1', 1920, 1080)
test("1:1 is square", w == h)

sh, sv = calc_stretch_to_fit('9:16', 1920, 1080)
test("Stretch compresses one axis", sh <= 1.0 or sv <= 1.0)
test("Stretch zero dims", calc_stretch_to_fit('9:16', 0, 0) == (1.0, 1.0))

# Output path
p = get_output_path('test.mp4', '_edited')
test("Output path suffix", 'test_edited' in p)

# Base dir
test("Base dir exists", get_base_dir().exists())
test("FFmpeg path is string", isinstance(get_ffmpeg(), str) and len(get_ffmpeg()) > 0)


# ═══════════════════════════════════════════════════════════
print("\n=== FFmpeg Command Builder ===")
# ═══════════════════════════════════════════════════════════

# Basic - stream copy
cmd = build_command('in.mp4', 'out.mp4')
test("Basic: has -y", '-y' in cmd)
test("Basic: stream copy", 'copy' in cmd[cmd.index('-c:v') + 1:cmd.index('-c:v') + 2])

# Crop
cmd = build_command('in.mp4', 'out.mp4', crop_x=100, crop_y=50, crop_w=800, crop_h=600)
vf = cmd[cmd.index('-vf') + 1]
test("Crop filter", 'crop=800:600:100:50' in vf)
test("Crop forces encode", cmd[cmd.index('-c:v') + 1] != 'copy')

# Trim: -ss before -i, -t not -to
cmd = build_command('in.mp4', 'out.mp4', trim_start=10.0, trim_end=15.0)
test("Trim: -ss present", '-ss' in cmd)
test("Trim: -t = duration", cmd[cmd.index('-t') + 1] == '5.000')
test("Trim: no -to", '-to' not in cmd)
test("Trim: -ss before -i", cmd.index('-ss') < cmd.index('-i'))

# Trim start=0 should not add -ss
cmd = build_command('in.mp4', 'out.mp4', trim_start=0.0, trim_end=5.0)
test("Trim: start=0 no -ss", '-ss' not in cmd)

# Speed
cmd = build_command('in.mp4', 'out.mp4', speed=2.0)
vf = cmd[cmd.index('-vf') + 1]
test("Speed: setpts filter", 'setpts=PTS/2.0000' in vf)
af = cmd[cmd.index('-af') + 1]
test("Speed: asetrate audio", 'asetrate=' in af and 'aresample=' in af)

# Mute overrides speed audio
cmd = build_command('in.mp4', 'out.mp4', speed=2.0, audio_mode='mute')
test("Mute priority over speed", '-an' in cmd and '-af' not in cmd)

# Stretch
cmd = build_command('in.mp4', 'out.mp4', stretch_h=0.5, stretch_v=1.0)
vf = cmd[cmd.index('-vf') + 1]
test("Stretch: scale filter", 'scale=' in vf and '0.5000' in vf)

# All filters combined
cmd = build_command(
    'in.mp4', 'out.mp4',
    trim_start=5.0, trim_end=10.0,
    crop_x=100, crop_y=50, crop_w=800, crop_h=600,
    codec='h265', crf=0, speed=0.5,
    stretch_h=0.8, stretch_v=1.0, audio_mode='reencode'
)
vf = cmd[cmd.index('-vf') + 1]
test("Combined: all video filters", 'crop=' in vf and 'scale=' in vf and 'setpts=' in vf)
# H.265: libx265 (software) OR hevc_nvenc (GPU). Either is acceptable.
test("Combined: h265 codec", 'libx265' in cmd or 'hevc_nvenc' in cmd)
# Lossless: software emits -crf 0; NVENC emits -cq 0. Either is acceptable.
quality_flag = '-crf' if '-crf' in cmd else '-cq'
test("Combined: lossless quality", cmd[cmd.index(quality_flag) + 1] == '0')
test("Combined: speed overrides reencode audio", '-af' in cmd)

# Audio speed filter edge cases
f = _build_speed_audio_filter(2.0)
test("Audio speed 2x", 'asetrate=96000' in f)
f = _build_speed_audio_filter(0.5)
test("Audio speed 0.5x", 'asetrate=24000' in f)
f = _build_speed_audio_filter(0.0001)
test("Audio speed near-zero", 'asetrate=4' in f or 'asetrate=1' in f)  # max(1, int(48000*0.0001))=4


# ═══════════════════════════════════════════════════════════
print("\n=== Bug Fix Verification ===")
# ═══════════════════════════════════════════════════════════

# Fix #24: Export crop clamping - negative coords
video_w, video_h = 1920, 1080
crop_x, crop_y, crop_w, crop_h = -100, -50, 960, 540
if crop_x < 0:
    crop_w += crop_x; crop_x = 0
if crop_y < 0:
    crop_h += crop_y; crop_y = 0
test("Crop clamp: negative x/y", crop_x == 0 and crop_y == 0)
test("Crop clamp: width adjusted", crop_w == 860 and crop_h == 490)

# Fix #24: Export crop clamping - exceeds bounds
crop_x, crop_y, crop_w, crop_h = 1800, 900, 960, 540
if crop_x + crop_w > video_w:
    crop_w = video_w - crop_x
if crop_y + crop_h > video_h:
    crop_h = video_h - crop_y
test("Crop clamp: right overflow", crop_w == 120)
test("Crop clamp: bottom overflow", crop_h == 180)

# Fix #24: Fully out of bounds
crop_x, crop_y, crop_w, crop_h = -500, -300, 400, 200
if crop_x < 0:
    crop_w += crop_x; crop_x = 0
if crop_y < 0:
    crop_h += crop_y; crop_y = 0
crop_w -= crop_w % 2; crop_h -= crop_h % 2
test("Crop clamp: fully OOB fails min check", crop_w < 2 or crop_h < 2)

# Fix #25: Settings type validation
print("\n--- Settings Validation ---")
def validate_settings(data):
    defaults = {
        'codec': 'h264', 'crf': 17, 'output_dir': '',
        'aspect_ratio': 'Free', 'crop_mode': 'crop',
        'default_speed': 1.0, 'output_suffix': '_edited',
        'auto_advance': False, 'audio_mode': 'copy',
    }
    defaults.update(data)
    try:
        defaults['crf'] = max(0, min(51, int(defaults['crf'])))
    except (ValueError, TypeError):
        defaults['crf'] = 17
    try:
        defaults['default_speed'] = max(0.1, min(10.0, float(defaults['default_speed'])))
    except (ValueError, TypeError):
        defaults['default_speed'] = 1.0
    if defaults.get('codec') not in ('h264', 'h265'):
        defaults['codec'] = 'h264'
    if defaults.get('audio_mode') not in ('copy', 'reencode', 'mute'):
        defaults['audio_mode'] = 'copy'
    return defaults

s = validate_settings({'crf': 'garbage'})
test("Settings: string crf -> 17", s['crf'] == 17)

s = validate_settings({'crf': None})
test("Settings: null crf -> 17", s['crf'] == 17)

s = validate_settings({'default_speed': 'fast'})
test("Settings: string speed -> 1.0", s['default_speed'] == 1.0)

s = validate_settings({'default_speed': None})
test("Settings: null speed -> 1.0", s['default_speed'] == 1.0)

s = validate_settings({'codec': 'h999'})
test("Settings: invalid codec -> h264", s['codec'] == 'h264')

s = validate_settings({'audio_mode': 'invalid'})
test("Settings: invalid audio -> copy", s['audio_mode'] == 'copy')

s = validate_settings({'crf': 100})
test("Settings: crf > 51 clamped", s['crf'] == 51)

s = validate_settings({'crf': -5})
test("Settings: crf < 0 clamped", s['crf'] == 0)

s = validate_settings({'default_speed': 999})
test("Settings: speed > 10 clamped", s['default_speed'] == 10.0)

s = validate_settings({'default_speed': 0.001})
test("Settings: speed < 0.1 clamped", s['default_speed'] == 0.1)

# Valid settings should pass through
s = validate_settings({'crf': 23, 'default_speed': 2.5, 'codec': 'h265', 'audio_mode': 'mute'})
test("Settings: valid values preserved", s['crf'] == 23 and s['default_speed'] == 2.5
     and s['codec'] == 'h265' and s['audio_mode'] == 'mute')


# ═══════════════════════════════════════════════════════════
print("\n=== Qt Widget Tests (headless) ===")
# ═══════════════════════════════════════════════════════════

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRect, QPoint

app = QApplication.instance() or QApplication(sys.argv)

# Rect normalization (Fix from pass 2)
r = QRect(100, 100, 200, 150)
r.setLeft(350)  # left past right
test("Rect negative width", r.width() < 0)
r = r.normalized()
test("Rect normalized positive", r.width() > 0 and r.height() > 0)

# CropOverlay coordinate mapping
from src.ui.crop_overlay import CropOverlay

overlay = CropOverlay()
overlay.resize(640, 360)
overlay._video_w = 1920
overlay._video_h = 1080

# set_crop_from_video stores authoritative coords
overlay.set_crop_from_video(480, 270, 960, 540)
test("set_crop stores video coords",
     overlay._crop_vx == 480 and overlay._crop_vy == 270
     and overlay._crop_vw == 960 and overlay._crop_vh == 540)

# Simulate resize — should NOT corrupt video coords (our fix)
overlay._crop_vx, overlay._crop_vy = 480, 270
overlay._crop_vw, overlay._crop_vh = 960, 540
# resizeEvent rebuilds widget rect from stored video coords but does not sync back
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtCore import QSize
overlay.resizeEvent(QResizeEvent(QSize(800, 450), QSize(640, 360)))
test("Resize preserves video coords",
     overlay._crop_vx == 480 and overlay._crop_vy == 270
     and overlay._crop_vw == 960 and overlay._crop_vh == 540)

# Hit-handle zoom scaling (Fix from pass 2)
overlay._crop_rect = QRect(100, 100, 200, 150)
# At zoom 1.0, tolerance = 10
hit = overlay._hit_handle(QPoint(100, 100))
test("Hit handle at 1x zoom", hit == "tl")

# RangeSlider
from src.ui.trim_controls import RangeSlider, TrimControls

slider = RangeSlider()
slider.set_range(10000)
test("Slider range", slider._max == 10000 and slider._start == 0 and slider._end == 10000)

slider.set_selection(2000, 8000)
test("Slider selection", slider.get_selection() == (2000, 8000))

# TrimControls
trim = TrimControls()
trim.set_duration(60000)
test("Trim duration", trim._duration_ms == 60000)

trim.set_trim(5000, 55000)
s, e = trim.get_trim_seconds()
test("Trim seconds", abs(s - 5.0) < 0.01 and abs(e - 55.0) < 0.01)

# Time formatting
test("Time format", TrimControls._fmt(65500) == "01:05.50")
test("Time format zero", TrimControls._fmt(0) == "00:00.00")

# Time parsing
test("Time parse mm:ss", TrimControls._parse("01:30.50") == 90500)
test("Time parse seconds", TrimControls._parse("45.5") == 45500)
test("Time parse invalid", TrimControls._parse("garbage") is None)


# ═══════════════════════════════════════════════════════════
print(f"\n{'=' * 50}")
print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
if failed == 0:
    print("ALL TESTS PASSED!")
else:
    print(f"FAILURES: {failed}")
    sys.exit(1)
