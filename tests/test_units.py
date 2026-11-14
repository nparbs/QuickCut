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
    clip = Clip(_info(str(video)), start=1, end=5, speed=2.0, rotate=270, reverse=True, volume=1.5, muted=True)
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
    assert (fresh.volume, fresh.muted) == (1.5, True)

    # Projects saved before per-clip volume existed load with the defaults.
    old = {k: v for k, v in entries[0].items() if k not in ("volume", "muted")}
    older = Clip(_info(entries[0]["path"]))
    apply_clip_fields(older, old)
    assert (older.volume, older.muted) == (1.0, False)


def _spans(parts):
    return [(round(p.start, 3), round(p.end, 3)) for p in parts]


def test_split_into_even_parts():
    from quickcut.command import split_into_parts
    parts = split_into_parts(Clip(MediaInfo("x.mp4", 20, 1920, 1080, "30", 30, True, "h264", 0)), 5)
    assert _spans(parts) == [(0, 5), (5, 10), (10, 15), (15, 20)]


def test_split_with_start_time_and_leftover():
    from quickcut.command import split_into_parts
    clip = Clip(MediaInfo("x.mp4", 30, 1920, 1080, "30", 30, True, "h264", 0))
    parts = split_into_parts(clip, 7, start=10)
    assert _spans(parts) == [(10, 17), (17, 24), (24, 30)]
    assert [round(p.out_length, 3) for p in parts] == [7, 7, 6]


def test_split_max_parts_and_edge_cases():
    from quickcut.command import split_into_parts
    clip = Clip(MediaInfo("x.mp4", 30, 1920, 1080, "30", 30, True, "h264", 0))
    assert _spans(split_into_parts(clip, 7, start=10, max_parts=2)) == [(10, 17), (17, 24)]
    assert split_into_parts(clip, 5, start=30) == []
    # A leftover under 0.1 s joins the last part instead of being its own file.
    assert _spans(split_into_parts(clip, 9.98)) == [(0, 9.98), (9.98, 19.96), (19.96, 30)]
    # A clip shorter than one part comes out whole.
    assert _spans(split_into_parts(clip, 60)) == [(0, 30)]


def test_split_respects_trim_speed_and_reverse():
    from quickcut.command import split_into_parts
    info = MediaInfo("x.mp4", 30, 1920, 1080, "30", 30, True, "h264", 0)
    # Trimmed to 10-20; times count from the trim start.
    assert _spans(split_into_parts(Clip(info, start=10, end=20), 4, start=1)) == [(11, 15), (15, 19), (19, 20)]
    # At 2x, a 5 s part covers 10 s of source.
    fast = split_into_parts(Clip(info, speed=2.0), 5)
    assert _spans(fast) == [(0, 10), (10, 20), (20, 30)]
    assert all(abs(p.out_length - 5) < 1e-9 for p in fast)
    # Reversed clips play from the end, so the first part is the end of the source.
    assert _spans(split_into_parts(Clip(info, reverse=True), 10)) == [(20, 30), (10, 20), (0, 10)]


def test_part_file_names():
    from quickcut.split_dialog import part_filename, part_time
    assert part_time(10) == "00m10.0s" and part_time(59.96) == "01m00.0s"
    assert part_time(3725.5) == "1h02m05.5s"
    info = MediaInfo("x.mp4", 30, 1920, 1080, "30", 30, True, "h264", 0)
    assert part_filename("clip", 3, 120, Clip(info, start=24, end=30), ".mp4") == "clip_003_00m24.0s-00m30.0s.mp4"
