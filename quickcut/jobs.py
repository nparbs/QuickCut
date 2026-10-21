"""Export queue: jobs run one after another while you keep editing."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .command import ExportPlan
from .exporter import ExportRunner

WAITING, RUNNING, DONE, FAILED, CANCELLED = "Waiting", "Exporting", "Done", "Failed", "Cancelled"


@dataclass
class Job:
    plan: ExportPlan
    temp_dir: str | None = None   # rendered overlays etc., deleted when the job ends
    status: str = WAITING
    progress: float = 0.0
    detail: str = ""
    warning: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def output(self) -> str:
        return self.plan.output

    @property
    def name(self) -> str:
        return Path(self.plan.output).name


class JobQueue(QObject):
    changed = Signal()                    # list or a status changed
    progress = Signal(object)             # Job
    finished = Signal(object)             # Job (done, failed or cancelled)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs: list[Job] = []
        self.current: Job | None = None
        self._runner: ExportRunner | None = None

    @property
    def waiting(self) -> int:
        return sum(j.status == WAITING for j in self.jobs)

    def add(self, job: Job):
        self.jobs.append(job)
        self.changed.emit()
        self._next()

    def remove(self, job: Job):
        if job is self.current:
            self.cancel_current()
            return
        self.jobs.remove(job)
        self._cleanup(job)
        self.changed.emit()

    def clear_finished(self):
        self.jobs = [j for j in self.jobs if j.status in (WAITING, RUNNING)]
        self.changed.emit()

    def cancel_current(self):
        if self._runner:
            self._runner.cancel()

    def cancel_all(self):
        for j in self.jobs:
            if j.status == WAITING:
                j.status = CANCELLED
                self._cleanup(j)
        self.cancel_current()
        self.changed.emit()

    def _next(self):
        if self.current is not None:
            return
        job = next((j for j in self.jobs if j.status == WAITING), None)
        if job is None:
            return
        self.current = job
        job.status = RUNNING
        self._runner = ExportRunner(job.plan, self)
        self._runner.progress.connect(lambda f, t, job=job: self._on_progress(job, f, t))
        self._runner.finished.connect(lambda ok, msg, job=job: self._on_finished(job, ok, msg))
        self.changed.emit()
        self._runner.start()

    def _on_progress(self, job: Job, fraction: float, text: str):
        job.progress, job.detail = fraction, text
        self.progress.emit(job)

    def _on_finished(self, job: Job, ok: bool, message: str):
        runner, self._runner = self._runner, None
        self.current = None
        if ok:
            job.status, job.progress = DONE, 1.0
            job.warning = runner.warning
            size = os.path.getsize(job.output) / 1048576 if os.path.exists(job.output) else 0
            job.detail = f"{size:.1f} MB"
        else:
            job.status = CANCELLED if message == "Export cancelled." else FAILED
            job.detail = message
        self._cleanup(job)
        self.changed.emit()
        self.finished.emit(job)
        self._next()

    @staticmethod
    def _cleanup(job: Job):
        if job.temp_dir:
            shutil.rmtree(job.temp_dir, ignore_errors=True)
            job.temp_dir = None


class JobsPanel(QWidget):
    """Table of queued, running and finished exports."""
    revealRequested = Signal(str)

    def __init__(self, queue: JobQueue, parent=None):
        super().__init__(parent)
        self.queue = queue
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["File", "Status", "Progress"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 140)
        self.table.doubleClicked.connect(self._reveal)

        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setToolTip("Remove the selected job (cancels it if it's running)")
        self.clear_btn = QPushButton("Clear finished")
        self.stop_btn = QPushButton("Cancel all")
        self.show_btn = QPushButton("Show file")
        row = QHBoxLayout()
        for b in (self.show_btn, self.remove_btn, self.clear_btn, self.stop_btn):
            row.addWidget(b)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.table)
        layout.addLayout(row)

        self.remove_btn.clicked.connect(self._remove)
        self.clear_btn.clicked.connect(queue.clear_finished)
        self.stop_btn.clicked.connect(queue.cancel_all)
        self.show_btn.clicked.connect(self._reveal)
        queue.changed.connect(self.refresh)
        queue.progress.connect(self._update_progress)
        self.refresh()

    def _selected(self) -> Job | None:
        row = self.table.currentRow()
        return self.queue.jobs[row] if 0 <= row < len(self.queue.jobs) else None

    def _remove(self):
        job = self._selected()
        if job:
            self.queue.remove(job)

    def _reveal(self, *_):
        job = self._selected()
        if job and job.status == DONE:
            self.revealRequested.emit(job.output)

    def refresh(self):
        self.table.setRowCount(len(self.queue.jobs))
        for row, job in enumerate(self.queue.jobs):
            name = QTableWidgetItem(job.name)
            name.setToolTip(job.output)
            status = job.status + (" ⚠" if job.warning else "")
            status_item = QTableWidgetItem(status)
            status_item.setToolTip(job.warning or job.detail)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, status_item)
            bar = self.table.cellWidget(row, 2)
            if not isinstance(bar, QProgressBar):
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setMaximumHeight(18)
                bar.setAlignment(Qt.AlignCenter)
                self.table.setCellWidget(row, 2, bar)
            self._paint_bar(bar, job)
        self.stop_btn.setEnabled(self.queue.current is not None or self.queue.waiting > 0)

    def _update_progress(self, job: Job):
        if job in self.queue.jobs:
            bar = self.table.cellWidget(self.queue.jobs.index(job), 2)
            if isinstance(bar, QProgressBar):
                self._paint_bar(bar, job)

    @staticmethod
    def _paint_bar(bar: QProgressBar, job: Job):
        bar.setValue(int(job.progress * 1000))
        bar.setFormat(job.detail if job.status == DONE else f"{job.progress * 100:.0f}%"
                      if job.status == RUNNING else job.status)
