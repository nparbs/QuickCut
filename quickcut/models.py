from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int          # display size, after rotation metadata
    height: int
    fps: str            # exact rate as ffmpeg understands it, e.g. "30000/1001"
    fps_value: float
    has_audio: bool
    video_codec: str
    size_bytes: int
    audio_codec: str = ""
    hdr: bool = False

    @property
    def name(self) -> str:
        return Path(self.path).name


@dataclass
class Clip:
    info: MediaInfo
    start: float = 0.0
    end: float = 0.0
    speed: float = 1.0      # 0.25 - 4
    rotate: int = 0         # extra clockwise rotation: 0, 90, 180, 270
    hflip: bool = False
    vflip: bool = False
    reverse: bool = False

    def __post_init__(self):
        if self.end <= 0:
            self.end = self.info.duration

    @property
    def length(self) -> float:
        """Length of the selected part of the source."""
        return max(0.0, self.end - self.start)

    @property
    def out_length(self) -> float:
        """Length in the exported video, after the speed change."""
        return self.length / self.speed

    @property
    def display_size(self) -> tuple[int, int]:
        w, h = self.info.width, self.info.height
        return (h, w) if self.rotate in (90, 270) else (w, h)

    @property
    def has_effects(self) -> bool:
        return self.speed != 1.0 or self.rotate != 0 or self.hflip or self.vflip or self.reverse


@dataclass
class Overlay:
    """A transparent PNG the size of the output frame, laid over the video."""
    image: str
    start: float = 0.0
    end: float | None = None    # None shows it until the end


@dataclass
class ExportSettings:
    output: str = ""
    encoder: str = "libx264"
    mode: str = "preset"            # preset | size | manual | copy
    preset: str = "medium"          # high | medium | low | smallest
    target_size_mb: float = 25.0
    two_pass: bool = True           # size mode, CPU encoders only
    auto_resolution: bool = True    # size mode: lower the resolution if the bitrate is too low
    manual_rate: str = "quality"    # quality | bitrate
    manual_quality: int = 23        # CRF / CQ value passed straight to the encoder
    manual_bitrate_k: int = 4000
    speed: str = "balanced"         # quality | balanced | fast
    height: int | None = None       # short side of the frame ("1080p"); None keeps the source size
    fps: str | None = None          # None keeps the first clip's frame rate
    aspect: str | None = None       # e.g. "9:16"; None keeps the first clip's shape
    fit: str = "pad"                # pad | crop | blur: how clips of another shape fill the frame
    hw_decode: bool = True
    tonemap: bool = True            # convert HDR clips to normal (SDR) colours
    mute: bool = False              # drop the clips' own audio
    volume: float = 1.0
    audio_bitrate_k: int = 160
    normalize: bool = False
    music: str | None = None
    music_volume: float = 0.5
    music_loop: bool = True
    crossfade: float = 0.0          # seconds; 0 joins clips with hard cuts
    transition: str = "fade"        # any xfade transition
    fade_in: float = 0.0
    fade_out: float = 0.0
    overlays: list[Overlay] = field(default_factory=list)
