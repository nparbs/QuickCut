"""Small pure-logic tests that don't need ffmpeg."""
from __future__ import annotations

import json
import os

from quickcut.command import _atempo, output_frame
from quickcut.models import Clip, MediaInfo
from quickcut.presets import PLATFORM_PRESETS, RESET, preset_state
from quickcut.project import apply_clip_fields, load_project, save_project
from quickcut.updates import is_newer, parse_version


def _info(path="x.mp4", w=1920, h=1080):
    return MediaInfo(path, 10, w, h, "30", 30, True, "h264", 0)


def test_version_compare():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert is_newer("v0.10.0", "0.9.9") and not is_newer("v0.2.0", "0.2.0")


def test_atempo_chain_multiplies_back():
    for speed in (0.25, 0.5, 0.75, 1.5, 3.0, 4.0):
        product = 1.0
        for f in _atempo(speed):
            assert 0.5 <= f <= 2.0
            product *= f
        assert abs(product - speed) < 1e-9


def test_rotation_swaps_output_frame():
    from quickcut.models import ExportSettings
    clip = Clip(_info(), rotate=90)
    assert output_frame([clip], ExportSettings()) == (1080, 1920)
    assert output_frame([clip], ExportSettings(height=720)) == (720, 1280)


def test_presets_only_use_known_fields():
    keys = set(RESET)
    for p in PLATFORM_PRESETS.values():
        keys |= set(p)
    known = {"format", "codec", "mode", "size_mb", "fps", "preset", "audio_bitrate", "aspect", "fit",
             "resolution", "auto_res"}
    assert keys <= known
    assert preset_state("Discord (10 MB)")["size_mb"] == 10.0


def test_project_relative_paths(tmp_path):
    video = tmp_path / "media" / "clip.mp4"
    video.parent.mkdir()
    video.write_bytes(b"")
    clip = Clip(_info(str(video)), start=1, end=5, speed=2.0, rotate=270, reverse=True)
    proj = tmp_path / "p.qcut"
    save_project(str(proj), [clip], {"aspect": "9:16"})
    data = json.loads(proj.read_text())
    assert data["clips"][0]["relative"] == os.path.join("media", "clip.mp4")

    # Moving the whole folder keeps the project working through the relative path.
    moved = tmp_path.parent / (tmp_path.name + "_moved")
    tmp_path.rename(moved)
    entries, export = load_project(str(moved / "p.qcut"))
    assert not entries[0]["missing"] and entries[0]["path"] == str(moved / "media" / "clip.mp4")
    assert export == {"aspect": "9:16"}
    fresh = Clip(_info(entries[0]["path"]))
    apply_clip_fields(fresh, entries[0])
    assert (fresh.start, fresh.end, fresh.speed, fresh.rotate, fresh.reverse) == (1, 5, 2.0, 270, True)
