"""Background generation of clip thumbnails, timeline filmstrips and audio waveforms."""
from __future__ import annotations

import subprocess

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from .ffmpeg import NO_WINDOW, Tools
from .models import MediaInfo

FILMSTRIP_FRAMES = 14
FILMSTRIP_HEIGHT = 54
THUMB_HEIGHT = 54
WAVEFORM_SIZE = (1600, 64)


def _png(args: list[str]) -> QImage:
    try:
        out = subprocess.run(args + ["-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True,
                             timeout=120, creationflags=NO_WINDOW).stdout
    except (subprocess.TimeoutExpired, OSError):
        return QImage()
    return QImage.fromData(out, "PNG")


def make_thumbnail(tools: Tools, info: MediaInfo) -> QImage:
    at = min(1.0, info.duration * 0.1)
    return _png([tools.ffmpeg, "-v", "error", "-ss", f"{at:.2f}", "-i", info.path, "-frames:v", "1",
                 "-vf", f"scale=-2:{THUMB_HEIGHT}"])


def make_filmstrip(tools: Tools, info: MediaInfo) -> QImage:
    # One input seek per tile, stitched with hstack, in a single ffmpeg run.
    w = max(2, int(round(FILMSTRIP_HEIGHT * info.width / info.height / 2)) * 2)
    args = [tools.ffmpeg, "-v", "error"]
    for k in range(FILMSTRIP_FRAMES):
        t = info.duration * (k + 0.5) / FILMSTRIP_FRAMES
        args += ["-ss", f"{t:.2f}", "-i", info.path]
    chains = [f"[{k}:v:0]scale={w}:{FILMSTRIP_HEIGHT},setsar=1,trim=end_frame=1[t{k}]"
              for k in range(FILMSTRIP_FRAMES)]
    stack = "".join(f"[t{k}]" for k in range(FILMSTRIP_FRAMES))
    graph = ";".join(chains) + f";{stack}hstack=inputs={FILMSTRIP_FRAMES}"
    return _png(args + ["-filter_complex", graph, "-frames:v", "1"])


def make_waveform(tools: Tools, info: MediaInfo) -> QImage:
    if not info.has_audio:
        return QImage()
    w, h = WAVEFORM_SIZE
    return _png([tools.ffmpeg, "-v", "error", "-i", info.path, "-filter_complex",
                 f"[0:a:0]aformat=channel_layouts=mono,showwavespic=s={w}x{h}:colors=#8ec5ff:scale=sqrt",
                 "-frames:v", "1"])


class _Job(QRunnable):
    def __init__(self, fn, done):
        super().__init__()
        self.fn, self.done = fn, done

    def run(self):
        self.done(self.fn())


class MediaCache(QObject):
    """Asks for images by kind and path; emits ready(kind, path, image) when they're made."""
    ready = Signal(str, str, QImage)

    def __init__(self, tools: Tools, parent=None):
        super().__init__(parent)
        self.tools = tools
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._images: dict[tuple[str, str], QImage] = {}
        self._pending: set[tuple[str, str]] = set()

    def get(self, kind: str, info: MediaInfo) -> QImage | None:
        """The image if it's ready; otherwise starts making it and returns None."""
        key = (kind, info.path)
        if key in self._images:
            return self._images[key]
        if key not in self._pending:
            self._pending.add(key)
            maker = {"thumb": make_thumbnail, "strip": make_filmstrip, "wave": make_waveform}[kind]
            self.pool.start(_Job(lambda: maker(self.tools, info),
                                 lambda img, key=key: self._done(key, img)))
        return None

    def _done(self, key, img: QImage):
        # Called on a worker thread; the signal is queued to the GUI thread.
        self._images[key] = img
        self._pending.discard(key)
        self.ready.emit(key[0], key[1], img)
