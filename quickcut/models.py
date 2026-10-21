from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int          # display size, after rotation
    height: int
    fps: str            # exact rate as ffmpeg understands it, e.g. "30000/1001"
    fps_value: float
    has_audio: bool
    video_codec: str
    size_bytes: int

    @property
    def name(self) -> str:
        return Path(self.path).name


@dataclass
class Clip:
    info: MediaInfo
    start: float = 0.0
    end: float = 0.0

    def __post_init__(self):
        if self.end <= 0:
            self.end = self.info.duration

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class ExportSettings:
    output: str = ""
    encoder: str = "libx264"
    mode: str = "preset"            # preset | size | manual
    preset: str = "medium"          # high | medium | low | smallest
    target_size_mb: float = 25.0
    two_pass: bool = True           # size mode, CPU encoders only
    manual_rate: str = "quality"    # quality | bitrate
    manual_quality: int = 23        # CRF / CQ value passed straight to the encoder
    manual_bitrate_k: int = 4000
    speed: str = "balanced"         # quality | balanced | fast
    height: int | None = None       # None keeps the first clip's resolution
    fps: str | None = None          # None keeps the first clip's frame rate
    mute: bool = False
    volume: float = 1.0
    audio_bitrate_k: int = 160
    crossfade: float = 0.0          # seconds; 0 joins clips with hard cuts
