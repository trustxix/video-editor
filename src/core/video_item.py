from dataclasses import dataclass, field


@dataclass
class VideoItem:
    path: str
    video_w: int = 0
    video_h: int = 0
    fps: float = 0.0  # Source frame rate — probed on load, used by the slow-mo interpolator
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
    # Color adjustments — per-clip. Brightness is an additive luminance
    # shift in [-1.0, 1.0] (mapped to slider -100..100 → -0.5..0.5).
    # Exposure is in photographic stops, [-3.0, 3.0] (mapped -100..100).
    # Both apply to preview (approximate, via QPainter overlay) and to
    # export (precise, via ffmpeg's eq and exposure filters).
    brightness: float = 0.0
    exposure: float = 0.0
    pan_x: float = 0.0   # content pan — persisted so crop framing survives navigation
    pan_y: float = 0.0
    locked: bool = False
    probed: bool = False
    export_listed: bool = False  # manually added to the batch export list
    # Independent formant (timbre) shift in semitones, applied on export via a
    # two-pass rubberband chain (pitch and duration preserved). Negative makes
    # the voice deeper/warmer. See ffmpeg_runner._build_formant_audio_filter.
    formant: float = 0.0
    audio_normalize: bool = False  # per-clip loudness normalization on export
    normalize_data: dict | None = None  # cached first-pass loudnorm measurements
    speed_keyframes: list = field(default_factory=list)  # [(time_ms, speed), ...]
