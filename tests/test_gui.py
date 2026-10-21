"""Drives the real main window: editing, undo, presets, projects, queue, preview, overlays."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest

_settings_dir = tempfile.mkdtemp(prefix="quickcut_settings_")
os.environ["QUICKCUT_SETTINGS"] = os.path.join(_settings_dir, "settings.ini")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no windows popping up during test runs

from PySide6.QtCore import QDeadlineTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from quickcut import main_window as mw  # noqa: E402
from quickcut.ffmpeg import find_tools, probe  # noqa: E402
from quickcut.jobs import DONE  # noqa: E402

TOOLS = find_tools()
pytestmark = pytest.mark.skipif(TOOLS is None, reason="ffmpeg not installed")


def wait_for(cond, timeout=60.0):
    deadline = QDeadlineTimer(int(timeout * 1000))
    while not cond():
        if deadline.hasExpired():
            raise AssertionError("timed out waiting")
        QTest.qWait(50)


def _make(path, size, seconds, audio=True):
    args = [TOOLS.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30:d={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:a", "aac"]
    subprocess.run(args + ["-c:v", "libx264", "-g", "15", "-pix_fmt", "yuv420p", "-shortest", path], check=True)
    return path


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(scope="module")
def media():
    d = tempfile.mkdtemp(prefix="quickcut_gui_")
    logo = os.path.join(d, "logo.png")
    subprocess.run([TOOLS.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=orange:s=200x100",
                    "-frames:v", "1", logo], check=True)
    return {"dir": d, "logo": logo,
            "a": _make(os.path.join(d, "a.mp4"), "1280x720", 4),
            "b": _make(os.path.join(d, "b.mp4"), "640x480", 3, audio=False)}


@pytest.fixture
def win(app, monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: pytest.fail(str(a[2]))))
    w = mw.MainWindow(TOOLS)
    w.show()
    wait_for(lambda: w.panel.encoder.count() > 0)
    yield w
    w._dirty = False
    w.queue.cancel_all()
    w.close()


def _run_queue(w):
    wait_for(lambda: w.queue.current is None and w.queue.waiting == 0, timeout=120)


def test_split_duplicate_undo_redo(win, media, monkeypatch):
    win.add_files([media["a"], media["b"]])
    assert win.list.count() == 2
    win.list.setCurrentRow(0)
    monkeypatch.setattr(win, "_playhead", lambda: 1.5)
    win.split_clip()
    clips = win._ordered()
    assert len(clips) == 3 and clips[0].end == 1.5 and clips[1].start == 1.5
    win.duplicate_clip()
    assert win.list.count() == 4
    win.undo()
    assert win.list.count() == 3
    win.undo()
    assert win.list.count() == 2 and win._ordered()[0].end == 4.0
    win.redo()
    assert win.list.count() == 3
    # Thumbnails arrive in the background.
    wait_for(lambda: not win.list.item(0).icon().isNull(), timeout=20)


def test_trim_edits_coalesce_into_one_undo(win, media):
    win.add_files([media["a"]])
    before = len(win._undo)
    for t in (0.5, 0.6, 0.7, 0.8):
        win._set_trim(start=t)
    assert len(win._undo) == before + 1
    win.undo()
    assert win._ordered()[0].start == 0.0


def test_clip_effects_and_preset_export_with_overlays(win, media, monkeypatch):
    win.add_files([media["a"], media["b"]])
    win.list.setCurrentRow(0)
    win.speed.setCurrentIndex(win.speed.findData(2.0))
    win.list.setCurrentRow(1)
    win._rotate(90)
    p = win.panel
    p.platform.setCurrentText("Shorts / Reels / TikTok (9:16)")
    assert p.aspect.currentData() == "9:16" and p.fit.currentData() == "blur"
    assert "1080×1920" in p.summary.text()
    p.text.setPlainText("Hello\nQuickCut")
    p.logo.set_path(media["logo"])
    p.transition_on.setChecked(True)
    p.fade_out.setValue(0.5)
    assert p.platform.currentText() == "Custom"  # changing anything leaves the preset

    out = os.path.join(media["dir"], "gui_vertical.mp4")
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "")))
    win.export()
    _run_queue(win)
    job = win.queue.jobs[-1]
    assert job.status == DONE, job.detail
    info = probe(TOOLS, out)
    assert (info.width, info.height) == (1080, 1920)
    assert abs(info.duration - (2 + 3 - 0.5)) < 0.2   # 4 s at 2x, 3 s clip, 0.5 s transition


def test_export_each_clip_separately(win, media, monkeypatch):
    out_dir = tempfile.mkdtemp(prefix="quickcut_batch_")
    win.add_files([media["a"], media["b"], media["a"]])
    win.panel.set_state({"format": ".mkv", "mode": "preset", "aspect": "", "text": "", "logo": ""})
    monkeypatch.setattr(mw.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: out_dir))
    win.export_separately()
    assert len(win.queue.jobs) == 3
    _run_queue(win)
    names = sorted(os.listdir(out_dir))
    assert names == ["a_edit.mkv", "a_edit_2.mkv", "b_edit.mkv"]


def test_project_roundtrip(win, media, monkeypatch):
    win.add_files([media["a"], media["b"]])
    win.list.setCurrentRow(0)
    win._set_trim(start=1.0, end=3.0)
    win.speed.setCurrentIndex(win.speed.findData(0.5))
    win.panel.set_state({"aspect": "1:1", "fade_in": 1.0})
    path = os.path.join(media["dir"], "test.qcut")
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (path, "")))
    assert win.save_project_as()
    assert win.windowTitle().startswith("test —")
    win.new_project()
    assert win.list.count() == 0 and win.project_path is None
    win.open_project(path)
    clips = win._ordered()
    assert len(clips) == 2 and (clips[0].start, clips[0].end, clips[0].speed) == (1.0, 3.0, 0.5)
    assert win.panel.aspect.currentData() == "1:1" and win.panel.fade_in.value() == 1.0
    assert path in win._recent()


def test_preview_render(win, media, monkeypatch):
    shown = []
    monkeypatch.setattr(mw, "PreviewDialog", lambda path, parent: type(
        "D", (), {"show": lambda self: shown.append(path)})())
    win.add_files([media["a"], media["b"]])
    win.panel.set_state({"transition_on": True, "text": "Preview", "format": ".mp4", "mode": "size"})
    win.preview_edit()
    wait_for(lambda: shown, timeout=60)
    info = probe(TOOLS, shown[0])
    assert min(info.width, info.height) <= 360 and info.duration > 6
    os.remove(shown[0])


def test_gif_and_audio_formats_from_ui(win, media, monkeypatch):
    win.add_files([media["a"]])
    for ext in (".gif", ".mp3"):
        out = os.path.join(media["dir"], f"ui_out{ext}")
        win.panel.set_state({"format": ext, "text": "", "logo": "", "resolution": 240})
        monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, o=out, **k: (o, "")))
        win.export()
        _run_queue(win)
        assert os.path.getsize(out) > 0


def test_themes(win, app):
    for theme in ("light", "dark", "system"):
        win.set_theme(theme)
        assert app.property("quickcut_dark") in (True, False)


def test_single_instance_handoff(app, media):
    from quickcut.single_instance import InstanceServer
    server = InstanceServer()
    got = []
    server.filesReceived.connect(got.extend)
    code = ("import sys; from PySide6.QtCore import QCoreApplication; a = QCoreApplication(sys.argv); "
            "from quickcut.single_instance import send_to_running; "
            f"sys.exit(0 if send_to_running([{media['a']!r}], 5000) else 3)")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=os.path.dirname(os.path.dirname(__file__)))
    wait_for(lambda: proc.poll() is not None and got, timeout=20)
    assert proc.returncode == 0 and got == [media["a"]]
    server.server.close()
