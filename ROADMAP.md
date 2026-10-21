# QuickCut Roadmap

What's been built so far, what's known to be missing, and ideas for where QuickCut could go next.

## Done

### v0.1.0: first release

**Trimming and preview**
- [x] Add videos with the Add button, Ctrl+O, drag and drop (files or whole folders), or command-line arguments
- [x] Preview player with play/pause, frame stepping and 1-second stepping
- [x] Timeline bar with draggable start/end handles and a playhead
- [x] Exact start/end entry, plus "set start/end here" at the playhead (I / O keys)
- [x] Loop the trimmed selection during preview
- [x] Each clip keeps its own trim

**Joining clips**
- [x] Clips join in list order; reorder by dragging or with the arrow buttons
- [x] Hard cuts or a crossfade of video and audio, with adjustable length
- [x] Crossfade automatically shortened to fit the shortest clip
- [x] Clips with different resolutions, frame rates or no audio join cleanly (black bars, frame-rate conversion, silence)

**Output settings**
- [x] MP4, MKV and MOV containers
- [x] H.264, H.265/HEVC and AV1
- [x] GPU encoding (NVIDIA NVENC, AMD AMF, Intel Quick Sync), detected at startup by a test encode, with CPU fallback
- [x] Resolution presets from 4K to 240p, or same as source
- [x] Frame rate presets or any typed value
- [x] Encoder speed setting (slower and smaller, balanced, faster)
- [x] Audio: remove, volume 0–300 %, bitrate choice

**Compression**
- [x] Quality presets (High / Balanced / Small / Smallest), tuned per codec
- [x] Target file size with quick buttons (8, 10, 25, 50, 100, 500 MB)
- [x] Two-pass encoding for CPU encoders in size mode
- [x] Automatic re-encode at a lower bitrate if a file comes out over the size limit (up to 2 retries), with a warning if the target can't be reached
- [x] Manual mode: constant quality (CRF/CQ) or fixed bitrate
- [x] Live summary of output resolution, length and estimated size, with a warning when the bitrate is too low for the resolution

**Export**
- [x] Progress bar with pass count and encode speed
- [x] Cancel; a partial output file is cleaned up
- [x] "Show in folder" and "Play" when done
- [x] Guard against overwriting one of the input clips

**App**
- [x] Dark theme
- [x] Remembers export settings, folders and window size between sessions
- [x] Finds FFmpeg via `QUICKCUT_FFMPEG_DIR`, a bundled `ffmpeg` folder, or `PATH`
- [x] Keyboard shortcuts (listed under Help → Keyboard shortcuts)

**Tests**
- [x] End-to-end tests that generate synthetic clips and run real FFmpeg exports: trim, concat, crossfade, fps/resolution, audio, two-pass size targets, and every encoder available on the machine

### Fixes and polish since release
- [x] **Crossfade colour format bug.** FFmpeg's `xfade` filter output 4:4:4 video. NVIDIA AV1 rejected it, and NVIDIA H.264 encoded a much heavier stream that overshot size targets by about 25 %. Output is now forced to standard 4:2:0, with a regression test.
- [x] **SVT-AV1 overshoot** on short clips reduced by capping its overshoot.
- [x] **Settings panel layout**
  - Export button and summary pinned to the bottom
  - Labels aligned across all groups
  - Rows shown only for the current compression mode
  - Simpler manual-mode controls
  - Two-pass shown only when it applies

## Known limitations

- The output's shape, and its frame rate when set to *Same as source*, come from the **first** clip in the list.
- **HDR** (10-bit) sources are converted to 8-bit without tone mapping, so colours can look washed out.
- **Variable frame rate** sources (such as phone recordings) are converted to a constant frame rate.
- The **preview** uses Qt Multimedia, which can't play every format that FFmpeg can export from.
- Very small size targets at high resolutions can be **impossible to reach**. QuickCut warns but doesn't lower the resolution for you.
- The preview plays **one clip at a time**; there's no full preview of the joined result before exporting.
- Tested mainly on **Windows** with an NVIDIA GPU. AMD and Intel GPU encoders, macOS and Linux haven't been tested on real hardware.

## Future ideas

Roughly ordered by value within each section.

### Distribution
- [ ] **Standalone Windows build** (PyInstaller) with FFmpeg bundled, so no Python install is needed
- [ ] **GitHub Releases** with a downloadable zip/installer, built automatically by GitHub Actions
- [ ] **"Open with QuickCut"** in the Explorer right-click menu for video files
- [ ] Check for updates on startup (opt-in)

### Editing
- [ ] **Split clip at playhead**, to cut out the middle of a video or keep several parts of one file
- [ ] **Duplicate clip**, to reuse one source with different trims
- [ ] **Crop**, with aspect presets (16:9, 9:16 for Shorts/TikTok, 1:1, 4:5)
- [ ] **Rotate / flip**
- [ ] **Speed change** (0.25×–4×) for slow motion or time-lapse, with pitch-corrected audio
- [ ] **Fade in / fade out** from black at the start and end
- [ ] **More transitions**: wipe, slide, dissolve, zoom (all available in `xfade`)
- [ ] **Background music** track with its own volume and fade
- [ ] **Text overlay / watermark** (title, caption, logo image)
- [ ] Reverse a clip

### Export
- [ ] **Platform presets**: Discord (10 MB), WhatsApp, YouTube, X/Twitter, Instagram, each setting size limit, resolution and codec in one click
- [ ] **Lossless quick trim**: stream copy with no re-encode (instant, but cuts land on keyframes)
- [ ] **GIF and animated WebP** export with palette optimization
- [ ] **Audio-only export** (MP3 / M4A / WAV)
- [ ] **Batch mode**: apply the same settings to many files, exporting each separately
- [ ] **Export queue**: line up several exports and let them run
- [ ] **Audio normalization** (loudness to a standard level)
- [ ] **HDR tone mapping** to SDR, or keep 10-bit HDR output for HEVC/AV1
- [ ] Auto-lower the resolution when a size target is unreachable
- [ ] Estimated time remaining during export
- [ ] VP9 / WebM output
- [ ] Hardware-accelerated decoding for faster exports of 4K sources

### Usability
- [ ] **Preview the whole timeline**, with transitions, before exporting
- [ ] **Thumbnails** in the clip list and along the timeline bar
- [ ] **Audio waveform** under the trim bar, for cutting on sound
- [ ] **Undo / redo**
- [ ] **Save and open projects** (clip list, trims and settings as a `.json` file)
- [ ] Recent files / recent projects
- [ ] Light theme option, and following the Windows theme
- [ ] Remember the splitter panel sizes
- [ ] Tooltips that explain codec/encoder trade-offs in plain language

### Quality and maintenance
- [ ] **CI on GitHub Actions** running the test suite (with FFmpeg installed) on every push
- [ ] **GUI tests** with `pytest-qt`: trimming, reordering, settings persistence
- [ ] Test AMD/Intel GPU encoders and macOS/Linux on real hardware
- [ ] Optional log file of FFmpeg commands and errors, for bug reports
- [ ] Packaging metadata (`pyproject.toml`) so it can be installed with `pip install .`
