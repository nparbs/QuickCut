"""End-to-end tests: build plans and run them through the real ffmpeg."""
from __future__ import annotations

import os
import subprocess
import tempfile

import pytest

from quickcut.command import build_plan, build_filtergraph, output_duration, retry_plan_if_too_big
from quickcut.ffmpeg import detect_encoders, find_tools, probe
from quickcut.models import Clip, ExportSettings

TOOLS = find_tools()
pytestmark = pytest.mark.skipif(TOOLS is None, reason="ffmpeg not installed")


def _make(path, size, rate, seconds, audio=True):
    args = [TOOLS.ffmpeg, "-v", "error", "-y", "-f", "lavfi",
            "-i", f"testsrc2=s={size}:r={rate}:d={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:a", "aac"]
    subprocess.run(args + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", path], check=True)
    return path


@pytest.fixture(scope="module")
def media():
    d = tempfile.mkdtemp(prefix="quickcut_test_")
    return {
        "dir": d,
        "a": _make(os.path.join(d, "a.mp4"), "1280x720", 30, 6),
        "b": _make(os.path.join(d, "b.mp4"), "640x480", 25, 4, audio=False),
        "c": _make(os.path.join(d, "c.mp4"), "1920x1080", 60, 5),
    }


@pytest.fixture(scope="module")
def encoders():
    return {e.name for e in detect_encoders(TOOLS)}


def _export(media, clips, **kw):
    out = os.path.join(media["dir"], kw.pop("name", "out") + kw.pop("ext", ".mp4"))
    s = ExportSettings(output=out, **kw)
    plan = build_plan(TOOLS.ffmpeg, clips, s)
    first = plan
    for _ in range(3):  # mirrors ExportRunner's retry when a size-targeted export is too big
        for cmd in plan.passes:
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=media["dir"])
            assert r.returncode == 0, r.stderr
        plan = retry_plan_if_too_big(plan, os.path.getsize(out))
        if plan is None:
            break
    return probe(TOOLS, out), first


def _clips(media, *names):
    return [Clip(probe(TOOLS, media[n])) for n in names]


def test_probe(media):
    info = probe(TOOLS, media["a"])
    assert (info.width, info.height, info.fps_value) == (1280, 720, 30)
    assert info.has_audio and abs(info.duration - 6) < 0.1
    assert not probe(TOOLS, media["b"]).has_audio


def test_trim_single_clip(media):
    clip = Clip(probe(TOOLS, media["a"]), start=1.5, end=4.0)
    info, _ = _export(media, [clip], name="trim")
    assert abs(info.duration - 2.5) < 0.1
    assert (info.width, info.height) == (1280, 720)


def test_fps_and_resolution(media):
    info, _ = _export(media, _clips(media, "c"), name="res", height=720, fps="24")
    assert (info.width, info.height) == (1280, 720)
    assert info.fps_value == 24


def test_concat_mixed_sources(media):
    clips = _clips(media, "a", "b", "c")
    info, _ = _export(media, clips, name="concat")
    assert abs(info.duration - 15) < 0.2
    assert info.has_audio and (info.width, info.height) == (1280, 720)


def test_crossfade(media):
    clips = _clips(media, "a", "b", "c")
    s = ExportSettings(crossfade=1.0)
    info, _ = _export(media, clips, name="xfade", crossfade=1.0)
    assert abs(info.duration - output_duration(clips, s)) < 0.2
    assert abs(info.duration - 13) < 0.2


def test_mute_and_volume(media):
    info, _ = _export(media, _clips(media, "a"), name="mute", mute=True)
    assert not info.has_audio
    info, _ = _export(media, _clips(media, "a"), name="vol", volume=0.5)
    assert info.has_audio


def test_target_size_two_pass(media):
    info, plan = _export(media, _clips(media, "a", "c"), name="size",
                         mode="size", target_size_mb=1.0, encoder="libx264")
    assert len(plan.passes) == 2
    assert info.size_bytes <= 1024 * 1024


@pytest.mark.parametrize("encoder", ["libx265", "libsvtav1", "h264_nvenc", "hevc_nvenc",
                                     "av1_nvenc", "h264_amf", "h264_qsv"])
@pytest.mark.parametrize("mode", ["preset", "size", "manual"])
def test_encoders(media, encoders, encoder, mode):
    if encoder not in encoders:
        pytest.skip(f"{encoder} not usable on this machine")
    info, _ = _export(media, _clips(media, "a"), name=f"{encoder}_{mode}", encoder=encoder,
                      mode=mode, target_size_mb=1.0, manual_rate="bitrate" if mode == "manual" else "quality",
                      manual_bitrate_k=1500, ext=".mkv" if mode == "manual" else ".mp4")
    assert abs(info.duration - 6) < 0.2
    if mode == "size":
        assert info.size_bytes <= 1024 * 1024


def test_filtergraph_has_single_outputs():
    from quickcut.models import MediaInfo
    info = MediaInfo("x.mp4", 10, 1920, 1080, "30", 30, True, "h264", 0)
    g = build_filtergraph([Clip(info), Clip(info)], 1920, 1080, "30", 0.5, audio=True)
    assert g.count("[vout]") == 1 and g.count("[aout]") == 1 and "xfade" in g


@pytest.mark.parametrize("encoder", ["libx264", "h264_nvenc", "av1_nvenc"])
def test_crossfade_output_is_420_and_fits_size(media, encoders, encoder):
    # xfade negotiates yuv444p by default, which av1_nvenc rejects and h264_nvenc encodes
    # as a much heavier 4:4:4 stream that blows through the size target.
    if encoder not in encoders:
        pytest.skip(f"{encoder} not usable on this machine")
    clips = _clips(media, "a", "b", "c")
    info, _ = _export(media, clips, name=f"xfade420_{encoder}", encoder=encoder, mode="size",
                      target_size_mb=2, height=720, crossfade=0.5)
    pix_fmt = subprocess.run([TOOLS.ffprobe, "-v", "error", "-select_streams", "v",
                              "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", info.path],
                             capture_output=True, text=True).stdout.strip()
    assert pix_fmt == "yuv420p"
    assert info.size_bytes <= 2 * 1024 * 1024
