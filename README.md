# QuickCut

A small desktop app for the everyday video jobs: trim a clip, join a few clips together, change the frame rate or resolution, and compress the result. You can compress by quality preset, to a size limit such as 25 MB for Discord, or with manual settings. It uses FFmpeg underneath and can encode on your GPU (NVIDIA, AMD or Intel).

![QuickCut](docs/screenshot.png)

## Features

- **Trim** each clip by dragging the yellow handles, typing start/end times, or pressing **I** / **O** at the playhead. Preview loops the selection.
- **Join** clips in the order of the list (drag to reorder), with hard cuts or a **crossfade** of video and audio.
- **Mix sources freely.** Clips with different resolutions, frame rates or no audio track are fitted to the output (black bars where shapes differ, silence where audio is missing).
- **Resolution** (source, 4K down to 240p) and **frame rate** (presets or any value you type).
- **Compression**, three ways:
  - *Quality preset:* High / Balanced / Small / Smallest.
  - *Target file size:* type a maximum such as 8, 25 or 100 MB. CPU encoders use two-pass for accuracy. If a file still comes out too big, QuickCut re-encodes it at a lower bitrate automatically, up to two times.
  - *Manual:* constant quality (CRF/CQ) or a fixed bitrate, plus encoder speed.
- **Codecs:** H.264, H.265/HEVC and AV1, each on the CPU or your GPU, using whichever encoders your PC supports. They're detected at startup.
- **Audio:** keep, remove, change the volume (0–300 %), and pick the bitrate.
- **Output:** MP4, MKV or MOV, with live progress and a cancel button.

## Requirements

- Windows 10/11 (also works on macOS/Linux, but is mainly tested on Windows)
- Python 3.10+
- FFmpeg, including `ffprobe`. On Windows:

  ```
  winget install Gyan.FFmpeg
  ```

  QuickCut looks for FFmpeg in this order: the `QUICKCUT_FFMPEG_DIR` environment variable, an `ffmpeg` folder next to the app, then your `PATH`.

## Install and run

```
git clone https://github.com/nparbs/QuickCut.git
cd QuickCut
pip install -r requirements.txt
python -m quickcut
```

On Windows you can also double-click `run.pyw` to start it without a console window. You can drag video files or whole folders onto the window, or pass them on the command line: `python -m quickcut a.mp4 b.mp4`.

## How to use

1. **Add videos:** click **Add**, press Ctrl+O, or drop files onto the window.
2. **Trim:** select a clip in the list, then set its start and end. Each clip keeps its own trim.
3. **Order:** drag clips in the list, or use the arrow buttons. They're joined top to bottom.
4. **Choose settings** on the right. The summary under them shows the output size, length and (for size/bitrate modes) the estimated file size.
5. **Export…** and pick where to save.

### Keyboard shortcuts

| Key | Action |
| --- | --- |
| Space | Play / pause |
| I / O | Set start / end at the playhead |
| ← / → | Step one frame |
| Shift + ← / → | Step one second |
| Home / End | Jump to the trim start / end |
| Delete | Remove the selected clip |
| Ctrl+O / Ctrl+E | Add videos / Export |

## Notes

- The output's resolution shape and (when set to *Same as source*) frame rate come from the **first** clip in the list.
- The crossfade is limited to half the length of the shortest clip.
- GPU encoders are much faster. CPU encoders compress a little better and hit size targets more precisely. A very small target at a high resolution may be impossible for any encoder; QuickCut warns you and suggests lowering the resolution.
- Preview playback uses Qt Multimedia, which can't preview every format that FFmpeg can export from. Exports work regardless.

## Development

```
pip install -r requirements-dev.txt
python -m pytest
```

The tests generate small synthetic clips and run real exports through FFmpeg, covering trim, concat, crossfade, fps/resolution changes, audio options, and every encoder available on the machine. Encoders the machine can't run are skipped.

Code layout:

| File | Purpose |
| --- | --- |
| `quickcut/command.py` | Builds the FFmpeg filter graph and command lines (pure functions, unit-testable) |
| `quickcut/ffmpeg.py` | Finds FFmpeg, probes files, detects usable encoders |
| `quickcut/exporter.py` | Runs the export passes with progress, cancel and size retries |
| `quickcut/main_window.py` | Clip list, preview player, trimming |
| `quickcut/export_panel.py` | Export settings panel |
| `quickcut/trim_bar.py` | Timeline widget with trim handles |

## License

MIT
