"""Small reusable widgets for the settings panel."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

LABEL_WIDTH = 92


def label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFixedWidth(LABEL_WIDTH)  # same column width everywhere, so fields line up
    return lbl


class Section(QWidget):
    """A titled block whose body folds away when you click the header."""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self.header = QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.header.setAutoRaise(True)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.setStyleSheet("QToolButton { font-weight: bold; border: none; padding: 2px 0; }")
        self.body = QFrame()
        self.body.setObjectName("sectionBody")
        self.body.setStyleSheet("#sectionBody { border: 1px solid palette(mid); border-radius: 4px; }")
        self.form = QFormLayout(self.body)
        self.form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.form.setHorizontalSpacing(10)
        self.form.setVerticalSpacing(8)
        self.form.setContentsMargins(10, 10, 10, 10)
        self.body.setVisible(expanded)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.header)
        layout.addWidget(self.body)
        self.header.toggled.connect(self._toggle)

    def _toggle(self, on: bool):
        self.header.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.body.setVisible(on)

    def add(self, text: str | None, widget: QWidget):
        self.form.addRow(label(text or ""), widget)

    def set_row_visible(self, widget: QWidget, visible: bool):
        self.form.setRowVisible(widget, visible)


class FilePicker(QWidget):
    changed = Signal(str)

    def __init__(self, caption: str, file_filter: str, parent=None):
        super().__init__(parent)
        self.caption, self.file_filter = caption, file_filter
        self._path = ""
        self.edit = QLineEdit()
        self.edit.setReadOnly(True)
        self.edit.setPlaceholderText("None")
        browse = QPushButton("Choose…")
        self.clear_btn = QToolButton()
        self.clear_btn.setText("✕")
        self.clear_btn.setToolTip("Remove")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.edit, 1)
        layout.addWidget(browse)
        layout.addWidget(self.clear_btn)
        browse.clicked.connect(self._browse)
        self.clear_btn.clicked.connect(lambda: self.set_path(""))
        self._sync()

    def _browse(self):
        start = str(Path(self._path).parent) if self._path else ""
        path, _ = QFileDialog.getOpenFileName(self, self.caption, start, self.file_filter)
        if path:
            self.set_path(path)

    def path(self) -> str:
        return self._path

    def set_path(self, path: str):
        path = path or ""
        if path == self._path:
            return
        self._path = path
        self._sync()
        self.changed.emit(path)

    def _sync(self):
        self.edit.setText(Path(self._path).name if self._path else "")
        self.edit.setToolTip(self._path)
        self.clear_btn.setEnabled(bool(self._path))


class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, color: str = "#ffffff", parent=None):
        super().__init__(parent)
        self._color = ""
        self.clicked.connect(self._pick)
        self.set_color(color)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str):
        c = QColor(color)
        if not c.isValid() or c.name() == self._color:
            return
        self._color = c.name()
        text = "#000000" if c.lightness() > 140 else "#ffffff"
        self.setText(self._color.upper())
        self.setStyleSheet(f"background: {self._color}; color: {text}; border: 1px solid palette(mid);"
                           "border-radius: 3px; padding: 4px;")
        self.changed.emit(self._color)

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Text colour")
        if c.isValid():
            self.set_color(c.name())
