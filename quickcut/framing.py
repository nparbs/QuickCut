"""Where each clip sits in the output frame: zoom and position.

The export and the live player both use these numbers, so what you line up in the player is what
gets exported. Zoom is relative to *filling* the frame: 1.0 just covers it (edges cropped off when
the shapes differ), lower values shrink the clip until bars or the blurred background show around it.
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_ZOOM = 0.1
MAX_ZOOM = 4.0


@dataclass
class Placement:
    scale: float     # output pixels per source pixel
    left: float      # top-left of the whole scaled clip in the frame (can be negative)
    top: float
    width: float     # size of the whole scaled clip
    height: float


def fit_zoom(cw: float, ch: float, fw: float, fh: float) -> float:
    """The zoom at which the whole clip just fits inside the frame (bars on two sides)."""
    contain = min(fw / cw, fh / ch)
    cover = max(fw / cw, fh / ch)
    return contain / cover


def auto_zoom(fit: str, cw: float, ch: float, fw: float, fh: float) -> float:
    """The zoom the frame-wide Fill setting gives a clip that hasn't been framed by hand."""
    return 1.0 if fit == "crop" else fit_zoom(cw, ch, fw, fh)


def clip_zoom(zoom: float | None, fit: str, cw: float, ch: float, fw: float, fh: float) -> float:
    return auto_zoom(fit, cw, ch, fw, fh) if zoom is None else clamp_zoom(zoom)


def clamp_zoom(zoom: float) -> float:
    return max(MIN_ZOOM, min(MAX_ZOOM, zoom))


def _clamp_centre(centre: float, size: float, frame: float) -> float:
    # Bigger than the frame: no gap may open at either edge. Smaller: stay fully inside.
    half = size / 2
    lo, hi = (frame - half, half) if size >= frame else (half, frame - half)
    return min(max(centre, lo), hi)


def placement(cw: float, ch: float, fw: float, fh: float, zoom: float,
              pan_x: float = 0.5, pan_y: float = 0.5) -> Placement:
    """cw/ch: the clip's displayed size (after rotation); fw/fh: the output frame;
    pan_x/pan_y: where the clip's centre sits, as a fraction of the frame."""
    scale = max(fw / cw, fh / ch) * zoom
    w, h = cw * scale, ch * scale
    cx = _clamp_centre(pan_x * fw, w, fw)
    cy = _clamp_centre(pan_y * fh, h, fh)
    return Placement(scale, cx - w / 2, cy - h / 2, w, h)


def clamp_pan(cw: float, ch: float, fw: float, fh: float, zoom: float,
              pan_x: float, pan_y: float) -> tuple[float, float]:
    """The pan values after keeping the clip in bounds, so stored values match what's shown."""
    p = placement(cw, ch, fw, fh, zoom, pan_x, pan_y)
    return (p.left + p.width / 2) / fw, (p.top + p.height / 2) / fh


def _even_down(v: float) -> int:
    return max(2, int(v) // 2 * 2)


@dataclass
class CropPlan:
    """How to cut and place one clip with ffmpeg: crop the source, scale it, put it at x/y."""
    crop: tuple[int, int, int, int] | None   # source w, h, x, y; None keeps the whole source
    width: int                               # scaled size of the visible part
    height: int
    x: int                                   # where it goes in the frame
    y: int
    covers: bool                             # fills the whole frame, nothing shows behind it


def crop_plan(cw: int, ch: int, fw: int, fh: int, zoom: float,
              pan_x: float = 0.5, pan_y: float = 0.5) -> CropPlan:
    p = placement(cw, ch, fw, fh, zoom, pan_x, pan_y)
    # The part of the scaled clip that lands inside the frame...
    vx0, vy0 = max(0.0, p.left), max(0.0, p.top)
    vx1, vy1 = min(fw, p.left + p.width), min(fh, p.top + p.height)
    vw, vh = min(fw, _even_down(vx1 - vx0 + 1)), min(fh, _even_down(vy1 - vy0 + 1))
    x = min(max(0, int(round(vx0))), fw - vw)
    y = min(max(0, int(round(vy0))), fh - vh)
    # ...and the matching part of the source, cut out before scaling so zooming in stays cheap.
    sx, sy = (vx0 - p.left) / p.scale, (vy0 - p.top) / p.scale
    sw, sh = (vx1 - vx0) / p.scale, (vy1 - vy0) / p.scale
    isw, ish = max(2, min(cw, int(round(sw)))), max(2, min(ch, int(round(sh))))
    isx, isy = min(max(0, int(round(sx))), cw - isw), min(max(0, int(round(sy))), ch - ish)
    crop = None if (isw, ish, isx, isy) == (cw, ch, 0, 0) else (isw, ish, isx, isy)
    return CropPlan(crop, vw, vh, x, y, (vw, vh, x, y) == (fw, fh, 0, 0))
