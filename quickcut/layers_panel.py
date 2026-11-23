"""The Layers tab: text and pictures laid over the whole edit.

Top of the list is in front. Drag to restack, untick to hide, select one to edit it here or drag it
around in the player.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFontComboBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSlider,
    QToolButton, QVBoxLayout, QWidget,
)

from .models import Layer
from .widgets import ColorButton, FilePicker

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
PLACES = [("Move to…", None), ("Top", (0.5, 0.1)), ("Centre", (0.5, 0.5)), ("Bottom", (0.5, 0.88)),
          ("Top left", (0.18, 0.1)), ("Top right", (0.82, 0.1)),
          ("Bottom left", (0.18, 0.88)), ("Bottom right", (0.82, 0.88))]
LAYER_ROLE = Qt.UserRole


def _spin(lo, hi, value, step, decimals, suffix) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(value)
    s.setSuffix(suffix)
    return s


class LayersPanel(QWidget):
    changed = Signal(str)            # undo key: something about the layers changed
    selectionChanged = Signal(int)   # index into the layers list, -1 = none

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layers: list[Layer] = []
        self._selected = -1
        self._filling = False
        self.playhead = lambda: 0.0      # set by the window: the playhead's time in the edit

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Text and pictures over the whole video. Top of the list is in front. "
                      "Drag them around in the player; scroll over one to resize it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(2)
        self.add_text_btn = QPushButton("+ Text")
        self.add_text_btn.setToolTip("Add a caption or title")
        self.add_text_btn.clicked.connect(self.add_text)
        self.add_image_btn = QPushButton("+ Picture")
        self.add_image_btn.setToolTip("Add a logo, watermark or sticker (PNG with transparency works best)")
        self.add_image_btn.clicked.connect(self.add_image)
        row.addWidget(self.add_text_btn)
        row.addWidget(self.add_image_btn)
        row.addStretch()
        self.dup_btn = self._tool("⧉", "Duplicate layer", self.duplicate)
        self.up_btn = self._tool("▲", "Bring forward", lambda: self.move(1))
        self.down_btn = self._tool("▼", "Send backward", lambda: self.move(-1))
        self.del_btn = self._tool("✕", "Delete layer (Delete)", self.delete)
        for b in (self.dup_btn, self.up_btn, self.down_btn, self.del_btn):
            row.addWidget(b)
        lay.addLayout(row)

        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.currentRowChanged.connect(self._row_changed)
        self.list.itemChanged.connect(self._item_changed)
        self.list.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._rows_moved))
        lay.addWidget(self.list, 1)

        self.editor = QFrame()
        self.editor.setObjectName("layerEditor")
        self.editor.setStyleSheet("#layerEditor { border: 1px solid palette(mid); border-radius: 4px; }")
        form = self.form = QFormLayout(self.editor)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setContentsMargins(8, 8, 8, 8)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Your text")
        self.text.setFixedHeight(self.text.fontMetrics().lineSpacing() * 3 + 12)
        self.image = FilePicker("Choose picture", IMAGE_FILTER)
        self.font_box = QFontComboBox()
        self.bold = QCheckBox("Bold")
        self.box = QCheckBox("Dark box behind")
        self.color = ColorButton("#ffffff")
        self.size = _spin(1, 100, 6, 0.5, 1, " %")
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(5, 100)
        self.place = QComboBox()
        for text, data in PLACES:
            self.place.addItem(text, data)
        self.start = _spin(0, 36000, 0, 0.5, 2, " s")
        self.duration = _spin(0, 36000, 0, 0.5, 2, " s")
        self.duration.setSpecialValueText("Until the end")
        self.from_playhead = QToolButton()
        self.from_playhead.setText("⏱ Playhead")
        self.from_playhead.setToolTip("Start this layer where the playhead is in the edit")
        self.from_playhead.clicked.connect(self._start_at_playhead)
        start_row = QWidget()
        sr = QHBoxLayout(start_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.addWidget(self.start, 1)
        sr.addWidget(self.from_playhead)
        style_row = QWidget()
        st = QHBoxLayout(style_row)
        st.setContentsMargins(0, 0, 0, 0)
        st.addWidget(self.bold)
        st.addWidget(self.box)
        st.addStretch()
        self.style_row = style_row
        form.addRow("Text", self.text)
        form.addRow("Picture", self.image)
        form.addRow("Font", self.font_box)
        form.addRow("", style_row)
        form.addRow("Colour", self.color)
        form.addRow("Size", self.size)
        form.addRow("Opacity", self.opacity)
        form.addRow("Position", self.place)
        form.addRow("Show from", start_row)
        form.addRow("for", self.duration)
        lay.addWidget(self.editor)

        self.text.textChanged.connect(lambda: self._edit("text", self.text.toPlainText(), "layer-text"))
        self.image.changed.connect(lambda p: self._edit("image", p))
        self.font_box.currentFontChanged.connect(lambda f: self._edit("font", f.family()))
        self.bold.toggled.connect(lambda on: self._edit("bold", on))
        self.box.toggled.connect(lambda on: self._edit("box", on))
        self.color.changed.connect(lambda c: self._edit("color", c))
        self.size.valueChanged.connect(lambda v: self._edit("size", v, "layer-size"))
        self.opacity.valueChanged.connect(lambda v: self._edit("opacity", v / 100, "layer-opacity"))
        self.start.valueChanged.connect(lambda v: self._edit("start", v, "layer-time"))
        self.duration.valueChanged.connect(lambda v: self._edit("duration", v, "layer-time"))
        self.place.activated.connect(self._place)
        self._sync_editor()

    def _tool(self, text, tip, slot) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.clicked.connect(slot)
        return b

    # -- model ----------------------------------------------------------------------------------

    @property
    def selected(self) -> int:
        return self._selected

    def current(self) -> Layer | None:
        return self.layers[self._selected] if 0 <= self._selected < len(self.layers) else None

    def set_layers(self, layers: list[Layer], selected: int = -1):
        self.layers = layers
        self._selected = selected if 0 <= selected < len(layers) else -1
        self._fill()

    def select(self, index: int):
        index = index if 0 <= index < len(self.layers) else -1
        if index == self._selected:
            return
        self._selected = index
        self._filling = True
        self.list.setCurrentRow(self._row_for(index) if index >= 0 else -1)
        self._filling = False
        self._sync_editor()

    def _row_for(self, index: int) -> int:
        return len(self.layers) - 1 - index      # the list shows the front layer first

    def _fill(self):
        self._filling = True
        self.list.clear()
        for i in range(len(self.layers) - 1, -1, -1):
            layer = self.layers[i]
            item = QListWidgetItem(layer.label)
            item.setData(LAYER_ROLE, i)
            item.setFlags((item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled) & ~Qt.ItemIsDropEnabled)
            item.setCheckState(Qt.Unchecked if layer.hidden else Qt.Checked)
            item.setToolTip(layer.image or layer.text)
            self.list.addItem(item)
        self.list.setCurrentRow(self._row_for(self._selected) if self._selected >= 0 else -1)
        self._filling = False
        self._sync_editor()

    def refresh_labels(self):
        self._filling = True
        for row in range(self.list.count()):
            item = self.list.item(row)
            i = item.data(LAYER_ROLE)
            if 0 <= i < len(self.layers):
                item.setText(self.layers[i].label)
        self._filling = False

    def _sync_editor(self):
        layer = self.current()
        self.editor.setEnabled(layer is not None)
        for b in (self.dup_btn, self.del_btn):
            b.setEnabled(layer is not None)
        self.up_btn.setEnabled(layer is not None and self._selected < len(self.layers) - 1)
        self.down_btn.setEnabled(layer is not None and self._selected > 0)
        is_text = layer is None or layer.kind == "text"
        for w in (self.text, self.font_box, self.style_row, self.color):
            self.form.setRowVisible(w, is_text)
        self.form.setRowVisible(self.image, not is_text)
        self.size.setToolTip("Text height as a percentage of the video's short side" if is_text else
                             "Picture width as a percentage of the video width")
        if layer is None:
            return
        widgets = (self.text, self.image, self.font_box, self.bold, self.box, self.color, self.size,
                   self.opacity, self.start, self.duration)
        for w in widgets:
            w.blockSignals(True)
        if self.text.toPlainText() != layer.text:
            self.text.setPlainText(layer.text)
        self.image.set_path(layer.image)
        self.font_box.setCurrentFont(QFont(layer.font))
        self.bold.setChecked(layer.bold)
        self.box.setChecked(layer.box)
        self.color.set_color(layer.color)
        self.size.setValue(layer.size)
        self.opacity.setValue(round(layer.opacity * 100))
        self.start.setValue(layer.start)
        self.duration.setValue(layer.duration)
        for w in widgets:
            w.blockSignals(False)

    def sync_from_model(self):
        """After the layers were edited elsewhere (dragged in the player, undo)."""
        self._sync_editor()
        self.refresh_labels()

    # -- edits ----------------------------------------------------------------------------------

    def _edit(self, field: str, value, key: str = "layer"):
        layer = self.current()
        if layer is None or getattr(layer, field) == value:
            return
        setattr(layer, field, value)
        if field in ("text", "image"):
            self.refresh_labels()
        self.changed.emit(key)

    def _place(self, index: int):
        spot = self.place.itemData(index)
        self.place.setCurrentIndex(0)
        layer = self.current()
        if layer is None or spot is None:
            return
        layer.x, layer.y = spot
        self.changed.emit("layer")

    def _start_at_playhead(self):
        self.start.setValue(max(0.0, self.playhead()))

    def _add(self, layer: Layer):
        self.layers.append(layer)
        self._selected = len(self.layers) - 1
        self._fill()
        self.selectionChanged.emit(self._selected)
        self.changed.emit("layer-add")

    def add_text(self):
        self._add(Layer("text", text="Your text"))
        self.text.setFocus()
        self.text.selectAll()

    def add_image(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Choose picture", "", IMAGE_FILTER)
            if not path:
                return
        self._add(Layer("image", image=path, x=0.82, y=0.12, size=18.0, box=False))

    def duplicate(self):
        layer = self.current()
        if layer is None:
            return
        copy = replace(layer, x=min(0.95, layer.x + 0.04), y=min(0.95, layer.y + 0.04))
        self.layers.insert(self._selected + 1, copy)
        self._selected += 1
        self._fill()
        self.selectionChanged.emit(self._selected)
        self.changed.emit("layer-add")

    def delete(self):
        if self.current() is None:
            return
        del self.layers[self._selected]
        self._selected = min(self._selected, len(self.layers) - 1)
        self._fill()
        self.selectionChanged.emit(self._selected)
        self.changed.emit("layer-delete")

    def move(self, delta: int):
        i, j = self._selected, self._selected + delta
        if self.current() is None or not 0 <= j < len(self.layers):
            return
        self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
        self._selected = j
        self._fill()
        self.selectionChanged.emit(j)
        self.changed.emit("layer-order")

    def _row_changed(self, row: int):
        if self._filling:
            return
        item = self.list.item(row) if row >= 0 else None
        self._selected = item.data(LAYER_ROLE) if item else -1
        self._sync_editor()
        self.selectionChanged.emit(self._selected)

    def _item_changed(self, item: QListWidgetItem):
        if self._filling:
            return
        i = item.data(LAYER_ROLE)
        hidden = item.checkState() != Qt.Checked
        if 0 <= i < len(self.layers) and self.layers[i].hidden != hidden:
            self.layers[i].hidden = hidden
            self.changed.emit("layer")

    def _rows_moved(self):
        order = [self.list.item(r).data(LAYER_ROLE) for r in range(self.list.count())][::-1]
        if order == list(range(len(self.layers))):
            return
        selected = self.layers[self._selected] if self.current() else None
        self.layers[:] = [self.layers[i] for i in order]
        self._selected = next((k for k, layer in enumerate(self.layers) if layer is selected), -1)
        self._fill()
        self.selectionChanged.emit(self._selected)
        self.changed.emit("layer-order")
