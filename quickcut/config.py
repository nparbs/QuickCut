"""Where QuickCut keeps its settings."""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings, QStandardPaths


def app_settings() -> QSettings:
    # QUICKCUT_SETTINGS points at an .ini file instead of the registry (used by tests, and handy
    # for a portable install).
    path = os.environ.get("QUICKCUT_SETTINGS")
    return QSettings(path, QSettings.IniFormat) if path else QSettings("QuickCut", "QuickCut")


def autosave_path() -> str:
    # Beside the settings file when there is one, so tests and portable installs stay self-contained.
    path = os.environ.get("QUICKCUT_SETTINGS")
    folder = os.path.dirname(path) if path else os.path.join(
        QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation), "QuickCut")
    return os.path.join(folder, "autosave.qcut")
