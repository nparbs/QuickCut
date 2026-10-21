"""End-to-end tests: build plans and run them through the real ffmpeg."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

import pytest

from quickcut.command import (
    build_filtergraph, build_plan, copy_mode_problems, output_duration, output_frame, output_kind,
    retry_plan_if_too_big,
)
from quickcut.ffmpeg import detect_encoders, find_tools, probe
from quickcut.models import Clip, ExportSettings, Overlay

TOOLS = find_tools()
pytestmark = pytest.mark.skipif(TOOLS is None, reason="ffmpeg not installed")


def _make(path, size, rate, seconds, audio=True, gop=250):
    args = [TOOLS.ffmpeg, "-v", "error", "-y", "-f", "lavfi",
            "-i", f"testsrc2=s={size}:r={rate}:d={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:a", "aac"]
    subprocess.run(args + ["-c:v", "libx264", "-g", str(gop), "-pix_fmt", "yuv420p", "-shortest", path],
                   check=True)
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
        for name, text in plan.files.items():
            with open(os.path.join(media["dir"], name), "w", encoding="utf-8") as f:
                f.write(text)
        for cmd in plan.passes:
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=media["dir"])
            assert r.returncode == 0, r.stderr
        plan = retry_plan_if_too_big(plan, os.path.getsize(out))
        if plan is None:
            break
    return (probe(TOOLS, out) if output_kind(out) != "audio" else out), first


def _streams(path):
    r = subprocess.run([TOOLS.ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                        "-show_streams", path], capture_output=True, text=True)
    return json.loads(r.stdout)


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
    g = build_filtergraph([Clip(info), Clip(info)], ExportSettings(crossfade=0.5), 1920, 1080, "30")
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


# -- editing ----------------------------------------------------------------------------------------

def test_speed_and_reverse(media):
    fast = Clip(probe(TOOLS, media["a"]), speed=2.0)
    slow = Clip(probe(TOOLS, media["b"]), speed=0.5, reverse=True)
    info, _ = _export(media, [fast, slow], name="speed")
    assert abs(info.duration - (3 + 8)) < 0.2
    assert info.has_audio


def test_extreme_speed_chains_atempo(media):
    clip = Clip(probe(TOOLS, media["a"]), speed=4.0)
    info, plan = _export(media, [clip], name="speed4")
    assert abs(info.duration - 1.5) < 0.15
    assert plan.passes[-1][plan.passes[-1].index("-filter_complex") + 1].count("atempo=2") == 2


def test_rotate_and_flip(media):
    clip = Clip(probe(TOOLS, media["a"]), rotate=90, hflip=True)
    info, _ = _export(media, [clip], name="rotate")
    assert (info.width, info.height) == (720, 1280)


@pytest.mark.parametrize("fit", ["crop", "pad", "blur"])
def test_vertical_aspect(media, fit):
    info, _ = _export(media, _clips(media, "c", "b"), name=f"vertical_{fit}", aspect="9:16",
                      fit=fit, height=720)
    assert (info.width, info.height) == (720, 1280)


def test_aspect_crop_without_resolution_never_upscales():
    from quickcut.models import MediaInfo
    info = MediaInfo("x.mp4", 10, 1920, 1080, "30", 30, True, "h264", 0)
    s = ExportSettings(aspect="9:16", fit="crop")
    assert output_frame([Clip(info)], s) == (608, 1080)
    assert output_frame([Clip(info)], ExportSettings(aspect="1:1", fit="pad")) == (1080, 1080)


@pytest.mark.parametrize("transition", ["wipeleft", "circleopen", "slideup"])
def test_transitions(media, transition):
    info, _ = _export(media, _clips(media, "a", "c"), name=f"tr_{transition}", crossfade=1.0,
                      transition=transition)
    assert abs(info.duration - 10) < 0.2


def test_fades_music_and_normalize(media):
    music = os.path.join(media["dir"], "music.mp3")
    subprocess.run([TOOLS.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=2",
                    music], check=True)
    info, plan = _export(media, _clips(media, "a", "b"), name="music", fade_in=1, fade_out=1,
                         music=music, music_volume=0.3, normalize=True)
    graph = plan.passes[-1][plan.passes[-1].index("-filter_complex") + 1]
    assert "amix" in graph and "loudnorm" in graph and "fade=t=out" in graph
    assert abs(info.duration - 10) < 0.2 and info.has_audio


def test_music_only_when_clip_audio_removed(media):
    music = os.path.join(media["dir"], "music2.mp3")
    subprocess.run([TOOLS.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=330:d=20",
                    music], check=True)
    info, _ = _export(media, _clips(media, "b"), name="musiconly", mute=True, music=music,
                      music_loop=False)
    assert info.has_audio and abs(info.duration - 4) < 0.2


def test_overlay(media):
    png = os.path.join(media["dir"], "overlay.png")
    subprocess.run([TOOLS.ffmpeg, "-v", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=red@0.5:s=1280x720,format=rgba", "-frames:v", "1", png], check=True)
    info, plan = _export(media, _clips(media, "a"), name="overlay",
                         overlays=[Overlay(png), Overlay(png, start=1, end=2)])
    graph = plan.passes[-1][plan.passes[-1].index("-filter_complex") + 1]
    assert graph.count("overlay=0:0") == 2 and "between(t,1.000,2.000)" in graph
    assert abs(info.duration - 6) < 0.2


def test_hdr_is_tonemapped(media):
    hdr = os.path.join(media["dir"], "hdr.mp4")
    subprocess.run([TOOLS.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=2",
                    "-pix_fmt", "yuv420p10le", "-c:v", "libx265", "-x265-params",
                    "log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
                    
                    hdr], check=True)
    clip = Clip(probe(TOOLS, hdr))
    assert clip.info.hdr
    info, plan = _export(media, [clip], name="hdr_out")
    assert "tonemap" in plan.passes[-1][plan.passes[-1].index("-filter_complex") + 1]
    assert _streams(info.path)["streams"][0]["pix_fmt"] == "yuv420p"


# -- export formats ---------------------------------------------------------------------------------

def test_gif_and_webp(media):
    clip = Clip(probe(TOOLS, media["c"]), start=0, end=2)
    info, _ = _export(media, [clip], name="anim", ext=".gif", height=240)
    # GIF delays are whole centiseconds, so 15 fps reads back as ~16.7 while the duration stays exact.
    assert info.video_codec == "gif" and info.fps_value < 17 and not info.has_audio
    assert abs(info.duration - 2) < 0.1
    # ffprobe can't decode animated WebP, so check the container directly.
    s = ExportSettings(output=os.path.join(media["dir"], "anim.webp"), height=240)
    for cmd in build_plan(TOOLS.ffmpeg, [clip], s).passes:
        subprocess.run(cmd, check=True, capture_output=True)
    with open(s.output, "rb") as f:
        data = f.read()
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP" and b"ANIM" in data[:200]


@pytest.mark.parametrize("ext,codec", [(".mp3", "mp3"), (".m4a", "aac"), (".wav", "pcm_s16le")])
def test_audio_only(media, ext, codec):
    out, _ = _export(media, _clips(media, "a", "b"), name="audio", ext=ext)
    data = _streams(out)
    assert [s["codec_name"] for s in data["streams"]] == [codec]
    assert abs(float(data["format"]["duration"]) - 10) < 0.3


def test_webm_vp9_two_pass(media):
    info, plan = _export(media, _clips(media, "a"), name="web", ext=".webm", encoder="libvpx-vp9",
                         mode="size", target_size_mb=1.5)
    assert len(plan.passes) == 2 and info.video_codec == "vp9"
    assert _streams(info.path)["streams"][1]["codec_name"] == "opus"
    assert info.size_bytes <= 1.5 * 1024 * 1024


def test_webm_rejects_h264(media):
    with pytest.raises(ValueError):
        build_plan(TOOLS.ffmpeg, _clips(media, "a"), ExportSettings(output="x.webm", encoder="libx264"))


def test_copy_mode(media):
    clip = Clip(probe(TOOLS, media["a"]), start=1, end=4)
    info, plan = _export(media, [clip], name="copy1", mode="copy")
    assert info.video_codec == "h264" and 2.5 < info.duration < 4.5
    # Copy mode cuts on keyframes, so use a source with one every half second.
    keyed = _make(os.path.join(media["dir"], "keyed.mp4"), "1280x720", 30, 6, gop=15)
    clips = [Clip(probe(TOOLS, keyed), start=0, end=3), Clip(probe(TOOLS, keyed), start=3)]
    info, plan = _export(media, clips, name="copy2", mode="copy")
    assert len(plan.passes) == 3 and abs(info.duration - 6) < 0.5


def test_copy_mode_refuses_mismatched_clips(media):
    errors, _ = copy_mode_problems(_clips(media, "a", "c"), ExportSettings(output="x.mp4", mode="copy"))
    assert errors


def test_auto_resolution(media):
    info, plan = _export(media, _clips(media, "c"), name="autores", mode="size", target_size_mb=0.3,
                         encoder="libx264")
    assert info.height < 1080 and plan.notes


def test_software_fallback_plan_exists(media):
    plan = build_plan(TOOLS.ffmpeg, _clips(media, "a"), ExportSettings(output="x.mp4"))
    assert "-hwaccel" in plan.passes[-1] and plan.fallback and "-hwaccel" not in plan.fallback.passes[-1]
