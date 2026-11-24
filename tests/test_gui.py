"""Drives the real main window: editing, undo, presets, projects, queue, preview, overlays."""
from __future__ import annotations

import csv
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

from quickcut import export_panel as ep, main_window as mw  # noqa: E402
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


def test_clip_volume_and_mute(win, media):
    win.add_files([media["a"], media["b"]])
    win.list.setCurrentRow(0)
    before = len(win._undo)
    for v in (90, 80, 70, 60, 50):  # spinning the box is one undo step
        win.clip_volume.setValue(v)
    assert len(win._undo) == before + 1
    assert win._ordered()[0].volume == 0.5 and win.audio.volume() == pytest.approx(0.5, abs=0.01)
    win.clip_mute.setChecked(True)
    assert win._ordered()[0].muted and not win.clip_volume.isEnabled() and win.audio.volume() == 0
    assert "muted" in win.list.item(0).text()
    win.undo()
    assert not win._ordered()[0].muted and win._ordered()[0].volume == 0.5
    assert "vol 50%" in win.list.item(0).text()
    win.list.setCurrentRow(1)  # clip without sound
    assert not win.clip_mute.isEnabled() and not win.clip_volume.isEnabled()
    assert win.clip_volume.value() == 100


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
    win.layers_panel.add_text()
    win.layers_panel.text.setPlainText("Hello\nQuickCut")
    win.layers_panel.add_image(media["logo"])
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


def test_autosave_and_recovery(win, media, monkeypatch):
    path = mw.autosave_path()
    win.add_files([media["a"], media["b"]])
    win.list.setCurrentRow(0)
    win._set_trim(start=1.0, end=3.0)
    assert win._dirty
    win._autosave()
    assert os.path.isfile(path)

    # A crash leaves the file behind; declining throws it away.
    second = mw.MainWindow(TOOLS)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    second.offer_recovery()
    assert not os.path.exists(path) and not second.clips
    win._autosave()

    # Accepting brings the clips and trim back as unsaved work, without touching recent projects.
    recent = second._recent()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    second.offer_recovery()
    assert len(second.clips) == 2 and second._dirty and second.project_path is None
    assert second._ordered()[0].start == 1.0 and second._ordered()[0].end == 3.0
    assert second._recent() == recent
    assert os.path.isfile(path)  # kept until the work is saved or the app closes cleanly

    # A clean close removes it, so the next start asks nothing.
    second._dirty = False
    second.close()
    assert not os.path.exists(path)
    win._dirty = False


def test_autosave_remembers_the_project_and_saving_clears_it(win, media, monkeypatch):
    path = mw.autosave_path()
    project = os.path.join(media["dir"], "autosave_test.qcut")
    win.add_files([media["a"]])
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (project, "")))
    assert win.save_project_as()
    assert not os.path.exists(path)

    win.list.setCurrentRow(0)
    win._set_trim(start=1.0, end=3.0)
    win._autosave()
    second = mw.MainWindow(TOOLS)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    second.offer_recovery()
    assert second.project_path == project and second._dirty
    assert second.save_project()
    assert not os.path.exists(path)
    second.close()
    win._dirty = False


def test_nothing_to_autosave_writes_nothing(win):
    win._autosave()
    assert not os.path.exists(mw.autosave_path())


def test_saved_presets(win, app, monkeypatch):
    panel = win.panel
    panel.set_state({"format": ".mkv", "mode": "size", "size_mb": 77.0, "fade_in": 1.5})
    monkeypatch.setattr(ep.QInputDialog, "getText", staticmethod(lambda *a, **k: ("  My clips ", True)))
    panel._ask_save_preset()
    assert panel.platform.currentText() == "My clips"
    assert panel.delete_preset_btn.isEnabled()

    # A built-in preset changes everything; picking ours brings it all back.
    panel.platform.setCurrentText("Discord (10 MB)")
    assert panel.size_mb.value() == 10.0 and not panel.delete_preset_btn.isEnabled()
    panel.platform.setCurrentText("My clips")
    state = panel.get_state()
    assert (state["format"], state["size_mb"], state["fade_in"]) == (".mkv", 77.0, 1.5)

    # Saved across restarts, along with which preset was selected.
    q = mw.app_settings()
    panel.save_state(q)
    reopened = ep.ExportPanel()
    reopened.restore_state(q)
    assert reopened.platform.currentText() == "My clips"
    assert reopened.user_presets["My clips"]["size_mb"] == 77.0

    # Editing a setting drops back to Custom, and the saved preset stays as it was.
    panel.size_mb.setValue(5.0)
    assert panel.platform.currentText() == ep.CUSTOM
    assert not panel.delete_preset_btn.isEnabled()
    assert panel.user_presets["My clips"]["size_mb"] == 77.0

    # Built-in names can't be taken, and answering No keeps an existing preset.
    monkeypatch.setattr(ep.QInputDialog, "getText", staticmethod(lambda *a, **k: ("Discord (10 MB)", True)))
    panel._ask_save_preset()
    assert "Discord (10 MB)" not in panel.user_presets
    monkeypatch.setattr(ep.QInputDialog, "getText", staticmethod(lambda *a, **k: ("My clips", True)))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    panel._ask_save_preset()
    assert panel.user_presets["My clips"]["size_mb"] == 77.0

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    panel.platform.setCurrentText("My clips")
    panel._ask_delete_preset()
    assert panel.platform.currentText() == ep.CUSTOM
    assert panel.platform.findText("My clips") < 0
    assert ep.ExportPanel().user_presets == {}


def test_damaged_saved_presets_are_ignored(app):
    q = mw.app_settings()
    q.setValue("presets/user", "not json")
    assert ep.ExportPanel().user_presets == {}
    q.setValue("presets/user", '["a list"]')
    assert ep.ExportPanel().user_presets == {}
    q.remove("presets/user")


def test_export_each_clip_separately(win, media, monkeypatch):
    out_dir = tempfile.mkdtemp(prefix="quickcut_batch_")
    win.add_files([media["a"], media["b"], media["a"]])
    win.panel.set_state({"format": ".mkv", "mode": "preset", "aspect": ""})
    monkeypatch.setattr(mw.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: out_dir))
    win.export_separately()
    assert len(win.queue.jobs) == 3
    _run_queue(win)
    names = sorted(os.listdir(out_dir))
    assert names == ["a_edit.mkv", "a_edit_2.mkv", "b_edit.mkv"]


def test_split_into_parts(win, media, monkeypatch):
    from quickcut import split_dialog as sd
    out_dir = tempfile.mkdtemp(prefix="quickcut_parts_")
    win.add_files([media["a"]])   # 4 s
    win.panel.set_state({"format": ".mkv", "mode": "preset", "aspect": ""})
    monkeypatch.setattr(mw.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: out_dir))

    def run(start, seconds, most, preset):
        def fake_exec(dialog):
            dialog.start.setValue(start)
            dialog.seconds.setValue(seconds)
            dialog.max_parts.setValue(most)
            dialog.preset.setCurrentIndex(dialog.preset.findData(preset))
            return sd.SplitDialog.Accepted
        monkeypatch.setattr(sd.SplitDialog, "exec", fake_exec)
        win.export_parts()
        _run_queue(win)

    # Start 1 s in, 1.2 s parts: 1.2, 1.2 and the 0.6 s left over, in a folder of their own with
    # the order and source times in each name.
    run(1.0, 1.2, 0, sd.CURRENT)
    folder = os.path.join(out_dir, "a_parts")
    names = sorted(os.listdir(folder))
    assert names == ["a_01_00m01.0s-00m02.2s.mkv", "a_02_00m02.2s-00m03.4s.mkv",
                     "a_03_00m03.4s-00m04.0s.mkv", "parts.csv"]
    lengths = [probe(TOOLS, os.path.join(folder, n)).duration for n in names[:3]]
    assert [round(x, 1) for x in lengths] == [1.2, 1.2, 0.6]
    with open(os.path.join(folder, "parts.csv"), encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["file"] for r in rows] == names[:3]
    assert (rows[0]["order"], rows[0]["start_seconds"], rows[2]["end_seconds"]) == ("1", "1.000", "4.000")

    # Running it again makes a new folder instead of mixing sets.
    run(1.0, 1.2, 0, sd.CURRENT)
    assert len(os.listdir(os.path.join(out_dir, "a_parts_2"))) == 4

    # A preset just for the parts, and a limit on how many.
    run(0.0, 1.0, 2, "GIF (small, 480p)")
    gifs = sorted(os.listdir(os.path.join(out_dir, "a_parts_3")))
    assert gifs == ["a_01_00m00.0s-00m01.0s.gif", "a_02_00m01.0s-00m02.0s.gif", "parts.csv"]
    gif = probe(TOOLS, os.path.join(out_dir, "a_parts_3", gifs[0]))
    assert min(gif.width, gif.height) == 480
    # The export panel itself is untouched, and the dialog remembers the choices.
    assert win.panel.extension == ".mkv" and win.panel.platform.currentText() == ep.CUSTOM
    assert win.qsettings.value("split/preset") == "GIF (small, 480p)"
    assert win.qsettings.value("split/max_parts", type=int) == 2


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
    win.panel.set_state({"transition_on": True, "format": ".mp4", "mode": "size"})
    win.layers_panel.add_text()
    win.preview_edit()
    wait_for(lambda: shown, timeout=60)
    info = probe(TOOLS, shown[0])
    assert min(info.width, info.height) <= 360 and info.duration > 6
    os.remove(shown[0])


def test_gif_and_audio_formats_from_ui(win, media, monkeypatch):
    win.add_files([media["a"]])
    for ext in (".gif", ".mp3"):
        out = os.path.join(media["dir"], f"ui_out{ext}")
        win.panel.set_state({"format": ext, "resolution": 240})
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
    server.close()


LAUNCHER = """
import sys, time
from PySide6.QtCore import QCoreApplication
app = QCoreApplication(sys.argv)
from quickcut.single_instance import become_primary
server = become_primary([sys.argv[1]])
if server is None:
    print("handed-off", flush=True)
    sys.exit(0)
got = []
server.filesReceived.connect(got.extend)
end = time.time() + 6
while time.time() < end:
    app.processEvents()
    time.sleep(0.02)
print("primary", len(got), flush=True)
server.close()
"""


def test_simultaneous_launches_open_one_window(app, tmp_path):
    """Explorer starts one process per selected file at the same moment; only one may win."""
    script = tmp_path / "launch.py"
    script.write_text(LAUNCHER, encoding="utf-8")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=root)
    procs = [subprocess.Popen([sys.executable, str(script), f"file{i}.mp4"], stdout=subprocess.PIPE,
                              text=True, env=env) for i in range(4)]
    outputs = sorted(p.communicate(timeout=60)[0].strip() for p in procs)
    # One window; the other three launches hand their files to it over the pipe.
    assert outputs == ["handed-off"] * 3 + ["primary 3"], outputs


def test_framing_in_the_player(win, media):
    from quickcut.framing import fit_zoom
    win.add_files([media["a"]])      # 1280x720
    win.panel.set_state({"aspect": "9:16", "fit": "pad", "mode": "preset", "format": ".mp4", "resolution": 0})
    clip = win._ordered()[0]
    assert win.view._frame == (720, 1280) and win.framing_row.isVisible()
    assert "(auto)" in win.zoom_label.text()
    # Dragging in the player moves the clip; the end of the drag is one undo step.
    for x in (0.4, 0.3, 0.2):
        win.view.framingEdited.emit(1.0, x, 0.5, False)
    win.view.framingEdited.emit(1.0, 0.2, 0.5, True)
    assert clip.zoom == 1.0 and clip.pan_x == 0.2
    assert "framed 100%" in win.list.item(0).text()
    win.undo()
    assert win._ordered()[0].zoom is None
    win.redo()
    win._frame_fit()
    clip = win._ordered()[0]
    assert abs(clip.zoom - fit_zoom(1280, 720, 720, 1280)) < 1e-9 and clip.pan_x == 0.5
    win.view.framingResetRequested.emit()     # double-click
    assert win._ordered()[0].zoom is None
    # No re-encode can't frame, so the player goes back to showing the plain clip.
    win.panel.set_state({"mode": "copy"})
    assert win.view._frame is None and not win.framing_row.isVisible()


def test_framed_export_from_ui(win, media, monkeypatch):
    win.add_files([media["a"], media["b"]])
    win.panel.set_state({"aspect": "1:1", "fit": "blur", "mode": "preset", "format": ".mp4", "resolution": 360})
    win.list.setCurrentRow(1)
    win._set_framing(1.6, (0.3, 0.6))
    win._framing_to_all()
    assert all(c.zoom == 1.6 for c in win._ordered())
    out = os.path.join(media["dir"], "gui_framed.mp4")
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "")))
    win.export()
    _run_queue(win)
    assert win.queue.jobs[-1].status == DONE, win.queue.jobs[-1].detail
    info = probe(TOOLS, out)
    assert (info.width, info.height) == (360, 360)


def test_layers_edit_undo_and_save(win, media, monkeypatch):
    lp = win.layers_panel
    win.add_files([media["a"]])
    lp.add_text()
    lp.text.setPlainText("Title")
    lp.add_image(media["logo"])
    assert [layer.kind for layer in win.layers] == ["text", "image"]
    assert lp.list.item(0).text().endswith("logo.png")     # front layer listed first
    # Dragging a layer in the player.
    win.layers[1].x = 0.25
    win.view.layerEdited.emit(1, 0.25, 0.85, 18.0, True)
    win.undo()
    assert win.layers[1].x == 0.82
    lp.move(-1)                                          # send the picture behind the text
    assert [layer.kind for layer in win.layers] == ["image", "text"]
    lp.select(1)
    lp.start.setValue(1.0)
    lp.duration.setValue(1.0)
    monkeypatch.setattr(win, "_playhead", lambda: 0.5)
    win._update_layer_times()
    assert win.view._layer_active == [True, False]
    path = os.path.join(media["dir"], "layers.qcut")
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (path, "")))
    assert win.save_project_as()
    saved = list(win.layers)
    win.new_project()
    assert win.layers == [] and lp.list.count() == 0
    win.open_project(path)
    assert win.layers == saved and lp.list.count() == 2
    lp.select(0)
    win.left_tabs.setCurrentIndex(1)
    win._delete_key()
    assert len(win.layers) == 1 and win.list.count() == 1   # Delete on the Layers tab removes the layer


def test_old_project_text_becomes_a_layer(win, media):
    import json
    path = os.path.join(media["dir"], "old.qcut")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"app": "QuickCut", "format": 1, "clips": [{"path": media["a"]}],
                   "export": {"text": "Old caption", "text_pos": "top", "logo": media["logo"]}}, f)
    win.open_project(path)
    assert [layer.kind for layer in win.layers] == ["image", "text"]
    assert win.layers[1].text == "Old caption"


def test_failed_export_keeps_the_existing_file(app, media):
    import dataclasses
    from quickcut.command import build_plan
    from quickcut.exporter import ExportRunner
    from quickcut.models import Clip, ExportSettings
    out = os.path.join(media["dir"], "keep.mp4")
    with open(out, "wb") as f:
        f.write(b"precious")
    good = build_plan(TOOLS.ffmpeg, [Clip(probe(TOOLS, media["a"]))], ExportSettings(output=out, mode="preset"))
    bad = dataclasses.replace(good, passes=[good.passes[0][:-1] + ["-no-such-option", out]], fallback=None)

    def run(plan):
        result = []
        runner = ExportRunner(plan)
        runner.finished.connect(lambda ok, msg: result.append(ok))
        runner.start()
        wait_for(lambda: result)
        return result[0]

    assert run(bad) is False
    with open(out, "rb") as f:
        assert f.read() == b"precious"
    assert run(good) is True
    assert probe(TOOLS, out).width == 1280
    assert not [n for n in os.listdir(media["dir"]) if "quickcut-partial" in n]
