"""Generate assets/icon.ico for the Video Editor.

Run this once (or whenever you change _draw_icon) to regenerate the icon
that's used in three places:

    1. Runtime: QApplication.setWindowIcon() in main.py
    2. PyInstaller build: tools/build.bat --icon flag
    3. Desktop shortcut: tools/make_shortcut.ps1 IconLocation

Usage:
    python tools/generate_icon.py

The ICO file that Windows shows in Explorer / the taskbar / the .exe needs
multiple resolutions embedded in a single file. We render each size with
QPainter, encode it to PNG via QImage.save(), then hand-assemble the ICO
container around those PNG blobs. Doing it this way avoids depending on
Qt's qico image-format plugin (which isn't always shipped with PyQt6).
"""

from __future__ import annotations

import struct
import sys
from io import BytesIO
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QBrush, QFont, QPolygon
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication


# Windows icon size set. 256 is required for modern Windows to render a crisp
# icon at high DPI; the smaller sizes are used in tight spots (title bar, the
# legacy Alt-Tab switcher, etc.). Keeping them all in one .ico lets Windows
# pick the best match for whatever it's drawing.
ICON_SIZES = (16, 32, 48, 256)


# ─── USER CONTRIBUTION ZONE ────────────────────────────────────────────────
# This function is the only meaningful decision in this file. Everything
# else (PNG encoding, ICO container assembly) is just plumbing.
#
# Design guidance:
#   - Draw within a (size x size) area. `size` is one of 16/32/48/256.
#   - Scale line widths and radii proportional to `size` so the 16px
#     version doesn't turn into mush.
#   - Use painter.setRenderHint(QPainter.RenderHint.Antialiasing) already
#     applied by the caller.
#   - Keep the silhouette recognizable at 16px — Windows shows this in the
#     taskbar. Complex detail at small sizes reads as noise.
#
# The default below is intentionally plain — a flat dark square with a
# white play triangle — so you know at a glance it's a placeholder.
def _draw_icon(painter: QPainter, size: int) -> None:
    # Design: film-strip frame + cyan play triangle.
    # Colors pulled from src/ui/themes.py "Midnight" palette so the icon
    # matches the app's visual identity when that theme is active.
    BG       = QColor("#0f1419")  # Midnight bg
    HOLE     = QColor("#1a1f2a")  # Midnight bg_alt — sprocket holes read as cut-outs, not dots
    BORDER   = QColor("#2d3444")  # Midnight border — subtle inner rim at large sizes
    ACCENT   = QColor("#64ffda")  # Midnight accent — play triangle
    HIGHLIGHT = QColor("#80ffed") # Midnight accent_hover — soft highlight on triangle

    # 1. Rounded-square background.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(BG)
    radius = size * 0.20
    painter.drawRoundedRect(0, 0, size, size, radius, radius)

    # 2. Thin inner border ring for depth — only visible at larger sizes
    #    where a sub-pixel stroke won't turn to mush.
    if size >= 48:
        pen = QPen(BORDER)
        pen.setWidthF(max(1.0, size * 0.014))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = size * 0.06
        painter.drawRoundedRect(
            int(inset), int(inset),
            int(size - 2 * inset), int(size - 2 * inset),
            radius * 0.78, radius * 0.78,
        )
        painter.setPen(Qt.PenStyle.NoPen)

    # 3. Film-strip sprocket holes down the left and right sides.
    #    Drop them at small sizes — they just become noise below ~32px.
    if size >= 48:
        painter.setBrush(HOLE)
        hole_w = size * 0.09
        hole_h = size * 0.11
        hole_rad = hole_w * 0.28
        left_x = size * 0.10
        right_x = size - size * 0.10 - hole_w
        # 3 evenly-spaced holes per side, centered vertically
        for row in range(3):
            y = size * (0.22 + 0.28 * row) - hole_h / 2
            painter.drawRoundedRect(
                int(left_x), int(y), int(hole_w), int(hole_h),
                hole_rad, hole_rad,
            )
            painter.drawRoundedRect(
                int(right_x), int(y), int(hole_w), int(hole_h),
                hole_rad, hole_rad,
            )

    # 4. Play triangle — the primary silhouette. Centered, bold, cyan.
    #    Sized so it reads as "play" even at 16px where everything else
    #    has dropped away.
    painter.setBrush(ACCENT)
    cx = size / 2
    cy = size / 2
    r = size * 0.26
    triangle = QPolygon([
        QPoint(int(cx - r * 0.55), int(cy - r)),
        QPoint(int(cx - r * 0.55), int(cy + r)),
        QPoint(int(cx + r),        int(cy)),
    ])
    painter.drawPolygon(triangle)

    # 5. Soft highlight along the triangle's leading (left) edge for
    #    subtle dimensionality. Omitted at small sizes to keep the
    #    silhouette crisp.
    if size >= 48:
        pen = QPen(HIGHLIGHT)
        pen.setWidthF(max(1.0, size * 0.012))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(
            int(cx - r * 0.55), int(cy - r * 0.9),
            int(cx - r * 0.55), int(cy + r * 0.9),
        )
# ─── END USER CONTRIBUTION ZONE ────────────────────────────────────────────


def _render_png(size: int) -> bytes:
    """Render _draw_icon into a size×size PNG and return the bytes."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    try:
        _draw_icon(painter, size)
    finally:
        painter.end()

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buf, "PNG"):
        raise RuntimeError(f"QImage.save failed for size {size}")
    return bytes(buf.data())


def _build_ico(png_blobs: dict[int, bytes]) -> bytes:
    """Pack rendered PNGs into a valid .ico file.

    Format ref: https://en.wikipedia.org/wiki/ICO_(file_format)

    Layout:
        ICONDIR       (6 bytes)
        ICONDIRENTRY  (16 bytes) * N
        image data    (variable) * N   -- we always use embedded PNG
    """
    sizes = sorted(png_blobs.keys())
    n = len(sizes)

    # ICONDIR: reserved=0, type=1 (icon), count=N
    out = BytesIO()
    out.write(struct.pack("<HHH", 0, 1, n))

    # Compute where each image blob will start. The directory ends at
    # 6 + 16 * N; image blobs follow back-to-back.
    offset = 6 + 16 * n
    for size in sizes:
        blob = png_blobs[size]
        # Width/Height fields are one byte each; 256 is encoded as 0.
        w = 0 if size == 256 else size
        h = 0 if size == 256 else size
        # ICONDIRENTRY fields: w, h, color_count, reserved, planes,
        # bitcount, image_size, image_offset.
        out.write(struct.pack(
            "<BBBBHHII",
            w, h,
            0,   # color_count (0 for >256 colors)
            0,   # reserved
            1,   # color planes
            32,  # bits per pixel
            len(blob),
            offset,
        ))
        offset += len(blob)

    # Now append the PNG blobs in the same order.
    for size in sizes:
        out.write(png_blobs[size])

    return out.getvalue()


def main() -> int:
    # QPainter needs a running QApplication even for offscreen drawing —
    # the font engine and image primitives reach for it.
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app  # silence unused warning

    project_root = Path(__file__).resolve().parent.parent
    out_path = project_root / "assets" / "icon.ico"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Rendering icon at sizes: {', '.join(str(s) for s in ICON_SIZES)}")
    blobs = {size: _render_png(size) for size in ICON_SIZES}

    ico_bytes = _build_ico(blobs)
    out_path.write_bytes(ico_bytes)

    print(f"Wrote {out_path} ({len(ico_bytes):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
