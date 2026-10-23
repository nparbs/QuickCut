# QuickCut Roadmap

What's been built so far, what's known to be missing, and ideas for where QuickCut could go next. See [CHANGELOG.md](CHANGELOG.md) for release-by-release details.

## Done

### v0.2.0: distribution, editing, export and usability

**Distribution**
- [x] Standalone Windows build (PyInstaller) with FFmpeg bundled; no Python install needed
- [x] The build script checks that the packaged app starts and uses its bundled FFmpeg
- [x] GitHub Actions: tests on every push; on a `v*` tag it builds the zip and publishes a GitHub Release with notes from the changelog
- [x] "Open with QuickCut" in the Explorer right-click menu (per user, toggled from the Tools menu)
- [x] Single window: opening more files while QuickCut runs adds them to the open window
- [x] Check for updates now, or on startup (opt-in)

**Editing**
- [x] Split clip at playhead
- [x] Duplicate clip
- [x] Crop / framing with aspect presets (16:9, 9:16, 1:1, 4:5, 4:3, 21:9) and black bars, crop to fill, or blurred background
- [x] Rotate / flip, shown live in the player
- [x] Speed change (0.25×–4×) with pitch-corrected audio
- [x] Fade in / fade out from black
- [x] 21 transitions (crossfade, dissolve, dip to black/white, wipes, slides, circle, radial, zoom, pixelate…)
- [x] Background music with volume, looping and an automatic end fade
- [x] Text overlay and logo watermark, with position, size, colour, box, opacity and timing
- [x] Reverse a clip

**Export**
- [x] Platform presets: Discord, Discord Nitro, WhatsApp, email, YouTube, Shorts/Reels/TikTok, Instagram, X, GIF, MP3
- [x] Lossless quick trim/join (stream copy), with checks for clips that can't be joined this way
- [x] GIF and animated WebP export with palette optimization
- [x] Audio-only export (MP3 / M4A / WAV)
- [x] Batch: export each clip separately
- [x] Export queue that runs while you keep editing
- [x] Loudness normalization (−16 LUFS)
- [x] HDR to SDR tone mapping
- [x] Auto-lower the resolution when a size target is too small for it
- [x] Estimated time remaining
- [x] VP9 / WebM output with Opus audio and two-pass size targets
- [x] Hardware-accelerated decoding, with automatic software fallback

**Usability**
- [x] Preview the whole edit (quick low-res render including transitions, text and music)
- [x] Thumbnails in the clip list; filmstrip and audio waveform on the timeline
- [x] Undo / redo
- [x] Save and open projects (`.qcut`), with relative paths so moved folders still open
- [x] Recent projects
- [x] Dark, light, or follow-Windows theme
- [x] Splitter sizes and window layout remembered
- [x] Plain-language tooltips for codecs, encoders and settings

**Tests**: 71 automated tests (6 of them need AMD or Intel GPUs), including end-to-end exports for every feature, and GUI tests that drive the real window off-screen.

### v0.1.0: first release
- [x] Trimming with preview, timeline handles and keyboard shortcuts
- [x] Joining clips with hard cuts or crossfades; mixed resolutions, frame rates and audio-less clips
- [x] Resolution and frame-rate changes
- [x] Compression by quality preset, target file size (two-pass, automatic retry) or manual settings
- [x] H.264, HEVC and AV1 on CPU, NVIDIA, AMD or Intel, detected at startup
- [x] Audio remove / volume / bitrate; MP4, MKV and MOV
- [x] Settings panel layout fix: pinned Export button, aligned labels, no gaps

## Known limitations

- **Variable frame rate** sources (such as phone recordings) are converted to a constant frame rate.
- **HDR** is always converted to SDR; there's no option to keep 10-bit HDR output yet.
- Transitions use **one style and length** for every cut; you can't choose per cut.
- Crop always keeps the **centre** of the frame; you can't move the crop area.
- The live player doesn't show **reverse, framing, text, music or transitions**; use Preview edit for those.
- Animated **WebP** files can't be opened in QuickCut itself (FFmpeg can't decode them).
- **No re-encode** mode cuts on keyframes, so cuts can be off by up to a few seconds.
- Tested mainly on **Windows** with an NVIDIA GPU. AMD and Intel GPU encoders, macOS and Linux are untested on real hardware, and there's no macOS/Linux download yet.
- The Windows download isn't **code-signed**, so SmartScreen may warn the first time you run it.

## Future ideas

### Editing
- [ ] Per-cut transition style and length
- [ ] Drag to position the crop area; per-clip crop
- [x] Per-clip volume and mute
- [ ] Freeze frame and still-image clips (photos in a slideshow)
- [ ] Subtitles: burn in `.srt` files
- [ ] Multiple text overlays, each with its own timing
- [ ] Speed ramps and a smoother reverse for long clips

### Export
- [ ] Keep 10-bit HDR when exporting to HEVC or AV1
- [ ] Save your own export presets
- [ ] Choose which audio track to use for multi-track sources
- [ ] Optional variable frame rate passthrough

### Usability
- [ ] Live preview of framing and text in the player
- [ ] Timeline view with clip widths proportional to length
- [ ] Autosave and crash recovery for projects
- [ ] Zoomable timeline for precise trims on long videos

### Distribution and maintenance
- [ ] Installer and `winget` package
- [ ] Code-signed Windows builds
- [ ] macOS and Linux builds
- [ ] Packaging metadata (`pyproject.toml`) for `pip install`
- [ ] Optional log file of FFmpeg commands and errors, for bug reports
