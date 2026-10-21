# Third-party software

QuickCut itself is MIT licensed (see `LICENSE`). The standalone Windows download also contains:

| Component | Licence | Source |
| --- | --- | --- |
| **FFmpeg** (in `ffmpeg/`) | GPL v3, because the build includes x264, x265 and other GPL libraries. Its licence text is in `ffmpeg/LICENSE.txt`. | https://ffmpeg.org — the Windows builds come from https://github.com/BtbN/FFmpeg-Builds, which publishes the matching source and build scripts. |
| **Qt 6** via PySide6 | LGPL v3 | https://www.qt.io, https://wiki.qt.io/Qt_for_Python |
| **Python** runtime | PSF License | https://www.python.org |

FFmpeg runs as a separate program; QuickCut starts `ffmpeg.exe` and `ffprobe.exe` and doesn't link to them. You can replace the bundled copy with any other FFmpeg build by swapping the files in `ffmpeg/bin`, or delete that folder to use the FFmpeg on your `PATH`.
