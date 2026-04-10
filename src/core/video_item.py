from dataclasses import dataclass, field


@dataclass
class VideoItem:
    path: str
    video_w: int = 0
    video_h: int = 0
    duration_s: float = 0.0
    duration_ms: int = 0
    trim_start_ms: int = 0
    trim_end_ms: int = 0
    crop_x: int = 0
    crop_y: int = 0
    crop_w: int = 0
    crop_h: int = 0
    preset_name: str = "Free"
    crop_mode: str = "Crop"
    speed: float = 1.0
    stretch_h: float = 1.0
    stretch_v: float = 1.0
    locked: bool = False
    probed: bool = False
    speed_keyframes: list = field(default_factory=list)  # [(time_ms, speed), ...]
