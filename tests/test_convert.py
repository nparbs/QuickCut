"""Explorer's "Convert to MP4": plan choices, output names and real ffmpeg runs."""
from __future__ import annotations

import subprocess

import pytest

from quickcut.ffmpeg import ENCODERS, find_tools, probe
from quickcut.models import MediaInfo
from quickcut.quick_convert import build_convert_plan, describe, output_path
from quickcut.shell_integration import CONVERT_EXTENSIONS, CONVERT_FLAG, launch_command

TOOLS = find_tools()


def _info(video="h264", audio="aac", path="clip.mov"):
    return MediaInfo(path, 5, 1920, 1080, "30", 30, bool(audio), video, 0, audio_codec=audio)


def test_mp4_friendly_streams_are_copied():
    plan = build_convert_plan("ffmpeg", _info(), "clip.mp4", None)
    cmd = plan.passes[0]
    assert cmd[cmd.index("-c:v") + 1] == "copy" and cmd[cmd.index("-c:a") + 1] == "copy"
    assert cmd[-1] == "clip.mp4" and plan.output == "clip.mp4"
    assert describe(_info()) == "copying (no quality loss)"


def test_hevc_is_tagged_and_pcm_audio_becomes_aac():
    cmd = build_convert_plan("ffmpeg", _info("hevc", "pcm_s16le"), "o.mp4", None).passes[0]
    assert cmd[cmd.index("-tag:v") + 1] == "hvc1"
    assert cmd[cmd.index("-c:a") + 1] == "aac"


def test_other_video_is_reencoded_with_cpu_fallback_for_gpu():
    with pytest.raises(ValueError):
        build_convert_plan("ffmpeg", _info("prores"), "o.mp4", None)
    plan = build_convert_plan("ffmpeg", _info("prores", ""), "o.mp4", ENCODERS["h264_nvenc"])
    assert "h264_nvenc" in plan.passes[0] and "-c:a" not in plan.passes[0]
    assert plan.fallback is not None and "libx264" in plan.fallback.passes[0]
    assert build_convert_plan("ffmpeg", _info("prores"), "o.mp4", ENCODERS["libx264"]).fallback is None


def test_output_name_never_overwrites(tmp_path):
    src = tmp_path / "a.mov"
    src.write_bytes(b"")
    assert output_path(str(src)) == str(tmp_path / "a.mp4")
    (tmp_path / "a.mp4").write_bytes(b"")
    (tmp_path / "a (1).mp4").write_bytes(b"")
    assert output_path(str(src)) == str(tmp_path / "a (2).mp4")


def test_menu_command_carries_the_flag():
    assert ".mp4" not in CONVERT_EXTENSIONS and ".mov" in CONVERT_EXTENSIONS
    command, _ = launch_command(CONVERT_FLAG)
    assert command.endswith(f'{CONVERT_FLAG} "%1"')
    assert CONVERT_FLAG not in launch_command()[0]


@pytest.mark.skipif(TOOLS is None, reason="ffmpeg not installed")
@pytest.mark.parametrize("ext,vcodec,acodec,expect_copy", [
    (".mov", "libx264", "aac", True),
    (".avi", "mpeg4", "mp3", False),
])
def test_real_conversion(tmp_path, ext, vcodec, acodec, expect_copy):
    src = str(tmp_path / f"in{ext}")
    subprocess.run([TOOLS.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=322x180:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:v", vcodec, "-c:a", acodec,
                    "-shortest", src], check=True)
    info = probe(TOOLS, src)
    out = output_path(src)
    plan = build_convert_plan(TOOLS.ffmpeg, info, out, None if expect_copy else ENCODERS["libx264"])
    subprocess.run(plan.passes[0], check=True, capture_output=True)
    result = probe(TOOLS, out)
    assert result.video_codec == "h264" and result.has_audio
    assert abs(result.duration - info.duration) < 0.2
    assert (result.width, result.height) == (info.width, info.height)
