"""Explorer's "Convert to MP4": a small window that turns the chosen videos into .mp4 files
next to the originals.

When the streams already fit in MP4 (H.264/HEVC video, AAC/MP3 audio — most phone .mov files)
they're copied as they are, which takes a second or two and loses nothing. Anything else is
re-encoded to H.264 + AAC, on the GPU when one is available.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from .command import ExportPlan, RateControl, encoder_args
from .exporter import ExportRunner
from .ffmpeg import ENCODERS, Encoder, FFmpegError, Tools, detect_encoders, probe
from .models import MediaInfo

COPY_VIDEO = {"h264", "hevc"}
COPY_AUDIO = {"aac", "mp3"}
REENCODE_QUALITY = 20       # x264 CRF; visually very close to the source
AUDIO_KBPS = 192
CLOSE_AFTER_MS = 4000       # the window closes itself this long after everything worked


def output_path(source: str) -> str:
    """name.mp4 next to the source; name (1).mp4 and so on when that's taken."""
    src = Path(source)
    out = src.with_suffix(".mp4")
    n = 1
    while out.exists():
        out = src.with_name(f"{src.stem} ({n}).mp4")
        n += 1
    return str(out)


def describe(info: MediaInfo) -> str:
    """What the conversion will do, shown under the file name."""
    if info.video_codec in COPY_VIDEO and (not info.has_audio or info.audio_codec in COPY_AUDIO):
        return "copying (no quality loss)"
    if info.video_codec in COPY_VIDEO:
        return "copying video, converting audio"
    return "converting to H.264"


def build_convert_plan(ffmpeg: str, info: MediaInfo, output: str, encoder: Encoder | None) -> ExportPlan:
    """One ffmpeg pass: copy what MP4 can hold, re-encode the rest. `encoder` is only used
    (and only needed) when the video has to be re-encoded."""
    copy_video = info.video_codec in COPY_VIDEO
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", "-i", info.path,
           # First video plus every audio track; .mov timecode/data tracks don't fit in MP4.
           "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0"]
    if copy_video:
        cmd += ["-c:v", "copy"]
        if info.video_codec == "hevc":
            cmd += ["-tag:v", "hvc1"]   # what Apple devices and Windows' player expect
    else:
        if encoder is None:
            raise ValueError("re-encoding needs an encoder")
        # Odd sizes break 4:2:0 encoders.
        cmd += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-pix_fmt", "yuv420p"]
        cmd += encoder_args(encoder, RateControl("quality", REENCODE_QUALITY), "balanced")
    if info.has_audio:
        cmd += ["-c:a", "copy"] if info.audio_codec in COPY_AUDIO else ["-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k"]
    cmd += ["-movflags", "+faststart", output]
    fallback = None
    if not copy_video and encoder is not None and encoder.hardware:
        # A GPU encoder can refuse an unusual file; redo it on the CPU.
        fallback = build_convert_plan(ffmpeg, info, output, ENCODERS["libx264"])
    return ExportPlan([cmd], info.duration, info.width, info.height, info.fps, output, None,
                      AUDIO_KBPS, pass_durations=[info.duration], fallback=fallback)


class _Row(QWidget):
    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.name = QLabel(Path(path).name)
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status = QLabel("Waiting…")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: palette(placeholder-text);")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(2)
        lay.addWidget(self.name)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)


class ConvertWindow(QWidget):
    """Converts its files one at a time; more can arrive from later Explorer launches."""

    def __init__(self, tools: Tools):
        super().__init__()
        self.tools = tools
        self.setWindowTitle("QuickCut · Convert to MP4")
        self.setMinimumWidth(420)
        self.rows: list[_Row] = []
        self.pending: list[_Row] = []
        self.failed = 0
        self.done = 0
        self.runner: ExportRunner | None = None
        self._busy = False          # probing a file (events still run, so guard re-entry)
        self._encoder: Encoder | None = None
        self._encoder_checked = False

        self.list_layout = QVBoxLayout()
        self.summary = QLabel("")
        self.open_btn = QPushButton("Show in folder")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._reveal_last)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.summary, 1)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.cancel_btn)
        lay = QVBoxLayout(self)
        lay.addLayout(self.list_layout)
        lay.addStretch(1)
        lay.addLayout(buttons)
        self._close_timer = QTimer(self, singleShot=True, interval=CLOSE_AFTER_MS)
        self._close_timer.timeout.connect(self.close)
        self._last_output = ""

    # -- queue ------------------------------------------------------------------------------------

    def add_files(self, paths: list[str]):
        known = {os.path.normcase(os.path.abspath(r.path)) for r in self.rows}
        for p in paths:
            key = os.path.normcase(os.path.abspath(p))
            if key in known or not os.path.isfile(p):
                continue
            known.add(key)
            row = _Row(p)
            self.rows.append(row)
            self.pending.append(row)
            self.list_layout.addWidget(row)
        self._close_timer.stop()
        self.cancel_btn.setText("Cancel")
        self._update_summary()
        self.adjustSize()
        if self.runner is None:
            QTimer.singleShot(0, self._next)

    def _next(self):
        if self.runner is not None or self._busy:
            return
        if not self.pending:
            self._all_done()
            return
        self._busy = True
        try:
            self._start(self.pending.pop(0))
        finally:
            self._busy = False

    def _start(self, row: _Row):
        row.status.setText("Reading…")
        QApplication.processEvents()
        try:
            info = probe(self.tools, row.path)
        except (FFmpegError, OSError, ValueError) as e:
            self._fail(row, str(e) or "Couldn't read this file.")
            return
        encoder = None
        if info.video_codec not in COPY_VIDEO:
            row.status.setText("Finding the fastest encoder…")
            QApplication.processEvents()
            encoder = self._h264_encoder()
            if encoder is None:
                self._fail(row, "This FFmpeg has no H.264 encoder.")
                return
        output = output_path(row.path)
        plan = build_convert_plan(self.tools.ffmpeg, info, output, encoder)
        note = describe(info)
        if encoder is not None and encoder.hardware:
            note += f" on the {encoder.label.split(' (')[0]}"
        row.status.setText(note[0].upper() + note[1:] + "…")

        runner = ExportRunner(plan, self)
        self.runner = runner
        runner.progress.connect(lambda f, text, row=row, note=note:
                                (row.bar.setValue(int(f * 1000)),
                                 row.status.setText(note[0].upper() + note[1:] + (f" · {text}" if text else ""))))
        runner.finished.connect(lambda ok, msg, row=row: self._finished(row, ok, msg))
        runner.start()

    def _h264_encoder(self) -> Encoder | None:
        if not self._encoder_checked:
            self._encoder_checked = True
            self._encoder = next((e for e in detect_encoders(self.tools) if e.codec == "h264"), None)
        return self._encoder

    def _finished(self, row: _Row, ok: bool, message: str):
        runner, self.runner = self.runner, None
        if runner is not None:
            runner.deleteLater()
        if ok:
            self.done += 1
            self._last_output = message
            row.bar.setValue(1000)
            row.status.setText(f"Saved as {Path(message).name}")
            self.open_btn.setEnabled(True)
        else:
            self._fail(row, message, queue_next=False)
        self._update_summary()
        self._next()

    def _fail(self, row: _Row, message: str, queue_next: bool = True):
        self.failed += 1
        row.bar.setValue(0)
        lines = [ln for ln in message.strip().splitlines() if ln.strip()]
        row.status.setText("Failed: " + (lines[-1] if lines else "unknown error"))
        row.status.setToolTip(message)
        row.status.setStyleSheet("color: #d9534f;")
        self._update_summary()
        if queue_next:
            QTimer.singleShot(0, self._next)

    def _update_summary(self):
        total = len(self.rows)
        self.summary.setText(f"{self.done} of {total} done" + (f", {self.failed} failed" if self.failed else ""))

    def _all_done(self):
        self.cancel_btn.setText("Close")
        if not self.failed:
            self._close_timer.start()

    def _reveal_last(self):
        if not self._last_output:
            return
        import subprocess
        subprocess.Popen(["explorer", "/select,", os.path.normpath(self._last_output)])

    # -- window -----------------------------------------------------------------------------------

    def bring_to_front(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def enterEvent(self, event):
        # Moving the mouse over the window keeps it open so the result can be read.
        self._close_timer.stop()
        super().enterEvent(event)

    def closeEvent(self, event):
        self._close_timer.stop()
        self.pending.clear()
        runner = self.runner
        if runner is not None:
            runner.cancel()
            # Wait for ffmpeg to stop so the runner deletes its half-written file before we exit.
            proc = getattr(runner, "_proc", None)
            if proc is not None:
                proc.waitForFinished(3000)
        super().closeEvent(event)
