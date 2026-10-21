from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox,
    QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressDialog, QPushButton, QSplitter, QStyle, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__, shell_integration
from .command import (
    ExportPlan, auto_short_side, bits_per_pixel, build_plan, compute_bitrates, copy_mode_problems,
    effective_crossfade, output_duration, output_fps, output_frame, output_kind,
)
from .config import app_settings
from .export_panel import FORMATS, ExportPanel
from .exporter import ExportRunner
from .ffmpeg import ENCODERS, FFmpegError, Tools, detect_encoders, probe
from .jobs import DONE, FAILED, Job, JobQueue, JobsPanel
from .media_cache import MediaCache
from .models import Clip, ExportSettings, Overlay
from .overlays import render_logo, render_text
from .player_view import PlayerView
from .preview_dialog import PreviewDialog
from .project import PROJECT_EXT, apply_clip_fields, load_project, save_project
from .theme import THEMES, apply_theme
from .trim_bar import TrimBar
from .updates import RELEASES_PAGE, UpdateChecker

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg",
                    ".mpeg", ".ts", ".mts", ".m2ts", ".3gp", ".gif"}
CLIP_ID = Qt.UserRole
SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
MAX_RECENT = 8
HISTORY_LIMIT = 200
COALESCE_SECONDS = 1.5
PREVIEW_SHORT_SIDE = 360


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:06.3f}" if h >= 1 else f"{int(m)}:{s:06.3f}"


def fmt_size(num_bytes: float) -> str:
    mb = num_bytes / 1048576
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


def reveal_in_folder(path: str):
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))


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
        self.setIconSize(QSize(96, 54))
        self.setSpacing(1)

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
        self.qsettings = app_settings()
        self.clips: dict[int, Clip] = {}
        self._next_id = 0
        self._current: int | None = None
        self._pending_seek: float | None = None
        self._encoders = []
        self.project_path: str | None = None
        self._dirty = False
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []
        self._committed: tuple | None = None
        self._last_key = ""
        self._last_time = 0.0
        self._restoring = False
        self._preview_runner: ExportRunner | None = None

        self.cache = MediaCache(tools, self)
        self.cache.ready.connect(self._image_ready)
        self.queue = JobQueue(self)
        self.queue.changed.connect(self._queue_changed)
        self.queue.progress.connect(self._job_progress)
        self.queue.finished.connect(self._job_finished)
        self.updates = UpdateChecker(self)

        self.setAcceptDrops(True)
        self.resize(1440, 860)
        self._build_ui()
        self._build_menus()
        self._build_shortcuts()
        self.panel.restore_state(self.qsettings)
        geometry = self.qsettings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        splitter = self.qsettings.value("window/splitter")
        if splitter is not None:
            self.splitter.restoreState(splitter)
        self._detect_encoders()
        self._committed = self._snapshot()
        self._select_current()
        self._refresh()
        self._set_dirty(False)
        if self.qsettings.value("updates/check", False, type=bool):
            self.updates.result.connect(self._update_result_quiet)
            self.updates.check()

    # == UI construction ===========================================================================

    def _build_ui(self):
        self.splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(self.splitter)
        st = self.style()

        # Left: clip list
        left = QWidget()
        lv = QVBoxLayout(left)
        title = QLabel("<b>Clips</b> — played top to bottom, drag to reorder")
        title.setWordWrap(True)
        lv.addWidget(title)
        self.list = ClipList()
        self.list.filesDropped.connect(self.add_files)
        self.list.currentItemChanged.connect(lambda *_: self._select_current())
        self.list.model().rowsMoved.connect(lambda *_: self._changed("move"))
        lv.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.setSpacing(2)
        for text, icon, slot, tip in (
            ("Add", QStyle.SP_FileDialogNewFolder, self.open_files, "Add video files (Ctrl+O)"),
            ("Split", QStyle.SP_ToolBarHorizontalExtensionButton, self.split_clip,
             "Split the selected clip at the playhead (S)"),
            ("Copy", QStyle.SP_FileDialogDetailedView, self.duplicate_clip,
             "Duplicate the selected clip (Ctrl+D)"),
            ("", QStyle.SP_TrashIcon, self.remove_clip, "Remove selected clip (Delete)"),
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
        self.splitter.addWidget(left)

        # Middle: preview, trimming, clip effects
        mid = QWidget()
        mv = QVBoxLayout(mid)
        self.view = PlayerView()
        mv.addWidget(self.view, 1)
        self.drop_hint = QLabel("Drop videos or a project here, or click <b>Add</b> to get started")
        self.drop_hint.setAlignment(Qt.AlignCenter)
        mv.addWidget(self.drop_hint)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.view.video_output)
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
        self.step_buttons = []
        for icon, delta, tip in ((QStyle.SP_MediaSeekBackward, -1, "Previous frame (Left)"),
                                 (QStyle.SP_MediaSeekForward, 1, "Next frame (Right)")):
            b = QToolButton()
            b.setIcon(st.standardIcon(icon))
            b.setToolTip(tip + ". Hold Shift for 1 second steps.")
            b.clicked.connect(lambda _=False, d=delta: self.step(d))
            row.addWidget(b)
            self.step_buttons.append(b)
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
        set_start = QPushButton("Set start  [I]")
        set_start.clicked.connect(self.mark_start)
        set_end = QPushButton("Set end  [O]")
        set_end.clicked.connect(self.mark_end)
        reset = QPushButton("Reset trim")
        reset.clicked.connect(self.reset_trim)
        row.addWidget(QLabel("Start"))
        row.addWidget(self.start_spin)
        row.addWidget(set_start)
        row.addSpacing(12)
        row.addWidget(QLabel("End"))
        row.addWidget(self.end_spin)
        row.addWidget(set_end)
        row.addSpacing(12)
        row.addWidget(reset)
        row.addStretch()
        self.length_label = QLabel()
        row.addWidget(self.length_label)
        mv.addLayout(row)

        row = QHBoxLayout()
        clip_label = QLabel("<b>This clip:</b>")
        row.addWidget(clip_label)
        row.addWidget(QLabel("Speed"))
        self.speed = QComboBox()
        for s in SPEEDS:
            self.speed.addItem(f"{s:g}×", s)
        self.speed.setToolTip("Playback speed of this clip. Audio keeps its pitch.")
        self.speed.currentIndexChanged.connect(self._speed_changed)
        row.addWidget(self.speed)
        rot_left = QToolButton()
        rot_left.setText("↺ 90°")
        rot_left.setToolTip("Rotate left")
        rot_left.clicked.connect(lambda: self._rotate(-90))
        rot_right = QToolButton()
        rot_right.setText("↻ 90°")
        rot_right.setToolTip("Rotate right")
        rot_right.clicked.connect(lambda: self._rotate(90))
        self.hflip = QToolButton()
        self.hflip.setText("↔ Mirror")
        self.hflip.setCheckable(True)
        self.hflip.setToolTip("Mirror left to right")
        self.vflip = QToolButton()
        self.vflip.setText("↕ Flip")
        self.vflip.setCheckable(True)
        self.vflip.setToolTip("Flip upside down")
        self.reverse = QCheckBox("Reverse")
        self.reverse.setToolTip("Play this clip backwards (shows in Preview edit, not in this player). "
                                "Long clips need a lot of memory to reverse.")
        self.hflip.toggled.connect(lambda on: self._set_effect("hflip", on))
        self.vflip.toggled.connect(lambda on: self._set_effect("vflip", on))
        self.reverse.toggled.connect(lambda on: self._set_effect("reverse", on))
        for w in (rot_left, rot_right, self.hflip, self.vflip, self.reverse):
            row.addWidget(w)
        row.addStretch()
        mv.addLayout(row)
        self.clip_controls = [self.trim, self.start_spin, self.end_spin, set_start, set_end, reset,
                              self.play_btn, self.speed, rot_left, rot_right, self.hflip, self.vflip,
                              self.reverse, *self.step_buttons]
        self.splitter.addWidget(mid)

        # Right: export settings
        self.panel = ExportPanel()
        self.panel.changed.connect(self._panel_changed)
        self.panel.exportRequested.connect(self.export)
        self.panel.queueRequested.connect(self.export_separately)
        self.panel.previewRequested.connect(self.preview_edit)
        self.panel.cancelRequested.connect(self.queue.cancel_current)
        self.splitter.addWidget(self.panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([300, 760, 380])
        self.splitter.setCollapsible(2, False)

        # Bottom: export queue
        self.jobs_panel = JobsPanel(self.queue)
        self.jobs_panel.revealRequested.connect(reveal_in_folder)
        self.jobs_dock = QDockWidget("Export queue", self)
        self.jobs_dock.setObjectName("exportQueue")
        self.jobs_dock.setWidget(self.jobs_panel)
        self.jobs_dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.jobs_dock)
        self.jobs_dock.hide()
        self.jobs_dock.visibilityChanged.connect(self._size_jobs_dock)

        self.update_label = QLabel()
        self.update_label.setOpenExternalLinks(True)
        self.update_label.hide()
        self.statusBar().addPermanentWidget(self.update_label)

    def _size_jobs_dock(self, visible: bool):
        if visible and not getattr(self, "_jobs_sized", False):
            self._jobs_sized = True
            self.resizeDocks([self.jobs_dock], [170], Qt.Vertical)

    def _time_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setSuffix(" s")
        spin.setSingleStep(0.1)
        spin.setRange(0, 0)
        spin.setMinimumWidth(100)
        spin.setKeyboardTracking(False)
        return spin

    def _action(self, menu, text, slot, key=None, checkable=False) -> QAction:
        act = QAction(text, self)
        if key:
            act.setShortcut(QKeySequence(key))
        act.setCheckable(checkable)
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self._action(m, "&New project", self.new_project, QKeySequence.New)
        self._action(m, "&Open project…", self.open_project_dialog, "Ctrl+Shift+O")
        self.recent_menu = m.addMenu("Open &recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self._action(m, "&Save project", self.save_project, QKeySequence.Save)
        self._action(m, "Save project &as…", self.save_project_as, "Ctrl+Shift+S")
        m.addSeparator()
        self._action(m, "&Add videos…", self.open_files, QKeySequence.Open)
        m.addSeparator()
        self._action(m, "&Export…", self.export, "Ctrl+E")
        self._action(m, "Export each clip &separately…", self.export_separately)
        self._action(m, "&Preview edit", self.preview_edit, "Ctrl+P")
        m.addSeparator()
        self._action(m, "E&xit", self.close)

        m = mb.addMenu("&Edit")
        self.undo_act = self._action(m, "&Undo", self.undo, QKeySequence.Undo)
        self.redo_act = self._action(m, "&Redo", self.redo, QKeySequence.Redo)
        m.addSeparator()
        self._action(m, "&Split at playhead", self.split_clip, "S")
        self._action(m, "&Duplicate clip", self.duplicate_clip, "Ctrl+D")
        self._action(m, "&Remove clip", self.remove_clip)
        self._action(m, "Remove &all clips", self.clear_clips)

        m = mb.addMenu("&View")
        theme_menu = m.addMenu("&Theme")
        group = QActionGroup(self)
        current = self.qsettings.value("view/theme", "system")
        for text, key in THEMES:
            act = self._action(theme_menu, text, lambda _=False, k=key: self.set_theme(k), checkable=True)
            act.setChecked(key == current)
            group.addAction(act)
        act = self.jobs_dock.toggleViewAction()
        act.setText("Export &queue")
        act.setShortcut(QKeySequence("Ctrl+J"))
        m.addAction(act)

        m = mb.addMenu("&Tools")
        self.shell_act = self._action(m, "Add “Open with QuickCut” to Explorer", self._toggle_shell,
                                      checkable=True)
        self.shell_act.setEnabled(shell_integration.SUPPORTED)
        self.shell_act.setChecked(shell_integration.is_registered())

        m = mb.addMenu("&Help")
        self._action(m, "Keyboard shortcuts", self._show_shortcuts, "F1")
        m.addSeparator()
        self._action(m, "Check for updates now", self.check_updates)
        act = self._action(m, "Check for updates on startup",
                           lambda on: self.qsettings.setValue("updates/check", on), checkable=True)
        act.setChecked(self.qsettings.value("updates/check", False, type=bool))
        m.addSeparator()
        self._action(m, "About QuickCut", lambda: QMessageBox.about(
            self, "About QuickCut",
            f"<b>QuickCut {__version__}</b><br>Trim, join, resize and compress videos.<br><br>"
            f"Powered by FFmpeg:<br><code>{self.tools.ffmpeg}</code><br><br>"
            f"<a href='{RELEASES_PAGE}'>Releases on GitHub</a>"))
        self._update_undo_actions()

    def _build_shortcuts(self):
        for key, slot in (("Space", self.toggle_play), ("I", self.mark_start), ("O", self.mark_end),
                          ("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1)),
                          ("Shift+Left", lambda: self.step(-1, seconds=True)),
                          ("Shift+Right", lambda: self.step(1, seconds=True)),
                          ("Home", lambda: self._seek(self._clip().start if self._clip() else 0)),
                          ("End", lambda: self._seek(self._clip().end if self._clip() else 0)),
                          ("Delete", self.remove_clip), ("Ctrl+Shift+Z", self.redo)):
            QShortcut(QKeySequence(key), self, activated=slot)

    def _show_shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts", (
            "Space — play / pause\n"
            "I / O — set start / end at the playhead\n"
            "Left / Right — step one frame\n"
            "Shift + Left / Right — step one second\n"
            "Home / End — jump to start / end of the trim\n"
            "S — split the clip at the playhead\n"
            "Ctrl+D — duplicate the clip\n"
            "Delete — remove the selected clip\n"
            "Ctrl+Z / Ctrl+Y — undo / redo\n"
            "Ctrl+O — add videos\n"
            "Ctrl+S — save project\n"
            "Ctrl+P — preview the full edit\n"
            "Ctrl+E — export\n"
            "Ctrl+J — show the export queue"))

    # == encoders ==================================================================================

    def _detect_encoders(self):
        self.panel.status.setText("Checking which encoders (GPU/CPU) this PC supports…")
        self._probe = _EncoderProbe()
        self._probe.done.connect(self._encoders_ready)
        probe_obj = self._probe
        threading.Thread(target=lambda: probe_obj.done.emit(detect_encoders(self.tools)),
                         daemon=True).start()

    def _encoders_ready(self, encoders):
        self._encoders = encoders
        self.panel.set_encoders(encoders)
        self._idle_status()
        if not encoders:
            QMessageBox.warning(self, "QuickCut", "This FFmpeg build has no H.264, HEVC, AV1 or VP9 encoders.")

    def _idle_status(self):
        if self.queue.current is not None:
            return
        gpu = sorted({e.label.split(" (")[0] for e in self._encoders if e.hardware})
        self.panel.status.setText(f"GPU encoding available: {', '.join(gpu)}" if gpu
                                  else "No GPU encoder found; using the CPU.")

    # == clips =====================================================================================

    def open_files(self):
        start_dir = self.qsettings.value("dirs/open", str(Path.home() / "Videos"))
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add videos", start_dir,
            f"Videos ({exts});;QuickCut projects (*{PROJECT_EXT});;All files (*)")
        if files:
            self.qsettings.setValue("dirs/open", str(Path(files[0]).parent))
            self.add_files(files)

    def add_files(self, paths: list[str]):
        projects = [p for p in paths if p.lower().endswith(PROJECT_EXT)]
        if projects:
            self.open_project(projects[0])
            paths = [p for p in paths if p not in projects]
        expanded = []
        for p in paths:
            if os.path.isdir(p):
                expanded += sorted(str(f) for f in Path(p).iterdir() if f.suffix.lower() in VIDEO_EXTENSIONS)
            elif os.path.isfile(p):
                expanded.append(p)
        if not expanded:
            return
        errors = []
        first_new = None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for path in expanded:
                try:
                    info = probe(self.tools, path)
                except (FFmpegError, OSError, ValueError) as exc:
                    msg = str(exc).splitlines()[-1] if str(exc) else type(exc).__name__
                    errors.append(f"{Path(path).name}: {msg}")
                    continue
                item = self._add_clip(Clip(info))
                first_new = first_new or item
        finally:
            QApplication.restoreOverrideCursor()
        if first_new is not None:
            self.list.setCurrentItem(first_new)
        self._changed("add")
        if errors:
            QMessageBox.warning(self, "Some files couldn't be added", "\n".join(errors[:15]))

    def _add_clip(self, clip: Clip, row: int | None = None, clip_id: int | None = None) -> QListWidgetItem:
        if clip_id is None:
            clip_id = self._next_id
        self._next_id = max(self._next_id, clip_id + 1)
        self.clips[clip_id] = clip
        item = QListWidgetItem()
        item.setData(CLIP_ID, clip_id)
        item.setToolTip(clip.info.path)
        thumb = self.cache.get("thumb", clip.info)
        if thumb is not None and not thumb.isNull():
            item.setIcon(QIcon(QPixmap.fromImage(thumb)))
        if row is None:
            self.list.addItem(item)
        else:
            self.list.insertItem(row, item)
        return item

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
        self._changed("remove")

    def clear_clips(self):
        if not self.clips:
            return
        self.list.clear()
        self.clips.clear()
        self._current = None
        self.player.setSource(QUrl())
        self._select_current()
        self._changed("clear")

    def move_clip(self, delta: int):
        row = self.list.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)
        self._changed("move")

    def duplicate_clip(self):
        clip = self._clip()
        if clip is None:
            return
        item = self._add_clip(replace(clip), self.list.currentRow() + 1)
        self.list.setCurrentItem(item)
        self._changed("duplicate")

    def split_clip(self):
        clip = self._clip()
        if clip is None:
            return
        t = self._playhead()
        if not clip.start + 0.1 < t < clip.end - 0.1:
            self.statusBar().showMessage("Move the playhead inside the selection to split there.", 5000)
            return
        second = replace(clip, start=t)
        clip.end = t
        item = self._add_clip(second, self.list.currentRow() + 1)
        self.list.setCurrentItem(item)
        self._changed("split")
        self.statusBar().showMessage(f"Split at {fmt_time(t)}", 4000)

    def _select_current(self):
        item = self.list.currentItem()
        previous = self._clip()
        self._current = item.data(CLIP_ID) if item else None
        clip = self._clip()
        for w in self.clip_controls:
            w.setEnabled(clip is not None)
        self.drop_hint.setVisible(clip is None)
        if clip is None:
            self.trim.set_clip(0, 0, 0)
            self.length_label.setText("")
            self.time_label.setText("0:00.000 / 0:00.000")
            self.view.set_orientation(0, False, False)
            return
        for spin in (self.start_spin, self.end_spin):
            spin.blockSignals(True)
            spin.setRange(0, clip.info.duration)
            spin.blockSignals(False)
        self._sync_clip_widgets()
        self.trim.set_clip(clip.info.duration, clip.start, clip.end)
        self.trim.set_images(self.cache.get("strip", clip.info), self.cache.get("wave", clip.info))
        self.trim.set_position(clip.start)
        self.time_label.setText(f"{fmt_time(clip.start)} / {fmt_time(clip.info.duration)}")
        if previous is None or previous.info.path != clip.info.path or self.player.source().isEmpty():
            self._pending_seek = clip.start
            self.player.setSource(QUrl.fromLocalFile(clip.info.path))
        else:
            self._seek(clip.start)

    def _sync_clip_widgets(self):
        clip = self._clip()
        if clip is None:
            return
        widgets = (self.start_spin, self.end_spin, self.speed, self.hflip, self.vflip, self.reverse)
        for w in widgets:
            w.blockSignals(True)
        self.start_spin.setValue(clip.start)
        self.end_spin.setValue(clip.end)
        i = self.speed.findData(clip.speed)
        if i < 0:
            self.speed.addItem(f"{clip.speed:g}×", clip.speed)
            i = self.speed.count() - 1
        self.speed.setCurrentIndex(i)
        self.hflip.setChecked(clip.hflip)
        self.vflip.setChecked(clip.vflip)
        self.reverse.setChecked(clip.reverse)
        for w in widgets:
            w.blockSignals(False)
        self.player.setPlaybackRate(clip.speed)
        self.view.set_orientation(clip.rotate, clip.hflip, clip.vflip)
        extra = f" → {fmt_time(clip.out_length)} at {clip.speed:g}×" if clip.speed != 1 else ""
        self.length_label.setText(f"Selection: <b>{fmt_time(clip.length)}</b>{extra}")

    # == clip effects ==============================================================================

    def _speed_changed(self):
        clip = self._clip()
        if clip:
            clip.speed = float(self.speed.currentData())
            self._sync_clip_widgets()
            self._changed("speed")

    def _rotate(self, degrees: int):
        clip = self._clip()
        if clip:
            clip.rotate = (clip.rotate + degrees) % 360
            self._sync_clip_widgets()
            self._changed("rotate")

    def _set_effect(self, name: str, on: bool):
        clip = self._clip()
        if clip:
            setattr(clip, name, on)
            self._sync_clip_widgets()
            self._changed(name)

    # == trimming ==================================================================================

    def _set_trim(self, start: float | None = None, end: float | None = None, from_bar=False):
        clip = self._clip()
        if clip is None:
            return
        if start is not None:
            clip.start = max(0.0, min(start, clip.end - 0.1))
        if end is not None:
            clip.end = min(clip.info.duration, max(end, clip.start + 0.1))
        self._sync_clip_widgets()
        if not from_bar:
            self.trim.set_range(clip.start, clip.end)
        self._changed("trim")

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

    # == playback ==================================================================================

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

    # == thumbnails ================================================================================

    def _image_ready(self, kind: str, path: str, image):
        if kind == "thumb" and not image.isNull():
            icon = QIcon(QPixmap.fromImage(image))
            for i in range(self.list.count()):
                item = self.list.item(i)
                if self.clips[item.data(CLIP_ID)].info.path == path:
                    item.setIcon(icon)
        clip = self._clip()
        if clip is not None and clip.info.path == path:
            if kind == "strip":
                self.trim.set_images(filmstrip=image)
            elif kind == "wave":
                self.trim.set_images(waveform=image)

    # == undo / redo ===============================================================================

    def _snapshot(self) -> tuple:
        ids = [self.list.item(i).data(CLIP_ID) for i in range(self.list.count())]
        return tuple((cid, replace(self.clips[cid])) for cid in ids), self._current

    @staticmethod
    def _same(a: tuple | None, b: tuple | None) -> bool:
        def key(snap):
            return tuple((cid, c.info.path, c.start, c.end, c.speed, c.rotate, c.hflip, c.vflip, c.reverse)
                         for cid, c in snap[0])
        return a is not None and b is not None and key(a) == key(b)

    def _changed(self, key: str):
        """Call after any change to the clips: records undo history and refreshes the UI."""
        self._refresh()
        if self._restoring:
            return
        snap = self._snapshot()
        if self._same(snap, self._committed):
            return
        now = time.monotonic()
        coalesce = (key in ("trim", "speed") and key == self._last_key
                    and now - self._last_time < COALESCE_SECONDS)
        if not coalesce and self._committed is not None:
            self._undo.append(self._committed)
            del self._undo[:-HISTORY_LIMIT]
        self._redo.clear()
        self._committed = snap
        self._last_key, self._last_time = key, now
        self._set_dirty()
        self._update_undo_actions()

    def _restore(self, snap: tuple):
        order, current = snap
        self._restoring = True
        try:
            self.list.blockSignals(True)
            self.list.clear()
            self.clips.clear()
            select = None
            for cid, clip in order:
                item = self._add_clip(replace(clip), clip_id=cid)
                if cid == current:
                    select = item
            if select is None and self.list.count():
                select = self.list.item(0)
            if select is not None:
                self.list.setCurrentItem(select)
            self.list.blockSignals(False)
            self._select_current()
            self._refresh()
        finally:
            self._restoring = False
        self._committed = self._snapshot()
        self._last_key = ""
        self._set_dirty()
        self._update_undo_actions()

    def undo(self):
        if self._undo:
            self._redo.append(self._snapshot())
            self._restore(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(self._snapshot())
            self._restore(self._redo.pop())

    def _update_undo_actions(self):
        if hasattr(self, "undo_act"):
            self.undo_act.setEnabled(bool(self._undo))
            self.redo_act.setEnabled(bool(self._redo))

    def _reset_history(self):
        self._undo.clear()
        self._redo.clear()
        self._committed = self._snapshot()
        self._last_key = ""
        self._update_undo_actions()

    # == projects ==================================================================================

    def _set_dirty(self, dirty: bool = True):
        self._dirty = dirty and bool(self.clips or self.project_path)
        name = Path(self.project_path).stem if self.project_path else "Untitled"
        self.setWindowTitle(f"{name}{' •' if self._dirty else ''} — QuickCut")

    def _panel_changed(self):
        self._refresh_summary()
        if self.project_path:
            self._set_dirty()

    def _confirm_discard(self, closing: bool = False) -> bool:
        if not self._dirty:
            return True
        # Quick trim-and-export sessions without a project aren't nagged on close.
        if closing and not self.project_path:
            return True
        question = ("Save changes to this project first?" if self.project_path else
                    "Save these clips as a project first? Otherwise they'll be removed.")
        answer = QMessageBox.question(
            self, "QuickCut", question,
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if answer == QMessageBox.Save:
            return self.save_project()
        return answer == QMessageBox.Discard

    def _replace_clips(self, clips: list[Clip]):
        self._restoring = True
        try:
            self.list.clear()
            self.clips.clear()
            self._current = None
            self.player.setSource(QUrl())
            for clip in clips:
                self._add_clip(clip)
            if self.list.count():
                self.list.setCurrentRow(0)
            self._select_current()
        finally:
            self._restoring = False
        self._refresh()

    def new_project(self):
        if not self._confirm_discard():
            return
        self._replace_clips([])
        self.project_path = None
        self._reset_history()
        self._set_dirty(False)

    def open_project_dialog(self):
        if not self._confirm_discard():
            return
        start = self.qsettings.value("dirs/project", str(Path.home() / "Videos"))
        path, _ = QFileDialog.getOpenFileName(self, "Open project", start, f"QuickCut projects (*{PROJECT_EXT})")
        if path:
            self.open_project(path, confirm=False)

    def open_project(self, path: str, confirm: bool = True):
        if confirm and not self._confirm_discard():
            return
        try:
            entries, export_state = load_project(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Couldn't open project", str(exc))
            return
        problems, clips = [], []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for entry in entries:
                if entry["missing"]:
                    problems.append(f"Missing: {entry.get('path', '?')}")
                    continue
                try:
                    clip = Clip(probe(self.tools, entry["path"]))
                except (FFmpegError, OSError, ValueError):
                    problems.append(f"Unreadable: {entry['path']}")
                    continue
                apply_clip_fields(clip, entry)
                clips.append(clip)
        finally:
            QApplication.restoreOverrideCursor()
        self._replace_clips(clips)
        self.panel.set_state(export_state)
        self.project_path = path
        self._remember_recent(path)
        self.qsettings.setValue("dirs/project", str(Path(path).parent))
        self._reset_history()
        self._set_dirty(False)
        if problems:
            QMessageBox.warning(self, "Some clips couldn't be loaded", "\n".join(problems[:15]))

    def save_project(self) -> bool:
        if not self.project_path:
            return self.save_project_as()
        try:
            save_project(self.project_path, self._ordered(), self.panel.get_state())
        except OSError as exc:
            QMessageBox.warning(self, "Couldn't save project", str(exc))
            return False
        self._remember_recent(self.project_path)
        self._set_dirty(False)
        self.statusBar().showMessage(f"Saved {Path(self.project_path).name}", 4000)
        return True

    def save_project_as(self) -> bool:
        folder = Path(self.qsettings.value("dirs/project", str(Path.home() / "Videos")))
        start = self.project_path or str(folder / f"Untitled{PROJECT_EXT}")
        path, _ = QFileDialog.getSaveFileName(self, "Save project", start, f"QuickCut projects (*{PROJECT_EXT})")
        if not path:
            return False
        if not path.lower().endswith(PROJECT_EXT):
            path += PROJECT_EXT
        self.project_path = path
        self.qsettings.setValue("dirs/project", str(Path(path).parent))
        return self.save_project()

    def _recent(self) -> list[str]:
        value = self.qsettings.value("recent/projects", [])
        return [value] if isinstance(value, str) else list(value or [])

    def _remember_recent(self, path: str):
        recent = [p for p in self._recent() if os.path.normcase(p) != os.path.normcase(path)]
        self.qsettings.setValue("recent/projects", [path] + recent[:MAX_RECENT - 1])

    def _fill_recent(self):
        self.recent_menu.clear()
        recent = [p for p in self._recent() if os.path.isfile(p)]
        if not recent:
            act = self.recent_menu.addAction("No recent projects")
            act.setEnabled(False)
            return
        for i, path in enumerate(recent, 1):
            act = self.recent_menu.addAction(f"&{i}  {Path(path).name}")
            act.setToolTip(path)
            act.triggered.connect(lambda _=False, p=path: self.open_project(p))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear list", lambda: self.qsettings.setValue("recent/projects", []))

    # == summary ===================================================================================

    def _refresh(self):
        clips = self._ordered()
        for i in range(self.list.count()):
            item = self.list.item(i)
            c = self.clips[item.data(CLIP_ID)]
            tags = []
            if c.start > 0.0005 or c.end < c.info.duration - 0.0005:
                tags.append(f"✂ {fmt_time(c.start)}–{fmt_time(c.end)}")
            if c.speed != 1:
                tags.append(f"{c.speed:g}×")
            if c.rotate:
                tags.append(f"↻{c.rotate}°")
            if c.hflip or c.vflip:
                tags.append("flipped")
            if c.reverse:
                tags.append("reversed")
            if c.info.hdr:
                tags.append("HDR")
            if not c.info.has_audio:
                tags.append("no audio")
            item.setText(f"{i + 1}. {c.info.name}\n{fmt_time(c.out_length)} · {c.info.width}×{c.info.height} · "
                         f"{c.info.fps_value:.3g} fps" + (f"\n{' · '.join(tags)}" if tags else ""))
        if clips:
            total = sum(c.out_length for c in clips)
            self.total_label.setText(f"{len(clips)} clip{'s' if len(clips) != 1 else ''}, {fmt_time(total)} total")
        else:
            self.total_label.setText("")
        self._refresh_summary()

    def _refresh_summary(self):
        clips = self._ordered()
        if not clips:
            self.panel.summary.setText("<i>Add a clip to see the output details.</i>")
            return
        s = self.panel.settings()
        s.output = "x" + self.panel.extension
        kind = output_kind(s.output)
        warn = "<span style='color:#e8a33d'>⚠ {}</span>"
        lines = []
        if s.mode == "copy" and kind == "video":
            errors, warnings = copy_mode_problems(clips, s)
            lines.append(f"<b>Output:</b> no re-encode, {fmt_time(output_duration(clips, s))}")
            lines += [warn.format(e) for e in errors] + [f"<i>{w}</i>" for w in warnings]
            self.panel.summary.setText("<br>".join(lines))
            return
        duration = output_duration(clips, s)
        if kind == "audio":
            lines.append(f"<b>Output:</b> audio only, {fmt_time(duration)}")
            if s.mute and not s.music:
                lines.append(warn.format("Nothing to export: the clips' audio is removed and there's no music."))
            self.panel.summary.setText("<br>".join(lines))
            return
        w, h = output_frame(clips, s)
        fps = output_fps(clips, s)
        video_k, audio_k = compute_bitrates(clips, s)
        if video_k is not None and s.mode == "size" and s.auto_resolution and s.encoder in ENCODERS:
            short = auto_short_side(clips, s, video_k, fps)
            if short:
                w, h = output_frame(clips, s, short)
                lines.append(f"Resolution lowered to {short}p to fit the size limit cleanly.")
        num, _, den = fps.partition("/")
        fps_v = float(num) / float(den or 1)
        lines.insert(0, f"<b>Output:</b> {w}×{h}, {fps_v:.3g} fps, {fmt_time(duration)}")
        if len(clips) > 1 and 0 < effective_crossfade(clips, s) < s.crossfade:
            lines.append(f"Transition shortened to {effective_crossfade(clips, s):.2f} s to fit the shortest clip.")
        if video_k is not None:
            est = (video_k + audio_k) * 1000 / 8 * duration
            lines.append(f"Video {video_k:,} kbps{f' + audio {audio_k} kbps' if audio_k else ''} "
                         f"→ about <b>{fmt_size(est)}</b>")
            if bits_per_pixel(video_k, w, h, fps) < 0.02:
                lines.append(warn.format("Very low bitrate for this resolution, expect blocky video. "
                                         "Lower the resolution or frame rate."))
        elif kind in ("gif", "webp"):
            lines.append("Animations get big quickly; keep them short and small.")
        else:
            lines.append("File size depends on the content.")
        if any(c.info.hdr for c in clips):
            lines.append("HDR clips are converted to normal (SDR) colours.")
        if any(c.reverse and c.length > 60 for c in clips):
            lines.append(warn.format("Reversing long clips needs a lot of memory."))
        if len({c.display_size for c in clips}) > 1 and s.fit == "pad":
            lines.append("Clips have different shapes; black bars fill the gaps (see Framing).")
        if s.music and not os.path.isfile(s.music):
            lines.append(warn.format("The music file can't be found."))
        self.panel.summary.setText("<br>".join(lines))

    # == export ====================================================================================

    def _check_ready(self) -> list[Clip] | None:
        clips = self._ordered()
        if not clips:
            QMessageBox.information(self, "QuickCut", "Add at least one video first.")
            return None
        if self.panel.kind == "video" and self.panel.mode.currentData() != "copy" \
                and not self.panel.encoder.currentData():
            QMessageBox.information(self, "QuickCut", "Still checking encoders, try again in a moment.")
            return None
        return clips

    def _plan_with_overlays(self, clips: list[Clip], s: ExportSettings) -> tuple[ExportPlan, str | None]:
        """Build the plan, rendering text/logo overlays at the final frame size first."""
        plan = build_plan(self.tools.ffmpeg, clips, s)
        spec = self.panel.overlay_spec()
        if s.mode == "copy" or output_kind(s.output) == "audio" or not (spec["text"] or spec["logo"]):
            return plan, None
        temp_dir = tempfile.mkdtemp(prefix="quickcut_overlay_")
        try:
            overlays = []
            if spec["logo"]:
                path = render_logo(os.path.join(temp_dir, "logo.png"), plan.width, plan.height, spec["logo"],
                                   spec["logo_pos"], spec["logo_size"], spec["logo_opacity"])
                overlays.append(Overlay(path))
            if spec["text"]:
                path = render_text(os.path.join(temp_dir, "text.png"), plan.width, plan.height, spec["text"],
                                   spec["text_pos"], spec["text_size"], spec["text_color"], spec["text_box"])
                end = spec["text_start"] + spec["text_duration"] if spec["text_duration"] > 0 else None
                overlays.append(Overlay(path, spec["text_start"], end))
            return build_plan(self.tools.ffmpeg, clips, replace(s, overlays=overlays)), temp_dir
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def _queue_export(self, clips: list[Clip], output: str) -> bool:
        s = self.panel.settings()
        s.output = output
        try:
            plan, temp_dir = self._plan_with_overlays(clips, s)
        except ValueError as exc:
            QMessageBox.warning(self, "QuickCut", str(exc))
            return False
        self.queue.add(Job(plan, temp_dir, notes=plan.notes))
        return True

    @staticmethod
    def _same_as_input(path: str, clips: list[Clip]) -> bool:
        inputs = {os.path.normcase(os.path.abspath(c.info.path)) for c in clips}
        return os.path.normcase(os.path.abspath(path)) in inputs

    def export(self):
        clips = self._check_ready()
        if clips is None:
            return
        ext = self.panel.extension
        first = Path(clips[0].info.path)
        out_dir = self.qsettings.value("dirs/save", str(first.parent))
        stem = Path(self.project_path).stem if self.project_path else f"{first.stem}_edit"
        name = {e: n for n, e in FORMATS}[ext]
        path, _ = QFileDialog.getSaveFileName(self, "Export", str(Path(out_dir) / f"{stem}{ext}"),
                                              f"{name} (*{ext})")
        if not path:
            return
        if Path(path).suffix.lower() != ext:
            path += ext
        if self._same_as_input(path, clips):
            QMessageBox.warning(self, "QuickCut", "Choose a different file name; that's one of your input clips.")
            return
        self.qsettings.setValue("dirs/save", str(Path(path).parent))
        self.player.pause()
        self._queue_export(clips, path)

    def export_separately(self):
        clips = self._check_ready()
        if clips is None:
            return
        start = self.qsettings.value("dirs/save", str(Path(clips[0].info.path).parent))
        out_dir = QFileDialog.getExistingDirectory(self, "Export each clip to folder", start)
        if not out_dir:
            return
        self.qsettings.setValue("dirs/save", out_dir)
        ext = self.panel.extension
        used: set[str] = set()
        added = 0
        for clip in clips:
            stem = f"{Path(clip.info.path).stem}_edit"
            path = Path(out_dir) / f"{stem}{ext}"
            n = 2
            while os.path.normcase(str(path)) in used or path.exists() or self._same_as_input(str(path), clips):
                path = Path(out_dir) / f"{stem}_{n}{ext}"
                n += 1
            used.add(os.path.normcase(str(path)))
            if not self._queue_export([clip], str(path)):
                break
            added += 1
        if added:
            self.jobs_dock.show()
            self.statusBar().showMessage(f"Added {added} export{'s' if added != 1 else ''} to the queue", 5000)

    def _queue_changed(self):
        busy = self.queue.current is not None
        self.panel.set_busy(busy)
        self.panel.export_btn.setText("Export… (adds to queue)" if busy else "Export…")
        if len(self.queue.jobs) > 1:
            self.jobs_dock.show()
        if not busy:
            self._idle_status()

    def _job_progress(self, job: Job):
        waiting = self.queue.waiting
        tail = f" · {waiting} more waiting" if waiting else ""
        self.panel.set_progress(job.progress, f"{job.name}: {job.detail}{tail}")

    def _job_finished(self, job: Job):
        if job.status == FAILED:
            self.panel.status.setText(f"{job.name} failed")
            QMessageBox.critical(self, "Export failed", f"{job.name}\n\n{job.detail}")
            return
        if job.status != DONE:
            self.panel.status.setText(f"{job.name}: cancelled")
            return
        self.panel.status.setText(f"Saved {job.name} ({job.detail})")
        if self.queue.waiting or self.queue.current is not None:
            self.statusBar().showMessage(f"Saved {job.name}", 5000)
            return
        if sum(j.status == DONE for j in self.queue.jobs) > 1:
            self.statusBar().showMessage("All exports finished", 8000)
            return
        box = QMessageBox(self)
        box.setWindowTitle("Export finished")
        box.setIcon(QMessageBox.Warning if job.warning else QMessageBox.Information)
        notes = "".join(f"<br>{n}" for n in job.notes)
        box.setText(f"Saved <b>{job.name}</b> ({job.detail}){notes}"
                    + (f"<br><br>{job.warning}" if job.warning else ""))
        show = box.addButton("Show in folder", QMessageBox.ActionRole)
        play = box.addButton("Play", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is show:
            reveal_in_folder(job.output)
        elif box.clickedButton() is play:
            QDesktopServices.openUrl(QUrl.fromLocalFile(job.output))

    # == preview ===================================================================================

    def preview_settings(self, output: str) -> ExportSettings:
        """The current settings, made fast: low resolution, quick H.264, no size targets."""
        s = self.panel.settings()
        fast = next((e for e in self._encoders if e.codec == "h264"), None)
        fps = s.fps if s.fps and float(s.fps) <= 30 else None
        if fps is None and self._ordered() and self._ordered()[0].info.fps_value > 30:
            fps = "30"
        return replace(s, output=output, encoder=fast.name if fast else "libx264", mode="preset",
                       preset="low", speed="fast", two_pass=False, audio_bitrate_k=96, fps=fps,
                       height=min(s.height or PREVIEW_SHORT_SIDE, PREVIEW_SHORT_SIDE))

    def preview_edit(self):
        clips = self._check_ready()
        if clips is None or self._preview_runner is not None:
            return
        if self.panel.kind == "audio":
            QMessageBox.information(self, "QuickCut", "Preview is for video. Audio exports can be "
                                    "played once they're exported.")
            return
        out = os.path.join(tempfile.gettempdir(), f"quickcut_preview_{os.getpid()}_{int(time.time())}.mp4")
        try:
            plan, temp_dir = self._plan_with_overlays(clips, self.preview_settings(out))
        except ValueError as exc:
            QMessageBox.warning(self, "QuickCut", str(exc))
            return
        self.player.pause()
        dialog = QProgressDialog("Rendering a quick preview of the full edit…", "Cancel", 0, 1000, self)
        dialog.setWindowTitle("Preview")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        runner = ExportRunner(plan, self)
        self._preview_runner = runner
        runner.progress.connect(lambda f, _t: dialog.setValue(int(f * 1000)))
        dialog.canceled.connect(runner.cancel)

        def done(ok: bool, message: str):
            self._preview_runner = None
            dialog.reset()
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            if ok:
                PreviewDialog(message, self).show()
            elif message != "Export cancelled.":
                QMessageBox.critical(self, "Preview failed", message)

        runner.finished.connect(done)
        runner.start()

    # == settings, theme, integration, updates =====================================================

    def set_theme(self, theme: str):
        self.qsettings.setValue("view/theme", theme)
        apply_theme(QApplication.instance(), theme)

    def _toggle_shell(self, on: bool):
        try:
            if on:
                shell_integration.register()
                self.statusBar().showMessage("Right-click a video in Explorer → Open with QuickCut "
                                             "(on Windows 11 it's under “Show more options”).", 10000)
            else:
                shell_integration.unregister()
        except OSError as exc:
            QMessageBox.warning(self, "QuickCut", f"Couldn't change the Explorer menu:\n{exc}")
        self.shell_act.setChecked(shell_integration.is_registered())

    def check_updates(self):
        self.statusBar().showMessage("Checking for updates…")

        def disconnect():
            self.updates.result.disconnect(result)
            self.updates.failed.disconnect(failed)
            self.statusBar().clearMessage()

        def result(version: str, url: str):
            disconnect()
            if version:
                self._show_update(version, url)
                if QMessageBox.question(self, "Update available",
                                        f"QuickCut {version} is available (you have {__version__}). "
                                        "Open the download page?") == QMessageBox.Yes:
                    QDesktopServices.openUrl(QUrl(url))
            else:
                QMessageBox.information(self, "QuickCut", f"You have the latest version ({__version__}).")

        def failed(message: str):
            disconnect()
            QMessageBox.warning(self, "QuickCut", f"Couldn't check for updates:\n{message}")

        self.updates.result.connect(result)
        self.updates.failed.connect(failed)
        self.updates.check()

    def _update_result_quiet(self, version: str, url: str):
        self.updates.result.disconnect(self._update_result_quiet)
        if version:
            self._show_update(version, url)

    def _show_update(self, version: str, url: str):
        self.update_label.setText(f"<a href='{url}'>QuickCut {version} is available</a>")
        self.update_label.show()

    # == window ====================================================================================

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def bring_to_front(self):
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    def shutdown(self):
        """Release media and background work while Qt is still running; tearing the player down
        after QApplication is gone hangs the process on exit."""
        self.player.stop()
        self.player.setSource(QUrl())
        self.cache.pool.clear()
        self.cache.pool.waitForDone(5000)

    def closeEvent(self, e):
        if self.queue.current is not None or self.queue.waiting:
            answer = QMessageBox.question(self, "QuickCut", "Exports are still running. Cancel them and quit?")
            if answer != QMessageBox.Yes:
                e.ignore()
                return
        if not self._confirm_discard(closing=True):
            e.ignore()
            return
        self.queue.cancel_all()
        if self._preview_runner:
            self._preview_runner.cancel()
        self.panel.save_state(self.qsettings)
        self.qsettings.setValue("window/geometry", self.saveGeometry())
        self.qsettings.setValue("window/splitter", self.splitter.saveState())
        super().closeEvent(e)
