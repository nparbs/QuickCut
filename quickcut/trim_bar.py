"""Timeline bar with a filmstrip, audio waveform, draggable start/end handles and a playhead."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

MIN_LENGTH = 0.1
MARGIN = 10
GRAB = 8
STRIP_H = 46
WAVE_H = 26


class TrimBar(QWidget):
    startChanged = Signal(float)
    endChanged = Signal(float)
    seekRequested = Signal(float)
    dragFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 0.0
        self.start = 0.0
        self.end = 0.0
        self.position = 0.0
        self.filmstrip: QImage | None = None
        self.waveform: QImage | None = None
        self._drag: str | None = None
        self.setFixedHeight(STRIP_H + WAVE_H + 24)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setToolTip("Drag the yellow handles to trim. Click anywhere to seek.")

    def set_clip(self, duration: float, start: float, end: float):
        self.duration, self.start, self.end = duration, start, end
        self.filmstrip = self.waveform = None
        self.update()

    def set_images(self, filmstrip: QImage | None = None, waveform: QImage | None = None):
        if filmstrip is not None:
            self.filmstrip = filmstrip
        if waveform is not None:
            self.waveform = waveform
        self.update()

    def set_range(self, start: float, end: float):
        self.start, self.end = start, end
        self.update()

    def set_position(self, seconds: float):
        self.position = seconds
        self.update()

    def _track(self) -> QRectF:
        return QRectF(MARGIN, 12, self.width() - 2 * MARGIN, STRIP_H + WAVE_H)

    def _x(self, t: float) -> float:
        r = self._track()
        return r.left() + (t / self.duration * r.width() if self.duration else 0)

    def _t(self, x: float) -> float:
        r = self._track()
        if not self.duration or r.width() <= 0:
            return 0.0
        return min(self.duration, max(0.0, (x - r.left()) / r.width() * self.duration))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._track()
        pal = self.palette()
        clip = QPainterPath()
        clip.addRoundedRect(r, 4, 4)
        p.fillPath(clip, pal.color(pal.ColorRole.Base).darker(115))
        if not self.duration:
            return

        p.save()
        p.setClipPath(clip)
        strip = QRectF(r.left(), r.top(), r.width(), STRIP_H)
        wave = QRectF(r.left(), r.top() + STRIP_H, r.width(), WAVE_H)
        if self.filmstrip is not None and not self.filmstrip.isNull():
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(strip, self.filmstrip)
        p.fillRect(wave, QColor(0, 0, 0, 90))
        if self.waveform is not None and not self.waveform.isNull():
            p.drawImage(wave, self.waveform)
        # Dim what's trimmed away.
        x0, x1 = self._x(self.start), self._x(self.end)
        shade = QColor(0, 0, 0, 150)
        p.fillRect(QRectF(r.left(), r.top(), x0 - r.left(), r.height()), shade)
        p.fillRect(QRectF(x1, r.top(), r.right() - x1, r.height()), shade)
        p.restore()

        accent = QColor("#f5b82e")
        p.setPen(QPen(accent, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(x0, r.top() + 1, x1 - x0, r.height() - 2))
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        for x in (x0, x1):
            p.drawRoundedRect(QRectF(x - 4, r.top() - 4, 8, r.height() + 8), 3, 3)
            p.setBrush(QColor(0, 0, 0, 120))
            p.drawRect(QRectF(x - 1, r.center().y() - 8, 2, 16))
            p.setBrush(accent)

        xp = self._x(self.position)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(int(xp), int(r.top() - 6), int(xp), int(r.bottom() + 6))
        tri = QPainterPath()
        tri.moveTo(xp - 6, 2)
        tri.lineTo(xp + 6, 2)
        tri.lineTo(xp, 10)
        tri.closeSubpath()
        p.fillPath(tri, QColor("#ffffff"))

    def _handle_at(self, x: float) -> str | None:
        d0, d1 = abs(x - self._x(self.start)), abs(x - self._x(self.end))
        if min(d0, d1) > GRAB:
            return None
        return "start" if d0 <= d1 else "end"

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or not self.duration:
            return
        x = e.position().x()
        self._drag = self._handle_at(x) or "seek"
        self._drag_to(x)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag:
            self._drag_to(x)
        else:
            self.setCursor(Qt.SizeHorCursor if self._handle_at(x) else Qt.PointingHandCursor)

    def mouseReleaseEvent(self, _):
        if self._drag in ("start", "end"):
            self.dragFinished.emit()
        self._drag = None

    def _drag_to(self, x: float):
        t = self._t(x)
        if self._drag == "start":
            self.start = min(t, self.end - MIN_LENGTH)
            self.startChanged.emit(max(0.0, self.start))
            self.seekRequested.emit(max(0.0, self.start))
        elif self._drag == "end":
            self.end = max(t, self.start + MIN_LENGTH)
            self.endChanged.emit(min(self.duration, self.end))
            self.seekRequested.emit(min(self.duration, self.end))
        else:
            self.seekRequested.emit(t)
        self.update()
