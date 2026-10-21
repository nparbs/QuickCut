"""Locating FFmpeg, probing media files and detecting usable encoders."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .models import MediaInfo

# Keep ffmpeg/ffprobe from flashing a console window when run from pythonw.
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class FFmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tools:
    ffmpeg: str
    ffprobe: str


def find_tools() -> Tools | None:
    """Find ffmpeg and ffprobe: QUICKCUT_FFMPEG_DIR, a bundled ./ffmpeg folder, then PATH."""
    dirs = []
    if os.environ.get("QUICKCUT_FFMPEG_DIR"):
        dirs.append(Path(os.environ["QUICKCUT_FFMPEG_DIR"]))
    app_dir = Path(sys.argv[0]).resolve().parent
    dirs += [app_dir / "ffmpeg" / "bin", app_dir / "ffmpeg", app_dir]

    def locate(name: str) -> str | None:
        exe = name + (".exe" if sys.platform == "win32" else "")
        for d in dirs:
            if (d / exe).is_file():
                return str(d / exe)
        return shutil.which(name)

    ffmpeg, ffprobe = locate("ffmpeg"), locate("ffprobe")
    if not ffmpeg or not ffprobe:
        return None
    return Tools(ffmpeg, ffprobe)


def _run(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=NO_WINDOW,
    )


def _parse_rate(rate: str | None) -> tuple[str, float] | None:
    if not rate or rate in ("0/0", "0"):
        return None
    try:
        if "/" in rate:
            num, den = (int(x) for x in rate.split("/"))
            if num <= 0 or den <= 0:
                return None
            value = num / den
        else:
            value = float(rate)
    except ValueError:
        return None
    # Ignore nonsense rates some containers report (e.g. 90000/1 timebases).
    if not 1 <= value <= 1000:
        return None
    return rate, value


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            return int(float(sd["rotation"])) % 360
    try:
        return int(stream.get("tags", {}).get("rotate", 0)) % 360
    except ValueError:
        return 0


def probe(tools: Tools, path: str) -> MediaInfo:
    result = _run([tools.ffprobe, "-v", "error", "-print_format", "json",
                   "-show_format", "-show_streams", path])
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip() or "ffprobe could not read this file")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise FFmpegError("No video stream found")

    fmt = data.get("format", {})
    duration = 0.0
    for candidate in (fmt.get("duration"), video.get("duration")):
        try:
            duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue
    if duration <= 0:
        raise FFmpegError("Could not determine the video's duration")

    rate = (_parse_rate(video.get("avg_frame_rate"))
            or _parse_rate(video.get("r_frame_rate")) or ("30", 30.0))

    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if _rotation(video) in (90, 270):
        width, height = height, width
    if width <= 0 or height <= 0:
        raise FFmpegError("Could not determine the video's resolution")

    return MediaInfo(
        path=path,
        duration=duration,
        width=width,
        height=height,
        fps=rate[0],
        fps_value=rate[1],
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        video_codec=video.get("codec_name", "?"),
        size_bytes=int(fmt.get("size", 0) or 0),
    )


@dataclass(frozen=True)
class Encoder:
    name: str      # ffmpeg encoder name
    codec: str     # h264 | hevc | av1
    family: str    # x264 | x265 | svtav1 | nvenc | amf | qsv
    label: str

    @property
    def hardware(self) -> bool:
        return self.family in ("nvenc", "amf", "qsv")


_GPU_LABELS = {"nvenc": "NVIDIA GPU", "amf": "AMD GPU", "qsv": "Intel GPU (Quick Sync)"}
_CPU = {"h264": ("libx264", "x264"), "hevc": ("libx265", "x265"), "av1": ("libsvtav1", "svtav1")}

ENCODERS: dict[str, Encoder] = {}
for _codec in ("h264", "hevc", "av1"):
    for _family, _label in _GPU_LABELS.items():
        _name = f"{_codec}_{_family}"
        ENCODERS[_name] = Encoder(_name, _codec, _family, f"{_label} ({_name})")
    _name, _family = _CPU[_codec]
    ENCODERS[_name] = Encoder(_name, _codec, _family, f"CPU ({_name})")

CODEC_LABELS = {
    "h264": "H.264 (plays everywhere)",
    "hevc": "H.265 / HEVC (smaller files)",
    "av1": "AV1 (smallest, newer devices)",
}


def _encoder_works(tools: Tools, enc: Encoder) -> bool:
    # GPU encoders are compiled into most builds even without the hardware, so try a tiny encode.
    try:
        result = _run([tools.ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi",
                       "-i", "color=black:s=640x360:r=30", "-frames:v", "5",
                       "-pix_fmt", "yuv420p", "-c:v", enc.name, "-f", "null", "-"], timeout=20)
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def detect_encoders(tools: Tools) -> list[Encoder]:
    """Return the encoders this machine can actually use, GPU ones first."""
    result = _run([tools.ffmpeg, "-hide_banner", "-encoders"])
    listed = {line.split()[1] for line in result.stdout.splitlines()
              if len(line.split()) > 1 and line.startswith(" V")}
    candidates = [e for e in ENCODERS.values() if e.name in listed]
    hardware = [e for e in candidates if e.hardware]
    with ThreadPoolExecutor(max_workers=max(1, len(hardware))) as pool:
        works = dict(zip(hardware, pool.map(lambda e: _encoder_works(tools, e), hardware)))
    usable = [e for e in candidates if not e.hardware or works.get(e)]
    return sorted(usable, key=lambda e: (not e.hardware, list(ENCODERS).index(e.name)))
