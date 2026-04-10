ASPECT_PRESETS = {
    "Free": None,
    "16:9": (16, 9),
    "9:16": (9, 16),
    "4:3": (4, 3),
    "1:1": (1, 1),
    "4:5": (4, 5),
}


def calc_preset_crop(preset_name: str, video_w: int, video_h: int) -> tuple[int, int, int, int]:
    """Return (x, y, w, h) for a centred crop at the given aspect ratio."""
    ratio = ASPECT_PRESETS.get(preset_name)
    if ratio is None:
        return 0, 0, video_w, video_h

    target_w_ratio, target_h_ratio = ratio
    scale = min(video_w / target_w_ratio, video_h / target_h_ratio)
    crop_w = int(target_w_ratio * scale)
    crop_h = int(target_h_ratio * scale)
    # ensure even dimensions (ffmpeg requirement for most codecs)
    crop_w = max(2, crop_w - crop_w % 2)
    crop_h = max(2, crop_h - crop_h % 2)
    x = (video_w - crop_w) // 2
    y = (video_h - crop_h) // 2
    return x, y, crop_w, crop_h


def calc_stretch_to_fit(preset_name: str, video_w: int, video_h: int) -> tuple[float, float]:
    """Return (stretch_h, stretch_v) to make the video match the target aspect ratio.
    Always compresses one axis (never upscales)."""
    ratio = ASPECT_PRESETS.get(preset_name)
    if ratio is None or video_w == 0 or video_h == 0:
        return 1.0, 1.0
    aw, ah = ratio
    # Option A: adjust width to match ratio at current height
    stretch_h = (video_h * aw) / (ah * video_w)
    if stretch_h <= 1.0:
        return stretch_h, 1.0
    # Option B: adjust height to match ratio at current width
    stretch_v = (video_w * ah) / (aw * video_h)
    return 1.0, stretch_v
