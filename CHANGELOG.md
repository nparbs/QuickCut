# Changelog

## [Unreleased]

### Explorer
- **Convert to MP4 from the right-click menu.** Right-click a .mov, .mkv, .avi or other video in Explorer → **Convert to MP4 (QuickCut)**. A small window shows the progress and saves `name.mp4` next to the original (or `name (1).mp4` if that's taken; nothing gets overwritten). When the video is already H.264 or HEVC (like most phone .mov files) it is copied as it is: done in a second or two with no quality loss. Anything else is re-encoded to H.264 + AAC, on the GPU when there is one. Select several files to convert them one after another in one window. The window closes itself a few seconds after everything worked, and stays open to show the error if a file failed.
- The Tools menu entry is now **Add QuickCut to Explorer's right-click menu**. If you turned it on in an older version, QuickCut adds the new entry the next time it starts.

## [0.4.0] - 2026-11-23

### Editing
- **The player shows the output frame.** When you pick a shape (9:16, 1:1…), the player draws a box the shape of the export and dims everything outside it, so you can see exactly what gets cropped off. The blurred background shows inside the box when Fill is set to it.
- **Zoom and move each clip.** Drag the picture in the player to move it, scroll to zoom (around the cursor), double-click to go back to the Fill setting. Under the player: a Zoom slider (10–400 %, where 100 % just fills the frame and lower values show bars or blur around the clip), **Fit**, **Fill**, **Centre**, **Reset** and **Apply to all**. Each clip keeps its own framing, the clip list shows "framed 120%", undo covers it and projects save it. The clip can't be pushed so far that a gap opens at the edge.
- **Layers.** A new **Layers** tab next to Clips holds any number of text and picture layers, each with its own position, size, font, colour, box, opacity and timing (**⏱ Playhead** starts one where the playhead is). Drag them in the player, drag the corner handle or scroll over one to resize, restack them in the list (top is in front) and untick to hide. The player only shows a layer while the playhead is inside its time, and shows the selected one faintly otherwise. These replace the single text and logo in the export settings; projects that used them open with them turned into layers. Saved presets no longer include text or a logo.

### Fixes
- A thumbnail finishing just after the window closed could print an error.
- **Exporting over an existing file no longer loses it if the export fails or is cancelled.** QuickCut writes to a temporary file next to it and only replaces the old file once the export has finished.
- A project copied to a new folder together with its videos and pictures now uses the copies, even while the originals still exist in the old place.
- An error while making a thumbnail, filmstrip or waveform no longer leaves that image stuck as still being made.
- Finished exports no longer hold on to a little memory each until QuickCut closes.

## [0.3.1] - 2026-11-14

Version 0.3.0 was tagged but never released; 0.3.1 has everything below plus a fix for target file size exports.

### Fixes
- **Target file size** exports with two-pass encoding (CPU encoders) failed with newer FFmpeg builds, including the one bundled with the download.

### Editing
- Per-clip **volume** (0–400 %) and **mute**, under the player. The player previews them (up to 100 %), the clip list shows "muted" or "vol 50%", and projects save them. No re-encode mode ignores them and says so.

### Usability
- **Autosave and crash recovery**: unsaved edits are saved in the background every minute. If QuickCut doesn't close properly, it offers to bring your edit back the next time it starts.

### Export
- **Split into parts**: cut a video into pieces of a set length (for example 5 seconds) and save each one as its own file. Choose where to start, how long each part is and the most parts to make; the last part keeps whatever is left over. The parts use their own preset (any built-in or saved one), so they can be compressed differently from your normal exports. Each video's parts go in their own folder, named in order with their start and end times in the source (`clip_01_00m10.0s-00m17.0s.mp4`), along with a `parts.csv` index. Handy for reference clips in AI video tools such as ComfyUI.
- **Save your own presets**: **Save…** next to the Preset list stores all the export settings under a name, including any text overlay and logo. Saved presets appear below the built-in ones and can be replaced or deleted.

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
