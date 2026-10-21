"""Draws QuickCut's icon and writes quickcut/resources/icon.png and packaging/quickcut.ico."""
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parent.parent


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0, QColor("#f7c548"))
    grad.setColorAt(1, QColor("#c98f12"))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 52 * s, 52 * s)
    # Film frame
    p.setBrush(QColor("#202124"))
    p.drawRoundedRect(QRectF(44 * s, 64 * s, 168 * s, 128 * s), 18 * s, 18 * s)
    # Play triangle
    tri = QPainterPath()
    tri.moveTo(104 * s, 96 * s)
    tri.lineTo(160 * s, 128 * s)
    tri.lineTo(104 * s, 160 * s)
    tri.closeSubpath()
    p.fillPath(tri, QColor("#f7c548"))
    # Cut line
    pen = QPen(QColor("#ffffff"), 12 * s, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(76 * s, 214 * s), QPointF(180 * s, 42 * s))
    p.end()
    return img


if __name__ == "__main__":
    app = QGuiApplication([])
    draw(256).save(str(ROOT / "quickcut" / "resources" / "icon.png"))
    # Qt writes multi-size .ico from the largest image; Windows scales it down cleanly.
    draw(256).save(str(ROOT / "packaging" / "quickcut.ico"))
    print("icons written")
