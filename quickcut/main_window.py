from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSplitter,
    QStyle, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__
from .command import build_plan, compute_bitrates, effective_crossfade, output_duration, output_geometry
from .export_panel import ExportPanel
from .exporter import ExportRunner
from .ffmpeg import FFmpegError, Tools, detect_encoders, probe
from .models import Clip
from .trim_bar import TrimBar

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg",
                    ".mpeg", ".ts", ".mts", ".m2ts", ".3gp", ".gif"}
CLIP_ID = Qt.UserRole


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:06.3f}" if h >= 1 else f"{int(m)}:{s:06.3f}"


def fmt_size(num_bytes: float) -> str:
    mb = num_bytes / 1048576
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


class ClipList(QListWidget):
    """Reorderable by drag; also accepts video files dropped from Explorer."""
    filesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAcceptDrops(True)
        self.setAlternatingRowColors(True)
        self.setWordWrap(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            self.filesDropped.emit([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class _EncoderProbe(QObject):
    done = Signal(list)


class MainWindow(QMainWindow):
    def __init__(self, tools: Tools):
        super().__init__()
        self.tools = tools
        self.qsettings = QSettings("QuickCut", "QuickCut")
        self.clips: dict[int, Clip] = {}
        self._next_id = 0
        self._current: int | None = None
        self._runner: ExportRunner | None = None
        self._pending_seek: float | None = None

        self.setWindowTitle(f"QuickCut {__version__}")
        self.resize(1400, 820)
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_shortcuts()
        self.panel.restore_state(self.qsettings)
        geometry = self.qsettings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self._detect_encoders()
        self._refresh()

    # -- UI construction -------------------------------------------------------------------------

    def _build_ui(self):
        splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(splitter)

        # Left: clip list
        left = QWidget()
        lv = QVBoxLayout(left)
        title = QLabel("<b>Clips</b> — played in this order, drag to reorder")
        title.setWordWrap(True)
        lv.addWidget(title)
        self.list = ClipList()
        self.list.filesDropped.connect(self.add_files)
        self.list.currentItemChanged.connect(lambda *_: self._select_current())
        self.list.model().rowsMoved.connect(lambda *_: self._refresh())
        lv.addWidget(self.list, 1)
        row = QHBoxLayout()
        st = self.style()
        for text, icon, slot, tip in (
            ("Add", QStyle.SP_FileDialogNewFolder, self.open_files, "Add video files (Ctrl+O)"),
            ("Remove", QStyle.SP_TrashIcon, self.remove_clip, "Remove selected clip (Delete)"),
            ("", QStyle.SP_ArrowUp, lambda: self.move_clip(-1), "Move up"),
            ("", QStyle.SP_ArrowDown, lambda: self.move_clip(1), "Move down"),
        ):
            b = QToolButton()
            b.setIcon(st.standardIcon(icon))
            b.setText(text)
            b.setToolTip(tip)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if text else Qt.ToolButtonIconOnly)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        lv.addLayout(row)
        self.total_label = QLabel()
        self.total_label.setWordWrap(True)
        lv.addWidget(self.total_label)
        splitter.addWidget(left)

        # Middle: preview + trimming
        mid = QWidget()
        mv = QVBoxLayout(mid)
        self.video = QVideoWidget()
        self.video.setMinimumSize(480, 270)
        self.video.setStyleSheet("background: black;")
        mv.addWidget(self.video, 1)
        self.drop_hint = QLabel("Drop video files here or click <b>Add</b> to get started")
        self.drop_hint.setAlignment(Qt.AlignCenter)
        mv.addWidget(self.drop_hint)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_position)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.errorOccurred.connect(lambda _e, msg: self.statusBar().showMessage(f"Preview: {msg}", 8000))

        self.trim = TrimBar()
        self.trim.startChanged.connect(lambda t: self._set_trim(start=t, from_bar=True))
        self.trim.endChanged.connect(lambda t: self._set_trim(end=t, from_bar=True))
        self.trim.seekRequested.connect(self._seek)
        mv.addWidget(self.trim)

        row = QHBoxLayout()
        self.play_btn = QPushButton()
        self.play_btn.setIcon(st.standardIcon(QStyle.SP_MediaPlay))
        self.play_btn.setToolTip("Play / pause (Space)")
        self.play_btn.clicked.connect(self.toggle_play)
        row.addWidget(self.play_btn)
        for icon, delta, tip in ((QStyle.SP_MediaSeekBackward, -1, "Previous frame (Left)"),
                                 (QStyle.SP_MediaSeekForward, 1, "Next frame (Right)")):
            b = QToolButton()
            b.setIcon(st.standardIcon(icon))
            b.setToolTip(tip + ". Hold Shift for 1 second steps.")
            b.clicked.connect(lambda _=False, d=delta: self.step(d))
            row.addWidget(b)
        self.time_label = QLabel("0:00.000 / 0:00.000")
        self.time_label.setStyleSheet("font-family: Consolas, monospace;")
        row.addWidget(self.time_label)
        row.addStretch()
        self.loop = QCheckBox("Loop selection")
        self.loop.setChecked(True)
        self.loop.setToolTip("Preview plays only the trimmed part, over and over")
        row.addWidget(self.loop)
        mv.addLayout(row)

        row = QHBoxLayout()
        self.start_spin = self._time_spin()
        self.end_spin = self._time_spin()
        self.start_spin.valueChanged.connect(lambda v: self._set_trim(start=v))
        self.end_spin.valueChanged.connect(lambda v: self._set_trim(end=v))
        set_start = QPushButton("Set start here  [I]")
        set_start.clicked.connect(self.mark_start)
        set_end = QPushButton("Set end here  [O]")
        set_end.clicked.connect(self.mark_end)
        reset = QPushButton("Reset trim")
        reset.clicked.connect(self.reset_trim)
        row.addWidget(QLabel("Start"))
        row.addWidget(self.start_spin)
        row.addWidget(set_start)
        row.addSpacing(16)
        row.addWidget(QLabel("End"))
        row.addWidget(self.end_spin)
        row.addWidget(set_end)
        row.addSpacing(16)
        row.addWidget(reset)
        row.addStretch()
        self.length_label = QLabel()
        row.addWidget(self.length_label)
        mv.addLayout(row)
        self.trim_controls = [self.trim, self.start_spin, self.end_spin, set_start, set_end, reset,
                              self.play_btn]
        splitter.addWidget(mid)

        # Right: export settings
        self.panel = ExportPanel()
        self.panel.changed.connect(self._refresh_summary)
        self.panel.exportRequested.connect(self.export)
        self.panel.cancelRequested.connect(self.cancel_export)
        splitter.addWidget(self.panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([280, 750, 370])
        splitter.setCollapsible(2, False)

        menu = self.menuBar().addMenu("&File")
        for text, key, slot in (("&Add videos…", QKeySequence.Open, self.open_files),
                                ("&Export…", "Ctrl+E", self.export),
                                ("Remove all clips", None, self.clear_clips)):
            act = QAction(text, self)
            if key:
                act.setShortcut(key)
            act.triggered.connect(slot)
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction("E&xit", self.close)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("Keyboard shortcuts", self._show_shortcuts)
        help_menu.addAction("About QuickCut", lambda: QMessageBox.about(
            self, "About QuickCut",
            f"<b>QuickCut {__version__}</b><br>Trim, join, resize and compress videos.<br>"
            f"Powered by FFmpeg:<br><code>{self.tools.ffmpeg}</code>"))

    def _time_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setSuffix(" s")
        spin.setSingleStep(0.1)
        spin.setRange(0, 0)
        spin.setMinimumWidth(100)
        spin.setKeyboardTracking(False)
        return spin

    def _build_shortcuts(self):
        for key, slot in (("Space", self.toggle_play), ("I", self.mark_start), ("O", self.mark_end),
                          ("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1)),
                          ("Shift+Left", lambda: self.step(-1, seconds=True)),
                          ("Shift+Right", lambda: self.step(1, seconds=True)),
                          ("Home", lambda: self._seek(self._clip().start if self._clip() else 0)),
                          ("End", lambda: self._seek(self._clip().end if self._clip() else 0)),
                          ("Delete", self.remove_clip)):
            QShortcut(QKeySequence(key), self, activated=slot)

    def _show_shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts", (
            "Space — play / pause\n"
            "I / O — set start / end at the playhead\n"
            "Left / Right — step one frame\n"
            "Shift + Left / Right — step one second\n"
            "Home / End — jump to start / end of the trim\n"
            "Delete — remove the selected clip\n"
            "Ctrl+O — add videos\n"
            "Ctrl+E — export"))

    # -- encoders --------------------------------------------------------------------------------

    def _detect_encoders(self):
        self.panel.status.setText("Checking which encoders (GPU/CPU) this PC supports…")
        self._probe = _EncoderProbe()
        self._probe.done.connect(self._encoders_ready)
        probe_obj = self._probe
        threading.Thread(target=lambda: probe_obj.done.emit(detect_encoders(self.tools)),
                         daemon=True).start()

    def _encoders_ready(self, encoders):
        self.panel.set_encoders(encoders)
        gpu = [e for e in encoders if e.hardware]
        self.panel.status.setText(
            f"GPU encoding available: {', '.join(sorted({e.label.split(' (')[0] for e in gpu}))}" if gpu
            else "No GPU encoder found; using the CPU.")
        if not encoders:
            QMessageBox.warning(self, "QuickCut", "This FFmpeg build has no H.264, HEVC or AV1 encoders.")

    # -- clips -----------------------------------------------------------------------------------

    def open_files(self):
        start_dir = self.qsettings.value("dirs/open", str(Path.home() / "Videos"))
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(self, "Add videos", start_dir,
                                                f"Videos ({exts});;All files (*)")
        if files:
            self.qsettings.setValue("dirs/open", str(Path(files[0]).parent))
            self.add_files(files)

    def add_files(self, paths: list[str]):
        expanded = []
        for p in paths:
            if os.path.isdir(p):
                expanded += sorted(str(f) for f in Path(p).iterdir()
                                   if f.suffix.lower() in VIDEO_EXTENSIONS)
            elif os.path.isfile(p):
                expanded.append(p)
        errors = []
        first_new = None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for path in expanded:
                try:
                    info = probe(self.tools, path)
                except (FFmpegError, OSError, ValueError) as exc:
                    errors.append(f"{Path(path).name}: {str(exc).splitlines()[-1] if str(exc) else exc}")
                    continue
                clip_id = self._next_id
                self._next_id += 1
                self.clips[clip_id] = Clip(info)
                item = QListWidgetItem()
                item.setData(CLIP_ID, clip_id)
                item.setToolTip(path)
                self.list.addItem(item)
                if first_new is None:
                    first_new = item
        finally:
            QApplication.restoreOverrideCursor()
        if first_new is not None:
            self.list.setCurrentItem(first_new)
        self._refresh()
        if errors:
            QMessageBox.warning(self, "Some files couldn't be added", "\n".join(errors[:15]))

    def _ordered(self) -> list[Clip]:
        return [self.clips[self.list.item(i).data(CLIP_ID)] for i in range(self.list.count())]

    def _clip(self) -> Clip | None:
        return self.clips.get(self._current) if self._current is not None else None

    def remove_clip(self):
        row = self.list.currentRow()
        if row < 0:
            return
        item = self.list.takeItem(row)
        self.clips.pop(item.data(CLIP_ID), None)
        if not self.list.count():
            self._current = None
            self.player.setSource(QUrl())
            self._select_current()
        self._refresh()

    def clear_clips(self):
        self.list.clear()
        self.clips.clear()
        self._current = None
        self.player.setSource(QUrl())
        self._select_current()
        self._refresh()

    def move_clip(self, delta: int):
        row = self.list.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)
        self._refresh()

    def _select_current(self):
        item = self.list.currentItem()
        clip_id = item.data(CLIP_ID) if item else None
        previous = self._clip()
        self._current = clip_id
        clip = self._clip()
        for w in self.trim_controls:
            w.setEnabled(clip is not None)
        self.drop_hint.setVisible(clip is None)
        if clip is None:
            self.trim.set_clip(0, 0, 0)
            self.length_label.setText("")
            self.time_label.setText("0:00.000 / 0:00.000")
            return
        for spin in (self.start_spin, self.end_spin):
            spin.blockSignals(True)
            spin.setRange(0, clip.info.duration)
            spin.blockSignals(False)
        self._sync_trim_widgets()
        self.trim.set_clip(clip.info.duration, clip.start, clip.end)
        self.trim.set_position(clip.start)
        self.time_label.setText(f"{fmt_time(clip.start)} / {fmt_time(clip.info.duration)}")
        if previous is None or previous.info.path != clip.info.path:
            self._pending_seek = clip.start
            self.player.setSource(QUrl.fromLocalFile(clip.info.path))
        else:
            self._seek(clip.start)

    # -- trimming --------------------------------------------------------------------------------

    def _set_trim(self, start: float | None = None, end: float | None = None, from_bar=False):
        clip = self._clip()
        if clip is None:
            return
        if start is not None:
            clip.start = max(0.0, min(start, clip.end - 0.1))
        if end is not None:
            clip.end = min(clip.info.duration, max(end, clip.start + 0.1))
        self._sync_trim_widgets()
        if not from_bar:
            self.trim.set_range(clip.start, clip.end)
        self._refresh()

    def _sync_trim_widgets(self):
        clip = self._clip()
        for spin, value in ((self.start_spin, clip.start), (self.end_spin, clip.end)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.length_label.setText(f"Selection: <b>{fmt_time(clip.length)}</b>")

    def _playhead(self) -> float:
        return self.player.position() / 1000

    def mark_start(self):
        if self._clip():
            self._set_trim(start=self._playhead())

    def mark_end(self):
        if self._clip():
            self._set_trim(end=self._playhead())

    def reset_trim(self):
        clip = self._clip()
        if clip:
            self._set_trim(start=0, end=clip.info.duration)

    # -- playback --------------------------------------------------------------------------------

    def toggle_play(self):
        clip = self._clip()
        if clip is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            return
        pos = self._playhead()
        if self.loop.isChecked() and not clip.start <= pos < clip.end - 0.05:
            self._seek(clip.start)
        self.player.play()

    def step(self, direction: int, seconds: bool = False):
        clip = self._clip()
        if clip is None:
            return
        self.player.pause()
        delta = 1.0 if seconds else 1.0 / max(clip.info.fps_value, 1)
        self._seek(min(clip.info.duration, max(0.0, self._playhead() + direction * delta)))

    def _seek(self, seconds: float):
        self.player.setPosition(int(round(seconds * 1000)))
        self.trim.set_position(seconds)

    def _on_position(self, ms: int):
        clip = self._clip()
        if clip is None:
            return
        pos = ms / 1000
        self.trim.set_position(pos)
        self.time_label.setText(f"{fmt_time(pos)} / {fmt_time(clip.info.duration)}")
        if self.player.playbackState() == QMediaPlayer.PlayingState and pos >= clip.end:
            if self.loop.isChecked():
                self._seek(clip.start)
            elif pos >= clip.info.duration - 0.05:
                self.player.pause()

    def _on_playback_state(self, state):
        icon = QStyle.SP_MediaPause if state == QMediaPlayer.PlayingState else QStyle.SP_MediaPlay
        self.play_btn.setIcon(self.style().standardIcon(icon))

    def _on_media_status(self, status):
        if status == QMediaPlayer.LoadedMedia and self._pending_seek is not None:
            self._seek(self._pending_seek)
            self._pending_seek = None
            self.player.pause()  # renders the first frame without playing

    # -- summary ---------------------------------------------------------------------------------

    def _refresh(self):
        clips = self._ordered()
        for i in range(self.list.count()):
            item = self.list.item(i)
            c = self.clips[item.data(CLIP_ID)]
            trimmed = c.start > 0.0005 or c.end < c.info.duration - 0.0005
            extra = f"  ✂ {fmt_time(c.start)} – {fmt_time(c.end)}" if trimmed else ""
            item.setText(f"{i + 1}. {c.info.name}\n"
                         f"     {fmt_time(c.length)} · {c.info.width}×{c.info.height} · "
                         f"{c.info.fps_value:.3g} fps{'' if c.info.has_audio else ' · no audio'}{extra}")
        if clips:
            raw = sum(c.length for c in clips)
            self.total_label.setText(f"{len(clips)} clip{'s' if len(clips) != 1 else ''}, "
                                     f"{fmt_time(raw)} total")
        else:
            self.total_label.setText("")
        self._refresh_summary()

    def _refresh_summary(self):
        clips = self._ordered()
        if not clips:
            self.panel.summary.setText("<i>Add a clip to see the output details.</i>")
            return
        s = self.panel.settings()
        w, h, fps = output_geometry(clips, s)
        duration = output_duration(clips, s)
        video_k, audio_k = compute_bitrates(clips, s)
        lines = [f"<b>Output:</b> {w}×{h}, {float(eval_rate(fps)):.3g} fps, {fmt_time(duration)}"]
        if effective_crossfade(clips, s) < s.crossfade and len(clips) > 1:
            lines.append(f"Crossfade shortened to {effective_crossfade(clips, s):.2f} s to fit the shortest clip.")
        if video_k is not None:
            est = (video_k + audio_k) * 1000 / 8 * duration
            lines.append(f"Video {video_k:,} kbps{f' + audio {audio_k} kbps' if audio_k else ''} "
                         f"→ about <b>{fmt_size(est)}</b>")
            bits_per_pixel = video_k * 1000 / (w * h * eval_rate(fps))
            if bits_per_pixel < 0.02:
                lines.append("<span style='color:#e8a33d'>⚠ Very low bitrate for this resolution — "
                             "expect blocky video. Lower the resolution or frame rate for a cleaner result."
                             "</span>")
        else:
            lines.append("File size depends on the content.")
        sizes = {(c.info.width, c.info.height) for c in clips}
        if len(sizes) > 1:
            lines.append("Clips have different sizes; they'll be fitted with black bars where needed.")
        self.panel.summary.setText("<br>".join(lines))

    # -- export ----------------------------------------------------------------------------------

    def export(self):
        if self._runner is not None:
            return
        clips = self._ordered()
        if not clips:
            QMessageBox.information(self, "QuickCut", "Add at least one video first.")
            return
        if not self.panel.encoder.currentData():
            QMessageBox.information(self, "QuickCut", "Still checking encoders, try again in a moment.")
            return
        ext = self.panel.extension
        first = Path(clips[0].info.path)
        out_dir = self.qsettings.value("dirs/save", str(first.parent))
        suggestion = str(Path(out_dir) / f"{first.stem}_edit{ext}")
        name = dict(FORMAT_FILTERS)[ext]
        path, _ = QFileDialog.getSaveFileName(self, "Export video", suggestion, f"{name} (*{ext})")
        if not path:
            return
        if Path(path).suffix.lower() != ext:
            path += ext
        inputs = {os.path.normcase(os.path.abspath(c.info.path)) for c in clips}
        if os.path.normcase(os.path.abspath(path)) in inputs:
            QMessageBox.warning(self, "QuickCut", "Choose a different file name; that's one of your input clips.")
            return
        self.qsettings.setValue("dirs/save", str(Path(path).parent))

        s = self.panel.settings()
        s.output = path
        try:
            plan = build_plan(self.tools.ffmpeg, clips, s)
        except ValueError as exc:
            QMessageBox.warning(self, "QuickCut", str(exc))
            return
        self.player.pause()
        self._runner = ExportRunner(plan, self)
        self._runner.progress.connect(self.panel.set_progress)
        self._runner.finished.connect(self._export_finished)
        self.panel.set_busy(True)
        self.panel.status.setText("Starting…")
        self._runner.start()

    def cancel_export(self):
        if self._runner:
            self._runner.cancel()

    def _export_finished(self, ok: bool, message: str):
        runner, self._runner = self._runner, None
        self.panel.set_busy(False)
        if not ok:
            self.panel.status.setText(message.splitlines()[0] if message else "Export failed")
            if message != "Export cancelled.":
                QMessageBox.critical(self, "Export failed", message)
            return
        size = os.path.getsize(message)
        self.panel.status.setText(f"Saved {Path(message).name} ({fmt_size(size)})")
        box = QMessageBox(self)
        box.setWindowTitle("Export finished")
        box.setIcon(QMessageBox.Warning if runner.warning else QMessageBox.Information)
        box.setText(f"Saved <b>{Path(message).name}</b> ({fmt_size(size)})"
                    + (f"<br><br>{runner.warning}" if runner.warning else ""))
        show = box.addButton("Show in folder", QMessageBox.ActionRole)
        play = box.addButton("Play", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is show:
            reveal_in_folder(message)
        elif box.clickedButton() is play:
            QDesktopServices.openUrl(QUrl.fromLocalFile(message))

    # -- window ----------------------------------------------------------------------------------

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def closeEvent(self, e):
        if self._runner is not None:
            answer = QMessageBox.question(self, "QuickCut", "An export is running. Cancel it and quit?")
            if answer != QMessageBox.Yes:
                e.ignore()
                return
            self._runner.cancel()
        self.panel.save_state(self.qsettings)
        self.qsettings.setValue("window/geometry", self.saveGeometry())
        super().closeEvent(e)


FORMAT_FILTERS = [(".mp4", "MP4 video"), (".mkv", "Matroska video"), (".mov", "QuickTime video")]


def eval_rate(rate: str) -> float:
    if "/" in rate:
        num, den = rate.split("/")
        return float(num) / float(den)
    return float(rate)


def reveal_in_folder(path: str):
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))
