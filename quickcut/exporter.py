"""Runs an ExportPlan's ffmpeg passes with QProcess and reports progress."""
from __future__ import annotations

import os
import shutil
import tempfile

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from .command import ExportPlan, retry_plan_if_too_big

MAX_SIZE_RETRIES = 2


class ExportRunner(QObject):
    progress = Signal(float, str)     # 0..1, status text
    finished = Signal(bool, str)      # success, message (output path or error)

    def __init__(self, plan: ExportPlan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self._pass = 0
        self._retries = 0
        self._cancelled = False
        self._stderr = ""
        self._buffer = ""
        self._workdir = tempfile.mkdtemp(prefix="quickcut_")
        self._proc: QProcess | None = None
        self.warning = ""

    def start(self):
        self._pass = 0
        self._run_pass()

    def cancel(self):
        self._cancelled = True
        if self._proc and self._proc.state() != QProcess.NotRunning:
            self._proc.kill()

    def _status(self) -> str:
        parts = []
        if len(self.plan.passes) > 1:
            parts.append(f"Pass {self._pass + 1} of {len(self.plan.passes)}")
        if self._retries:
            parts.append(f"retry {self._retries} (file was over the size limit)")
        return " · ".join(parts)

    def _run_pass(self):
        cmd = self.plan.passes[self._pass]
        self._stderr = ""
        self._buffer = ""
        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("SVT_LOG", "1")  # SVT-AV1 prints a banner to stderr unless told not to
        proc.setProcessEnvironment(env)
        proc.setWorkingDirectory(self._workdir)
        proc.setProgram(cmd[0])
        proc.setArguments(cmd[1:])
        proc.readyReadStandardOutput.connect(self._read_stdout)
        proc.readyReadStandardError.connect(self._read_stderr)
        proc.finished.connect(self._pass_finished)
        proc.errorOccurred.connect(self._process_error)
        self._proc = proc
        self.progress.emit(self._pass / len(self.plan.passes), self._status())
        proc.start()

    def _read_stdout(self):
        self._buffer += bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        *lines, self._buffer = self._buffer.split("\n")
        done = None
        speed = ""
        for line in lines:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.lstrip("-").isdigit():
                done = int(value) / 1_000_000
            elif key == "speed" and value not in ("N/A", ""):
                speed = value
        if done is not None and self.plan.duration > 0:
            frac = min(1.0, max(0.0, done / self.plan.duration))
            overall = (self._pass + frac) / len(self.plan.passes)
            status = " · ".join(p for p in (self._status(), f"{speed} speed" if speed else "") if p)
            self.progress.emit(overall, status)

    def _read_stderr(self):
        self._stderr += bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")
        self._stderr = self._stderr[-8000:]

    def _process_error(self, error):
        if error == QProcess.FailedToStart:
            self._finish(False, "Could not start ffmpeg.")

    def _pass_finished(self, code: int, status):
        if self._cancelled:
            self._remove_output()
            self._finish(False, "Export cancelled.")
            return
        if code != 0 or status != QProcess.NormalExit:
            self._remove_output()
            message = self._stderr.strip() or f"ffmpeg exited with code {code}"
            self._finish(False, message[-2000:])
            return
        self._pass += 1
        if self._pass < len(self.plan.passes):
            self._run_pass()
            return

        size = os.path.getsize(self.plan.output) if os.path.exists(self.plan.output) else 0
        retry = retry_plan_if_too_big(self.plan, size)
        if retry is not None:
            if self._retries < MAX_SIZE_RETRIES:
                self._retries += 1
                self.plan = retry
                self._pass = 0
                self._run_pass()
                return
            self.warning = (f"The file is {size / 1048576:.2f} MB, over the "
                            f"{self.plan.target_bytes / 1048576:.2f} MB target. The encoder can't go lower "
                            "at this resolution; try a lower resolution or frame rate.")
        self._finish(True, self.plan.output)

    def _remove_output(self):
        try:
            if os.path.exists(self.plan.output):
                os.remove(self.plan.output)
        except OSError:
            pass

    def _finish(self, ok: bool, message: str):
        shutil.rmtree(self._workdir, ignore_errors=True)
        self.finished.emit(ok, message)
