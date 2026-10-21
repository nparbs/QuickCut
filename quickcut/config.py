"""Where QuickCut keeps its settings."""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings


def app_settings() -> QSettings:
    # QUICKCUT_SETTINGS points at an .ini file instead of the registry (used by tests, and handy
    # for a portable install).
    path = os.environ.get("QUICKCUT_SETTINGS")
    return QSettings(path, QSettings.IniFormat) if path else QSettings("QuickCut", "QuickCut")
