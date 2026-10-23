# Changelog

## [Unreleased]

### Editing
- Per-clip **volume** (0–400 %) and **mute**, under the player. The player previews them (up to 100 %), the clip list shows "muted" or "vol 50%", and projects save them. No re-encode mode ignores them and says so.

## [0.2.0] - 2026-10-21

### Download
- **Standalone Windows build**: download the zip from GitHub Releases, extract, run `QuickCut.exe`. FFmpeg is included and no Python install is needed. Builds are made by GitHub Actions.
- **"Open with QuickCut"** in the Explorer right-click menu (Tools menu, per user, no admin needed). Opening more files while QuickCut is running adds them to the open window.
- **Update check**: Help → Check for updates, and an optional check on startup (off by default).

### Editing
- **Split** a clip at the playhead (S) and **duplicate** clips (Ctrl+D).
- Per-clip **speed** (0.25×–4×, audio keeps its pitch), **rotate**, **mirror/flip** and **reverse**. Rotation and flips show in the preview.
- **Framing**: 16:9, 9:16 (Shorts/Reels/TikTok), 1:1, 4:5, 4:3 and 21:9, filled with black bars, cropped, or over a blurred background.
- **21 transitions** (crossfade, dip to black, wipes, slides, circle, zoom, pixelate…), plus **fade in / fade out**.
- **Background music** with its own volume, looping and an automatic fade at the end.
- **Text and logo** overlays with position, size, colour, background box, opacity and timing.

### Export
- **Destination presets**: Discord, Discord Nitro, WhatsApp, email, YouTube, Shorts/Reels/TikTok, Instagram, X/Twitter, GIF and MP3.
- **No re-encode** mode: instant, lossless cuts and joins (cuts snap to keyframes).
- **GIF** and **animated WebP** export with palette optimisation.
- **Audio-only** export: MP3, M4A and WAV.
- **WebM / VP9** output (Opus audio), including two-pass size targets.
- **Export queue**: exports run one after another while you keep editing (Ctrl+J shows the queue).
- **Export each clip separately** into a folder.
- **Even out loudness** (−16 LUFS normalisation).
- **HDR to SDR** tone mapping, so HDR phone videos no longer look washed out.
- Target size mode **lowers the resolution automatically** when the size limit is too small for it.
- **Time remaining** shown while exporting.
- **GPU decoding** of source clips, with an automatic software retry if a file doesn't support it.

### Usability
- **Preview edit** (Ctrl+P): renders a quick low-resolution version of the whole edit, with transitions, text and music, and plays it.
- **Thumbnails** in the clip list, plus a **filmstrip** and **audio waveform** on the timeline.
- **Undo / redo** (Ctrl+Z / Ctrl+Y) for all clip edits.
- **Projects**: save and open `.qcut` files, with a recent-projects menu. Projects still open after you move the project and its videos to another folder together.
- **Themes**: dark, light, or follow Windows.
- Settings are grouped in collapsible sections, and the panel sizes are remembered.
- Plain-language tooltips on codecs, encoders and other settings.

## [0.1.0] - 2026-10-20

First release: trimming with preview, joining with crossfades, resolution and frame-rate changes, compression by quality preset, target file size or manual settings, GPU encoding, and audio volume control.
