"""The video player, drawn as the output frame you're exporting.

The clip sits in a box the shape of the export, placed the way it will be exported (rotation, flips,
zoom and position). Whatever falls outside the box is dimmed: that's what gets cropped off. Drag the
picture to move it, scroll to zoom, double-click to go back to the Fill setting. Text and picture
layers are drawn on top and can be dragged and resized in place.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QFrame, QGraphicsBlurEffect, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsView,
)

from .framing import MAX_ZOOM, MIN_ZOOM, clamp_pan, clamp_zoom, clip_zoom, placement
from .models import Layer
from .overlays import layer_rect, paint_layer

LAYER_PREVIEW_MAX = 1600     # layers are drawn at most this many pixels across in the player
VIEW_MARGIN = 0.06           # space around the frame, as a fraction of its long side
MAX_OVERFLOW = 0.25         # how far past the frame the cropped-off picture is still shown
WHEEL_STEP = 1.1
HANDLE_PX = 9                # resize handle size on screen
SETTLE_MS = 350              # after the last scroll step, the edit counts as finished


def _cosmetic(color: QColor, width: float = 1.0, style=Qt.SolidLine) -> QPen:
    pen = QPen(color, width, style)
    pen.setCosmetic(True)
    return pen


class PlayerView(QGraphicsView):
    framingEdited = Signal(float, float, float, bool)        # zoom, pan_x, pan_y, finished
    framingResetRequested = Signal()
    layerSelected = Signal(int)                              # -1 = none
    layerEdited = Signal(int, float, float, float, bool)     # index, x, y, size, finished

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#141414"))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
        self.setAcceptDrops(False)  # let file drops reach the main window
        self.setMinimumSize(480, 270)
        self.setMouseTracking(True)

        sc = self.scene()
        self.backdrop = QGraphicsRectItem()
        self.backdrop.setBrush(QColor("black"))
        self.backdrop.setPen(Qt.NoPen)
        self.backdrop.setZValue(-3)
        sc.addItem(self.backdrop)
        self.blur_item = QGraphicsPixmapItem()
        self.blur_item.setTransformationMode(Qt.SmoothTransformation)
        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(18)
        self.blur_item.setGraphicsEffect(blur)
        self.blur_item.hide()
        # Inside the backdrop, which clips it: the blur only ever shows within the frame.
        self.backdrop.setFlag(QGraphicsRectItem.ItemClipsChildrenToShape)
        self.blur_item.setParentItem(self.backdrop)
        self.item = QGraphicsVideoItem()
        self.item.setAspectRatioMode(Qt.KeepAspectRatio)
        sc.addItem(self.item)
        self.shade = QGraphicsPathItem()
        self.shade.setBrush(QColor(0, 0, 0, 165))
        self.shade.setPen(Qt.NoPen)
        self.shade.setZValue(5)
        sc.addItem(self.shade)
        self.border = QGraphicsRectItem()
        self.border.setPen(_cosmetic(QColor(255, 255, 255, 200)))
        self.border.setZValue(6)
        sc.addItem(self.border)
        self.thirds = QGraphicsPathItem()
        self.thirds.setPen(_cosmetic(QColor(255, 255, 255, 90), 1, Qt.DashLine))
        self.thirds.setZValue(6)
        self.thirds.hide()
        sc.addItem(self.thirds)
        self.sel_box = QGraphicsRectItem()
        self.sel_box.setPen(_cosmetic(QColor("#4aa3ff"), 1.5, Qt.DashLine))
        self.sel_box.setZValue(7)
        self.sel_box.hide()
        sc.addItem(self.sel_box)
        self.handle = QGraphicsRectItem()
        self.handle.setBrush(QColor("#4aa3ff"))
        self.handle.setPen(_cosmetic(QColor("white")))
        self.handle.setZValue(8)
        self.handle.hide()
        sc.addItem(self.handle)

        self._native = QSizeF()
        self._orientation = (0, False, False)
        self._frame: tuple[int, int] | None = None   # output frame; None = plain player
        self._clip_size = (16, 9)
        self._fit = "pad"
        self._zoom: float | None = None
        self._pan = (0.5, 0.5)
        self._editable = False
        self._layers: list[Layer] = []
        self._layer_items: list[QGraphicsPixmapItem] = []
        self._layer_active: list[bool] = []
        self._selected = -1
        self._drag: dict | None = None
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SETTLE_MS)
        self._settle.timeout.connect(self._finish_wheel)
        self._wheel_target: str | None = None
        self.item.nativeSizeChanged.connect(self._native_size)

    # -- public API -----------------------------------------------------------------------------

    @property
    def video_output(self) -> QGraphicsVideoItem:
        return self.item

    def set_orientation(self, rotate: int, hflip: bool, vflip: bool):
        self._orientation = (rotate, hflip, vflip)
        self._layout()

    def set_frame(self, frame: tuple[int, int] | None, clip_size: tuple[int, int] = (16, 9),
                  fit: str = "pad", editable: bool = True):
        """frame: the export's width/height, or None to show the clip on its own (no framing)."""
        changed = (frame, clip_size, fit) != (self._frame, self._clip_size, self._fit)
        self._frame, self._clip_size, self._fit = frame, clip_size, fit
        self._editable = editable and frame is not None
        if changed:
            self._render_layers()
        self._layout(refit=changed)

    def set_framing(self, zoom: float | None, pan_x: float, pan_y: float):
        if self._drag is None:
            self._zoom, self._pan = zoom, (pan_x, pan_y)
            self._layout()

    def set_background(self, image: QImage | None):
        """A still of the clip, blurred behind it when Fill is 'Blurred background'."""
        if image is None or image.isNull():
            self.blur_item.setPixmap(QPixmap())
        else:
            self.blur_item.setPixmap(QPixmap.fromImage(image))
        self._layout()

    def set_layers(self, layers: list[Layer], selected: int = -1):
        self._layers = layers
        self._selected = selected
        self._render_layers()

    def set_layer_active(self, active: list[bool]):
        """Which layers show at the current playhead; others are drawn faintly when selected."""
        if active != self._layer_active:
            self._layer_active = active
            self._apply_layer_opacity()

    def resolved_zoom(self) -> float:
        cw, ch = self._clip_size
        fw, fh = self._frame or self._clip_size
        return clip_zoom(self._zoom, self._fit, cw, ch, fw, fh)

    # -- geometry -------------------------------------------------------------------------------

    def _native_size(self, size: QSizeF):
        if size.isValid() and not size.isEmpty():
            self._native = size
            self.item.setSize(size)
            self._layout(refit=True)

    def _orient(self) -> QTransform:
        rotate, hflip, vflip = self._orientation
        t = QTransform()
        # Qt applies the last operation to points first: flip, then rotate (same order as export).
        t.rotate(rotate)
        t.scale(-1 if hflip else 1, -1 if vflip else 1)
        return t

    def _frame_rect(self) -> QRectF:
        fw, fh = self._frame or self._clip_size
        return QRectF(0, 0, fw, fh)

    def _placement(self):
        cw, ch = self._clip_size
        fw, fh = self._frame or self._clip_size
        zoom = self.resolved_zoom() if self._frame else 1.0
        pan = self._pan if self._frame else (0.5, 0.5)
        return placement(cw, ch, fw, fh, zoom, *pan)

    def _layout(self, refit: bool = False):
        framed = self._frame is not None
        frame = self._frame_rect()
        p = self._placement()
        if not self._native.isEmpty():
            o = self._orient()
            br = o.mapRect(QRectF(QPointF(0, 0), self._native))
            t = (o * QTransform.fromTranslate(-br.left(), -br.top())
                 * QTransform.fromScale(p.width / br.width(), p.height / br.height())
                 * QTransform.fromTranslate(p.left, p.top))
            self.item.setTransform(t)
        self.backdrop.setRect(frame)
        pix = self.blur_item.pixmap()
        show_blur = framed and self._fit == "blur" and not pix.isNull()
        self.blur_item.setVisible(show_blur)
        if show_blur:
            k = max(frame.width() / pix.width(), frame.height() / pix.height())
            self.blur_item.setScale(k)
            self.blur_item.setPos(frame.center().x() - pix.width() * k / 2,
                                  frame.center().y() - pix.height() * k / 2)
        big = frame.adjusted(-frame.width() * 20, -frame.height() * 20,
                             frame.width() * 20, frame.height() * 20)
        shade = QPainterPath()
        shade.setFillRule(Qt.OddEvenFill)
        shade.addRect(big)
        shade.addRect(frame)
        self.shade.setPath(shade)
        self.shade.setVisible(framed)
        self.border.setRect(frame)
        self.border.setVisible(framed)
        thirds = QPainterPath()
        for k in (1, 2):
            x, y = frame.width() * k / 3, frame.height() * k / 3
            thirds.moveTo(x, 0)
            thirds.lineTo(x, frame.height())
            thirds.moveTo(0, y)
            thirds.lineTo(frame.width(), y)
        self.thirds.setPath(thirds)
        self._place_selection()
        for item in self._layer_items:
            item.setVisible(framed)
        if self._drag is None and (refit or not self._settle.isActive()):
            self._fit_view()

    def _fit_view(self):
        frame = self._frame_rect()
        if frame.isEmpty():
            return
        if self._frame is None:
            view = frame
        else:
            p = self._placement()
            pic = QRectF(p.left, p.top, p.width, p.height)
            m = max(frame.width(), frame.height())
            limit = frame.adjusted(-m * MAX_OVERFLOW, -m * MAX_OVERFLOW, m * MAX_OVERFLOW, m * MAX_OVERFLOW)
            margin = m * VIEW_MARGIN
            view = frame.adjusted(-margin, -margin, margin, margin).united(pic.intersected(limit))
        self.scene().setSceneRect(view)
        self.fitInView(view, Qt.KeepAspectRatio)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_view()
        self._place_selection()

    # -- layers ---------------------------------------------------------------------------------

    def _render_layers(self):
        for item in self._layer_items:
            self.scene().removeItem(item)
        self._layer_items = []
        for i in range(len(self._layers)):
            item = QGraphicsPixmapItem()
            item.setTransformationMode(Qt.SmoothTransformation)
            item.setZValue(1 + i * 0.001)
            self.scene().addItem(item)
            self._layer_items.append(item)
            self._render_layer(i)
        self._apply_layer_opacity()
        self._place_selection()

    def _render_layer(self, i: int):
        item = self._layer_items[i]
        layer = self._layers[i]
        fw, fh = self._frame or self._clip_size
        rect = layer_rect(layer, fw, fh)
        if layer.hidden or layer.empty or rect.isEmpty() or self._frame is None:
            item.setPixmap(QPixmap())
            return
        k = min(1.0, LAYER_PREVIEW_MAX / max(fw, fh))
        pad = 2
        img = QImage(max(1, int(rect.width() * k) + 2 * pad), max(1, int(rect.height() * k) + 2 * pad),
                     QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.translate(pad, pad)
        p.scale(k, k)
        p.translate(-rect.left(), -rect.top())
        paint_layer(p, layer, fw, fh)
        p.end()
        item.setPixmap(QPixmap.fromImage(img))
        item.setScale(1 / k)
        item.setPos(rect.left() - pad / k, rect.top() - pad / k)

    def _apply_layer_opacity(self):
        for i, item in enumerate(self._layer_items):
            active = self._layer_active[i] if i < len(self._layer_active) else True
            item.setOpacity(1.0 if active else (0.35 if i == self._selected else 0.0))

    def _layer_rect(self, i: int) -> QRectF:
        fw, fh = self._frame or self._clip_size
        return layer_rect(self._layers[i], fw, fh)

    def _place_selection(self):
        i = self._selected
        show = self._editable and 0 <= i < len(self._layers) and not self._layers[i].hidden
        rect = self._layer_rect(i) if show else QRectF()
        show = show and not rect.isEmpty()
        self.sel_box.setVisible(show)
        self.handle.setVisible(show)
        if not show:
            return
        self.sel_box.setRect(rect)
        s = HANDLE_PX / max(self.transform().m11(), 1e-6)
        self.handle.setRect(QRectF(rect.right() - s / 2, rect.bottom() - s / 2, s, s))

    def _layer_at(self, pos: QPointF) -> int:
        for i in range(len(self._layers) - 1, -1, -1):
            item = self._layer_items[i] if i < len(self._layer_items) else None
            if item is None or item.opacity() <= 0 or self._layers[i].hidden:
                continue
            if self._layer_rect(i).contains(pos):
                return i
        return -1

    # -- mouse ----------------------------------------------------------------------------------

    def _frame_size(self) -> tuple[float, float]:
        fw, fh = self._frame or self._clip_size
        return float(fw), float(fh)

    def mousePressEvent(self, e):
        if not self._editable or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        pos = self.mapToScene(e.position().toPoint())
        fw, fh = self._frame_size()
        if self.handle.isVisible() and self.handle.sceneBoundingRect().adjusted(-4, -4, 4, 4).contains(pos):
            layer = self._layers[self._selected]
            rect = self._layer_rect(self._selected)
            self._drag = {"kind": "resize", "i": self._selected, "start": pos, "size": layer.size,
                          "centre": rect.center(), "dist": max(4.0, (pos - rect.center()).manhattanLength())}
            return
        hit = self._layer_at(pos)
        if hit >= 0:
            if hit != self._selected:
                self._selected = hit
                self._apply_layer_opacity()
                self._place_selection()
                self.layerSelected.emit(hit)
            layer = self._layers[hit]
            self._drag = {"kind": "layer", "i": hit, "start": pos, "x": layer.x, "y": layer.y}
            self.viewport().setCursor(Qt.SizeAllCursor)
            return
        if self._selected >= 0:
            self._selected = -1
            self._apply_layer_opacity()
            self._place_selection()
            self.layerSelected.emit(-1)
        p = self._placement()
        self._drag = {"kind": "pan", "start": pos, "cx": p.left + p.width / 2, "cy": p.top + p.height / 2,
                      "zoom": self.resolved_zoom()}
        self.thirds.show()
        self.viewport().setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        pos = self.mapToScene(e.position().toPoint())
        d = self._drag
        if d is None:
            if self._editable:
                over_handle = self.handle.isVisible() and \
                    self.handle.sceneBoundingRect().adjusted(-4, -4, 4, 4).contains(pos)
                cursor = Qt.SizeFDiagCursor if over_handle else \
                    (Qt.SizeAllCursor if self._layer_at(pos) >= 0 else Qt.OpenHandCursor)
                self.viewport().setCursor(cursor)
            return super().mouseMoveEvent(e)
        fw, fh = self._frame_size()
        delta = pos - d["start"]
        if d["kind"] == "pan":
            cw, ch = self._clip_size
            self._zoom = d["zoom"]
            self._pan = clamp_pan(cw, ch, fw, fh, d["zoom"], (d["cx"] + delta.x()) / fw,
                                  (d["cy"] + delta.y()) / fh)
            self._layout()
            self.framingEdited.emit(self._zoom, *self._pan, False)
        elif d["kind"] == "layer":
            layer = self._layers[d["i"]]
            layer.x = min(1.0, max(0.0, d["x"] + delta.x() / fw))
            layer.y = min(1.0, max(0.0, d["y"] + delta.y() / fh))
            self._render_layer(d["i"])
            self._place_selection()
            self.layerEdited.emit(d["i"], layer.x, layer.y, layer.size, False)
        elif d["kind"] == "resize":
            layer = self._layers[d["i"]]
            dist = max(1.0, (pos - d["centre"]).manhattanLength())
            layer.size = min(100.0, max(1.0, d["size"] * dist / d["dist"]))
            self._render_layer(d["i"])
            self._place_selection()
            self.layerEdited.emit(d["i"], layer.x, layer.y, layer.size, False)

    def mouseReleaseEvent(self, e):
        d = self._drag
        if d is None:
            return super().mouseReleaseEvent(e)
        self._drag = None
        self.thirds.hide()
        self.viewport().setCursor(Qt.OpenHandCursor)
        if d["kind"] == "pan":
            if (self.mapToScene(e.position().toPoint()) - d["start"]).manhattanLength() > 0:
                self.framingEdited.emit(self._zoom, *self._pan, True)
            self._fit_view()
        else:
            layer = self._layers[d["i"]]
            self.layerEdited.emit(d["i"], layer.x, layer.y, layer.size, True)

    def mouseDoubleClickEvent(self, e):
        if not self._editable:
            return super().mouseDoubleClickEvent(e)
        if self._layer_at(self.mapToScene(e.position().toPoint())) < 0:
            self.framingResetRequested.emit()

    def wheelEvent(self, e):
        if not self._editable or self._drag is not None:
            return super().wheelEvent(e)
        steps = e.angleDelta().y() / 120
        if not steps:
            return
        pos = self.mapToScene(e.position().toPoint())
        factor = WHEEL_STEP ** steps
        sel = self._selected
        if 0 <= sel < len(self._layers) and self._layer_rect(sel).contains(pos):
            layer = self._layers[sel]
            layer.size = min(100.0, max(1.0, layer.size * factor))
            self._render_layer(sel)
            self._place_selection()
            self._wheel_target = "layer"
            self.layerEdited.emit(sel, layer.x, layer.y, layer.size, False)
        else:
            fw, fh = self._frame_size()
            cw, ch = self._clip_size
            old = self._placement()
            zoom = clamp_zoom(self.resolved_zoom() * factor)
            if not MIN_ZOOM <= zoom <= MAX_ZOOM:
                return
            k = zoom / self.resolved_zoom()
            # Keep the point under the cursor where it is.
            cx = pos.x() + (old.left + old.width / 2 - pos.x()) * k
            cy = pos.y() + (old.top + old.height / 2 - pos.y()) * k
            self._zoom = zoom
            self._pan = clamp_pan(cw, ch, fw, fh, zoom, cx / fw, cy / fh)
            self._layout()
            self._wheel_target = "framing"
            self.framingEdited.emit(self._zoom, *self._pan, False)
        self._settle.start()

    def _finish_wheel(self):
        if self._wheel_target == "framing":
            self.framingEdited.emit(self.resolved_zoom(), *self._pan, True)
        elif self._wheel_target == "layer" and 0 <= self._selected < len(self._layers):
            layer = self._layers[self._selected]
            self.layerEdited.emit(self._selected, layer.x, layer.y, layer.size, True)
        self._wheel_target = None
        self._fit_view()
