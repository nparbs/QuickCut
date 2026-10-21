"""Video preview that shows a clip's rotation and flips the way they'll be exported."""
from __future__ import annotations

from PySide6.QtCore import QSizeF, Qt
from PySide6.QtGui import QColor, QPainter, QTransform
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView


class PlayerView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsVideoItem()
        self.item.setAspectRatioMode(Qt.KeepAspectRatio)
        self.scene().addItem(self.item)
        self.setBackgroundBrush(QColor("black"))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
        self.setAcceptDrops(False)  # let file drops reach the main window
        self.setMinimumSize(480, 270)
        self._orientation = (0, False, False)
        self.item.nativeSizeChanged.connect(self._native_size)

    @property
    def video_output(self) -> QGraphicsVideoItem:
        return self.item

    def set_orientation(self, rotate: int, hflip: bool, vflip: bool):
        self._orientation = (rotate, hflip, vflip)
        t = QTransform()
        # Qt applies the last operation to points first: flip, then rotate (same order as export).
        t.rotate(rotate)
        t.scale(-1 if hflip else 1, -1 if vflip else 1)
        self.item.setTransform(t)
        self._fit()

    def _native_size(self, size: QSizeF):
        if size.isValid() and not size.isEmpty():
            self.item.setSize(size)
            self._fit()

    def _fit(self):
        rect = self.item.sceneBoundingRect()
        if rect.isEmpty():
            return
        self.scene().setSceneRect(rect)
        self.fitInView(rect, Qt.KeepAspectRatio)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()
