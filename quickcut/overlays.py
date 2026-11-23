"""Render text and picture layers to transparent images the size of the output frame.

Drawing with Qt instead of ffmpeg's drawtext avoids font-path escaping problems on Windows, and the
live player draws layers with the same code, so what you see while editing is what gets exported.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

from .models import Layer

_image_cache: dict[str, QImage] = {}


def _picture(path: str) -> QImage:
    key = os.path.normcase(os.path.abspath(path))
    stamp = f"{key}|{os.path.getmtime(path) if os.path.isfile(path) else 0}"
    img = _image_cache.get(stamp)
    if img is None:
        img = QImage(path)
        if len(_image_cache) > 32:
            _image_cache.clear()
        _image_cache[stamp] = img
    return img


def _font(layer: Layer, w: int, h: int) -> QFont:
    font = QFont(layer.font or "Segoe UI")
    font.setPixelSize(max(8, int(min(w, h) * layer.size / 100)))
    font.setWeight(QFont.DemiBold if layer.bold else QFont.Normal)
    return font


def layer_rect(layer: Layer, w: int, h: int) -> QRectF:
    """Where the layer lands in a w x h frame (box included), without drawing it."""
    cx, cy = layer.x * w, layer.y * h
    if layer.kind == "image":
        src = _picture(layer.image) if layer.image else QImage()
        if src.isNull():
            return QRectF()
        lw = max(4.0, w * layer.size / 100)
        lh = lw * src.height() / max(1, src.width())
        return QRectF(cx - lw / 2, cy - lh / 2, lw, lh)
    font = _font(layer, w, h)
    fm = QFontMetricsF(font)
    lines = layer.text.splitlines() or [""]
    text_w = max(fm.horizontalAdvance(line) for line in lines)
    text_h = fm.lineSpacing() * len(lines)
    pad = font.pixelSize() * 0.35
    bw, bh = text_w + 2 * pad, text_h + 2 * pad
    return QRectF(cx - bw / 2, cy - bh / 2, bw, bh)


def paint_layer(p: QPainter, layer: Layer, w: int, h: int):
    """Draw one layer onto a painter whose coordinates are the w x h frame."""
    rect = layer_rect(layer, w, h)
    if rect.isEmpty():
        return
    p.save()
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    p.setOpacity(max(0.0, min(1.0, layer.opacity)))
    if layer.kind == "image":
        src = _picture(layer.image)
        scaled = src.scaled(max(1, round(rect.width())), max(1, round(rect.height())),
                            Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        p.drawImage(rect.topLeft(), scaled)
        p.restore()
        return
    font = _font(layer, w, h)
    fm = QFontMetricsF(font)
    lines = layer.text.splitlines() or [""]
    pad = font.pixelSize() * 0.35
    if layer.box:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(rect, pad, pad)
    for i, line in enumerate(lines):
        lx = rect.center().x() - fm.horizontalAdvance(line) / 2
        ly = rect.top() + pad + i * fm.lineSpacing() + fm.ascent()
        outline = QPainterPath()
        outline.addText(lx, ly, font, line)
        if not layer.box:  # without a box, an outline keeps text readable on bright video
            p.strokePath(outline, QPen(QColor(0, 0, 0, 200), max(2.0, font.pixelSize() / 14)))
        p.fillPath(outline, QColor(layer.color))
    p.restore()


def layer_image(layer: Layer, w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    paint_layer(p, layer, w, h)
    p.end()
    return img


def render_layer(path: str, layer: Layer, w: int, h: int) -> str:
    if layer.kind == "image" and (not layer.image or _picture(layer.image).isNull()):
        raise ValueError(f"Couldn't read the picture: {os.path.basename(layer.image) or '(none)'}")
    layer_image(layer, w, h).save(path)
    return path


# Where the old single text/logo settings put things, for opening projects from 0.3 and earlier.
_OLD_POSITIONS = {"bottom": (0.5, 0.9), "top": (0.5, 0.1), "center": (0.5, 0.5),
                  "bottom-left": (0.2, 0.9), "bottom-right": (0.8, 0.9),
                  "top-left": (0.2, 0.1), "top-right": (0.8, 0.1)}


def layers_from_old_settings(state: dict) -> list[Layer]:
    """Turn the text and logo fields of an old project's export settings into layers."""
    layers = []
    if state.get("logo"):
        x, y = _OLD_POSITIONS.get(state.get("logo_pos") or "bottom-right", (0.8, 0.9))
        layers.append(Layer("image", image=state["logo"], x=x, y=y,
                            size=float(state.get("logo_size", 15)),
                            opacity=float(state.get("logo_opacity", 85)) / 100))
    if str(state.get("text", "")).strip():
        x, y = _OLD_POSITIONS.get(state.get("text_pos") or "bottom", (0.5, 0.9))
        layers.append(Layer("text", text=state["text"], x=x, y=y,
                            size=float(state.get("text_size", 6)),
                            color=state.get("text_color", "#ffffff"),
                            box=bool(state.get("text_box", True)),
                            start=float(state.get("text_start", 0)),
                            duration=float(state.get("text_duration", 0))))
    return layers
