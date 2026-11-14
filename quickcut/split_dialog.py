"""Ask how to split the videos into parts: start time, length of each part, how many, and the preset."""
from __future__ import annotations

import csv
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QSpinBox, QVBoxLayout,
)

from .command import split_into_parts
from .export_panel import PRESETS, ExportPanel
from .models import Clip

MAX_PARTS = 500
CURRENT = ""    # preset choice meaning "whatever the export panel is set to"
LIST_LENGTHS_UP_TO = 8
PARTS_INDEX = "parts.csv"


def part_time(seconds: float) -> str:
    """A time that's safe in a file name and sorts well: 00m10.0s, or 1h02m05.5s past an hour."""
    seconds = round(max(0.0, seconds), 1)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{int(minutes):02d}m{secs:04.1f}s"
    return f"{int(hours)}h{text}" if hours else text


def part_filename(stem: str, index: int, count: int, part: Clip, ext: str) -> str:
    """e.g. clip_01_00m10.0s-00m17.0s.mp4: the order, then where the part is in the source video."""
    digits = max(2, len(str(count)))
    return f"{stem}_{index:0{digits}d}_{part_time(part.start)}-{part_time(part.end)}{ext}"


def write_parts_index(path: str, stem: str, parts: list[Clip], ext: str):
    """A CSV listing every part in order, with its times in the source video."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order", "file", "start_seconds", "end_seconds", "length_seconds"])
        for i, p in enumerate(parts, 1):
            w.writerow([i, part_filename(stem, i, len(parts), p, ext), f"{p.start:.3f}", f"{p.end:.3f}",
                        f"{p.out_length:.3f}"])


def describe_settings(panel: ExportPanel) -> str:
    """One line on how each part will be compressed with this panel's settings."""
    name = panel.format.currentText()
    if panel.kind == "audio":
        return f"{name}, {panel.audio_bitrate.currentText()}"
    if panel.kind != "video":
        return f"{name}, {panel.preset.currentText().lower()}"
    mode = panel.mode.currentData()
    if mode == "copy":
        return (f"{name}, no re-encode. Cuts snap to keyframes, so parts can come out longer or "
                "shorter than asked and may overlap.")
    if mode == "size":
        return f"{name}, each part compressed to fit {panel.size_mb.value():g} MB"
    if mode == "manual":
        if panel.rate.currentData() == "bitrate":
            return f"{name}, {panel.bitrate.value():,} kbps"
        return f"{name}, CRF/CQ {panel.quality.value()}"
    return f"{name}, {dict((d, t) for t, d in PRESETS)[panel.preset.currentData()].lower()}"


def _lengths(parts: list[Clip]) -> str:
    return ", ".join(f"{p.out_length:.1f} s" for p in parts)


class SplitDialog(QDialog):
    def __init__(self, clips: list[Clip], panel: ExportPanel,
                 panel_for: Callable[[str], ExportPanel], seconds: float = 5.0,
                 preset: str = CURRENT, start: float = 0.0, max_parts: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Split into parts")
        self._clips = clips
        self._panel_for = panel_for

        self.start = QDoubleSpinBox()
        self.start.setRange(0, 36000)
        self.start.setDecimals(1)
        self.start.setSingleStep(1)
        self.start.setSuffix(" s")
        self.start.setValue(start)
        self.start.setToolTip("Skip this much of each clip before the first part. Counted from the "
                              "clip's trimmed start.")

        self.seconds = QDoubleSpinBox()
        self.seconds.setRange(0.5, 36000)
        self.seconds.setDecimals(1)
        self.seconds.setSuffix(" s")
        self.seconds.setValue(seconds)
        self.seconds.setToolTip("How long each part plays. The last part is whatever is left over.")

        self.max_parts = QSpinBox()
        self.max_parts.setRange(0, MAX_PARTS)
        self.max_parts.setSpecialValueText("No limit")
        self.max_parts.setValue(max_parts)
        self.max_parts.setToolTip("Stop after this many parts from each clip. The rest of the clip "
                                  "is left out.")

        self.preset = QComboBox()
        self.preset.addItem("Current export settings", CURRENT)
        builtin, saved = panel.preset_names()
        self.preset.insertSeparator(self.preset.count())
        for name in builtin:
            self.preset.addItem(name, name)
        if saved:
            self.preset.insertSeparator(self.preset.count())
            for name in saved:
                self.preset.addItem(name, name)
        self.preset.setCurrentIndex(max(self.preset.findData(preset), 0))
        self.preset.setToolTip("Compression, quality and size limit for the parts. This choice is "
                               "remembered for splitting only and doesn't change the export panel. "
                               "Save your own presets with Save… at the top of the export panel.")

        self.settings_label = QLabel()
        self.settings_label.setWordWrap(True)
        self.settings_label.setStyleSheet("color: palette(placeholder-text);")
        self.count_label = QLabel()
        self.count_label.setWordWrap(True)
        self.count_label.setTextFormat(Qt.RichText)

        form = QFormLayout()
        form.addRow("Start at", self.start)
        form.addRow("Length of each part", self.seconds)
        form.addRow("Most parts", self.max_parts)
        form.addRow("Preset", self.preset)
        form.addRow("", self.settings_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Choose folder…")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.count_label)
        layout.addWidget(self.buttons)
        self.setMinimumWidth(440)

        for w in (self.start, self.seconds, self.max_parts):
            w.valueChanged.connect(self._update_count)
        self.preset.currentIndexChanged.connect(self._update_settings)
        self._update_settings()
        self._update_count()

    def part_seconds(self) -> float:
        return self.seconds.value()

    def start_seconds(self) -> float:
        return self.start.value()

    def part_limit(self) -> int:
        return self.max_parts.value()

    def preset_name(self) -> str:
        return self.preset.currentData() or CURRENT

    def parts(self, clip: Clip) -> list[Clip]:
        return split_into_parts(clip, self.part_seconds(), self.start_seconds(), self.part_limit())

    def part_count(self) -> int:
        return sum(len(self.parts(c)) for c in self._clips)

    def _update_settings(self):
        self.settings_label.setText(describe_settings(self._panel_for(self.preset_name())))

    def _update_count(self):
        total = self.part_count()
        warn = "<span style='color:#e8a33d'>⚠ {}</span>"
        if total == 0:
            text = warn.format("The start time is past the end of every clip.")
        elif total > MAX_PARTS:
            text = warn.format(f"That would make {total:,} files; the most is {MAX_PARTS}. "
                               "Use longer parts or set Most parts.")
        else:
            text = f"<b>{total} file{'s' if total != 1 else ''}</b>"
            if len(self._clips) == 1:
                parts = self.parts(self._clips[0])
                if total <= LIST_LENGTHS_UP_TO:
                    text += f": {_lengths(parts)}"
                elif abs(parts[-1].out_length - self.part_seconds()) > 0.05:
                    text += f"; the last one is {parts[-1].out_length:.1f} s"
            else:
                skipped = sum(not self.parts(c) for c in self._clips)
                text += f" from {len(self._clips) - skipped} clips (each clip is split on its own)"
                if skipped:
                    text += f"; {skipped} clip{'s are' if skipped != 1 else ' is'} shorter than the start time"
        self.count_label.setText(text)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(0 < total <= MAX_PARTS)
