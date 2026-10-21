"""Render text and logo overlays to transparent PNGs the size of the output frame.

Drawing with Qt instead of ffmpeg's drawtext avoids font-path escaping problems on Windows and
gives the same result as what you'd see in any Qt text box.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

POSITIONS = [("Bottom", "bottom"), ("Top", "top"), ("Centre", "center"),
             ("Bottom left", "bottom-left"), ("Bottom right", "bottom-right"),
             ("Top left", "top-left"), ("Top right", "top-right")]


def _place(pos: str, w: int, h: int, bw: float, bh: float, margin: float) -> tuple[float, float]:
    x = {"left": margin, "right": w - bw - margin}.get(pos.split("-")[-1], (w - bw) / 2)
    vertical = pos.split("-")[0]
    y = {"top": margin, "bottom": h - bh - margin}.get(vertical, (h - bh) / 2)
    return x, y


def render_text(path: str, w: int, h: int, text: str, position: str = "bottom",
                size_pct: float = 6.0, color: str = "#ffffff", box: bool = True) -> str:
    """size_pct is the text height as a percentage of the frame's short side."""
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    font = QFont("Segoe UI")
    font.setPixelSize(max(8, int(min(w, h) * size_pct / 100)))
    font.setWeight(QFont.DemiBold)
    p.setFont(font)
    fm = QFontMetricsF(font)
    lines = text.splitlines() or [""]
    line_h = fm.lineSpacing()
    text_w = max(fm.horizontalAdvance(line) for line in lines)
    text_h = line_h * len(lines)
    pad = font.pixelSize() * 0.35
    margin = min(w, h) * 0.05
    x, y = _place(position, w, h, text_w + 2 * pad, text_h + 2 * pad, margin)
    if box:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(QRectF(x, y, text_w + 2 * pad, text_h + 2 * pad), pad, pad)
    align = Qt.AlignHCenter if position in ("top", "bottom", "center") else \
        (Qt.AlignLeft if position.endswith("left") else Qt.AlignRight)
    for i, line in enumerate(lines):
        lw = fm.horizontalAdvance(line)
        lx = x + pad + {Qt.AlignLeft: 0, Qt.AlignRight: text_w - lw}.get(align, (text_w - lw) / 2)
        ly = y + pad + i * line_h + fm.ascent()
        outline = QPainterPath()
        outline.addText(lx, ly, font, line)
        if not box:  # without a box, an outline keeps text readable on bright video
            p.strokePath(outline, QPen(QColor(0, 0, 0, 200), max(2.0, font.pixelSize() / 14)))
        p.fillPath(outline, QColor(color))
    p.end()
    img.save(path)
    return path


def render_logo(path: str, w: int, h: int, logo: str, position: str = "bottom-right",
                size_pct: float = 15.0, opacity: float = 0.85) -> str:
    """size_pct is the logo width as a percentage of the frame width."""
    src = QImage(logo)
    if src.isNull():
        raise ValueError(f"Couldn't read the logo image: {os.path.basename(logo)}")
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    lw = max(4, int(w * size_pct / 100))
    scaled = src.scaledToWidth(lw, Qt.SmoothTransformation)
    margin = min(w, h) * 0.04
    x, y = _place(position, w, h, scaled.width(), scaled.height(), margin)
    p = QPainter(img)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setOpacity(opacity)
    p.drawImage(int(x), int(y), scaled)
    p.end()
    img.save(path)
    return path
