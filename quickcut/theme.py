"""Dark, light, or follow-Windows colour themes."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

THEMES = [("Follow Windows", "system"), ("Dark", "dark"), ("Light", "light")]
ACCENT = "#c98f12"


def dark_palette() -> QPalette:
    p = QPalette()
    colors = {
        QPalette.Window: "#202124", QPalette.WindowText: "#e8eaed", QPalette.Base: "#2b2c30",
        QPalette.AlternateBase: "#323338", QPalette.ToolTipBase: "#2b2c30",
        QPalette.ToolTipText: "#e8eaed", QPalette.Text: "#e8eaed", QPalette.Button: "#303135",
        QPalette.ButtonText: "#e8eaed", QPalette.BrightText: "#ffffff", QPalette.Highlight: ACCENT,
        QPalette.HighlightedText: "#000000", QPalette.Link: "#f5b82e",
        QPalette.PlaceholderText: "#9aa0a6", QPalette.Mid: "#4a4b50", QPalette.Light: "#3a3b40",
        QPalette.Dark: "#151618",
    }
    for role, color in colors.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor("#6f7378"))
    return p


def light_palette() -> QPalette:
    p = QApplication.style().standardPalette()
    p.setColor(QPalette.Highlight, QColor(ACCENT))
    p.setColor(QPalette.HighlightedText, QColor("#000000"))
    p.setColor(QPalette.Link, QColor("#9a6a00"))
    return p


def system_is_dark() -> bool:
    return QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def apply_theme(app: QApplication, theme: str):
    dark = theme == "dark" or (theme == "system" and system_is_dark())
    app.setPalette(dark_palette() if dark else light_palette())
    app.setProperty("quickcut_dark", dark)
