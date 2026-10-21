"""The right-hand panel: everything about how the export looks, sounds and is compressed.

Settings scroll in collapsible sections; the summary and export buttons stay pinned at the bottom.
Every setting is registered in ``self.fields`` so presets, saved settings and project files all
go through get_state()/set_state().
"""
from __future__ import annotations

import json

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSpinBox, QVBoxLayout, QWidget,
)

from .command import TRANSITIONS, TWO_PASS_FAMILIES, codec_allowed, output_kind
from .ffmpeg import CODEC_LABELS, CODEC_TIPS, ENCODERS, Encoder
from .models import ExportSettings
from .overlays import POSITIONS
from .presets import PLATFORM_PRESETS, preset_state
from .widgets import ColorButton, FilePicker, Section

FORMATS = [("MP4 video", ".mp4"), ("MKV video", ".mkv"), ("MOV video", ".mov"),
           ("WebM video", ".webm"), ("GIF animation", ".gif"), ("WebP animation", ".webp"),
           ("MP3 audio", ".mp3"), ("M4A audio", ".m4a"), ("WAV audio", ".wav")]
RESOLUTIONS = [("Same as source", 0), ("2160p (4K)", 2160), ("1440p", 1440), ("1080p", 1080),
               ("720p", 720), ("540p", 540), ("480p", 480), ("360p", 360), ("240p", 240)]
FPS_CHOICES = ["Same as source", "120", "60", "50", "30", "25", "24", "15", "10"]
ASPECTS = [("Same as source", ""), ("16:9 landscape", "16:9"), ("9:16 vertical (Shorts, Reels, TikTok)", "9:16"),
           ("1:1 square", "1:1"), ("4:5 portrait (Instagram)", "4:5"), ("4:3", "4:3"),
           ("21:9 cinematic", "21:9")]
FITS = [("Black bars", "pad"), ("Crop to fill", "crop"), ("Blurred background", "blur")]
PRESETS = [("High quality", "high"), ("Balanced", "medium"), ("Small file", "low"),
           ("Smallest file", "smallest")]
MODES = [("Quality preset", "preset"), ("Target file size", "size"), ("Manual", "manual"),
         ("No re-encode (instant, lossless)", "copy")]
RATES = [("Constant quality", "quality"), ("Fixed bitrate", "bitrate")]
SPEEDS = [("Balanced", "balanced"), ("Slower, smaller file", "quality"), ("Faster", "fast")]
AUDIO_BITRATES = [64, 96, 128, 160, 192, 256, 320]
SIZE_SHORTCUTS = [8, 10, 25, 50, 100, 500]
TRANSITION_NAMES = {
    "fade": "Crossfade", "dissolve": "Dissolve", "fadeblack": "Dip to black",
    "fadewhite": "Dip to white", "wipeleft": "Wipe left", "wiperight": "Wipe right",
    "wipeup": "Wipe up", "wipedown": "Wipe down", "slideleft": "Slide left",
    "slideright": "Slide right", "slideup": "Slide up", "slidedown": "Slide down",
    "smoothleft": "Smooth left", "smoothright": "Smooth right", "circleopen": "Circle open",
    "circleclose": "Circle close", "radial": "Radial", "zoomin": "Zoom in",
    "pixelize": "Pixelate", "hblur": "Blur", "distance": "Distance",
}
CUSTOM = "Custom"


def _combo(items) -> QComboBox:
    box = QComboBox()
    for text, data in items:
        box.addItem(text, data)
    return box


def _select(box: QComboBox, data) -> bool:
    i = box.findData(data)
    if i >= 0:
        box.setCurrentIndex(i)
    return i >= 0


def _spin(lo, hi, value, step=1.0, decimals=1, suffix="") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(value)
    s.setSuffix(suffix)
    return s


class ExportPanel(QWidget):
    changed = Signal()
    exportRequested = Signal()
    queueRequested = Signal()
    previewRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._encoders: list[Encoder] = []
        self._saved_choice: dict[str, str] = {}   # codec/encoder restored before detection finishes
        self._busy = False
        self._applying = False
        self.fields: dict[str, QWidget] = {}

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(8, 4, 12, 8)
        body.setSpacing(8)

        # Destination preset
        self.platform = QComboBox()
        self.platform.addItem(CUSTOM)
        self.platform.addItems(list(PLATFORM_PRESETS))
        self.platform.setToolTip("Sets everything below for a common destination. "
                                 "Change anything afterwards and it becomes Custom.")
        row = QHBoxLayout()
        row.addWidget(QLabel("<b>Preset</b>"))
        row.addWidget(self.platform, 1)
        body.addLayout(row)

        # Output & video
        self.sec_video = sec = Section("Output and video")
        self.format = self._reg("format", _combo(FORMATS))
        self.codec = self._reg("codec", QComboBox())
        self.encoder = self._reg("encoder", QComboBox())
        self.encoder.setToolTip("GPU encoders are much faster. CPU encoders compress a little better "
                                "and hit target sizes more precisely (two-pass).")
        self.resolution = self._reg("resolution", _combo(RESOLUTIONS))
        self.resolution.setToolTip("Size of the short side: 1080p is 1920×1080 for landscape and "
                                   "1080×1920 for vertical video.")
        self.fps = self._reg("fps", QComboBox())
        self.fps.setEditable(True)
        self.fps.addItems(FPS_CHOICES)
        self.fps.setToolTip("Pick a frame rate or type your own, e.g. 29.97")
        self.speed = self._reg("speed", _combo(SPEEDS))
        self.speed.setToolTip("How hard the encoder works. Slower gives a smaller file at the same quality.")
        self.hw_decode = self._reg("hw_decode", QCheckBox("Use GPU to decode clips"))
        self.hw_decode.setChecked(True)
        self.hw_decode.setToolTip("Faster for 4K and HEVC sources. If it fails on a file, "
                                  "QuickCut automatically retries without it.")
        sec.add("Format", self.format)
        sec.add("Codec", self.codec)
        sec.add("Encoder", self.encoder)
        sec.add("Resolution", self.resolution)
        sec.add("Frame rate", self.fps)
        sec.add("Speed", self.speed)
        sec.add("", self.hw_decode)
        body.addWidget(sec)

        # Framing
        self.sec_frame = sec = Section("Framing", expanded=False)
        self.aspect = self._reg("aspect", _combo(ASPECTS))
        self.fit = self._reg("fit", _combo(FITS))
        self.fit.setToolTip("What happens when a clip's shape doesn't match the output: black bars, "
                            "cropping the edges off, or a blurred copy of the video behind it.")
        sec.add("Shape", self.aspect)
        sec.add("Fill", self.fit)
        body.addWidget(sec)

        # Compression
        self.sec_comp = sec = Section("Compression")
        self.mode = self._reg("mode", _combo(MODES))
        self.mode.setItemData(3, "Cuts and joins without re-encoding. Instant and lossless, but cuts "
                                 "snap to keyframes and most other settings are ignored.", Qt.ToolTipRole)
        self.preset = self._reg("preset", _combo(PRESETS))
        self.size_mb = self._reg("size_mb", _spin(0.5, 100000, 25, 1, 1, " MB"))
        self.size_shortcuts = QWidget()
        row = QHBoxLayout(self.size_shortcuts)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for mb in SIZE_SHORTCUTS:
            b = QPushButton(str(mb))
            b.setToolTip(f"{mb} MB")
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.setMinimumWidth(28)
            b.clicked.connect(lambda _=False, mb=mb: self.size_mb.setValue(mb))
            row.addWidget(b)
        self.auto_res = self._reg("auto_res", QCheckBox("Lower resolution if needed"))
        self.auto_res.setChecked(True)
        self.auto_res.setToolTip("If the size limit leaves too few bits for this resolution, export "
                                 "smaller instead of blocky.")
        self.two_pass = self._reg("two_pass", QCheckBox("Two-pass encoding"))
        self.two_pass.setToolTip("Encodes twice to hit the size more precisely. CPU encoders only.")
        self.two_pass.setChecked(True)
        self.rate = self._reg("rate", _combo(RATES))
        self.quality = self._reg("quality", QSpinBox())
        self.quality.setRange(0, 63)
        self.quality.setValue(23)
        self.quality.setToolTip("CRF / CQ value. Lower = better quality, bigger file. "
                                "Typical: 18-28 for H.264, 22-32 for HEVC, 25-40 for AV1/VP9.")
        self.bitrate = self._reg("bitrate", QSpinBox())
        self.bitrate.setRange(100, 500000)
        self.bitrate.setSingleStep(500)
        self.bitrate.setValue(4000)
        self.bitrate.setSuffix(" kbps")
        sec.add("Mode", self.mode)
        sec.add("Quality", self.preset)
        sec.add("Max size", self.size_mb)
        sec.add("", self.size_shortcuts)
        sec.add("", self.auto_res)
        sec.add("", self.two_pass)
        sec.add("Rate control", self.rate)
        sec.add("CRF / CQ", self.quality)
        sec.add("Bitrate", self.bitrate)
        body.addWidget(sec)

        # Audio
        self.sec_audio = sec = Section("Audio")
        self.mute = self._reg("mute", QCheckBox("Remove the clips' audio"))
        self.volume = self._reg("volume", QSlider(Qt.Horizontal))
        self.volume.setRange(0, 300)
        self.volume.setValue(100)
        self.volume_label = QLabel("100%")
        self.volume_label.setFixedWidth(40)
        self.volume_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.volume_row = QWidget()
        row = QHBoxLayout(self.volume_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.volume, 1)
        row.addWidget(self.volume_label)
        self.normalize = self._reg("normalize", QCheckBox("Even out loudness"))
        self.normalize.setToolTip("Normalizes to -16 LUFS, the level most streaming sites use, so quiet "
                                  "clips get louder and loud ones quieter.")
        self.audio_bitrate = self._reg("audio_bitrate", _combo([(f"{k} kbps", k) for k in AUDIO_BITRATES]))
        _select(self.audio_bitrate, 160)
        sec.add("", self.mute)
        sec.add("Volume", self.volume_row)
        sec.add("", self.normalize)
        sec.add("Bitrate", self.audio_bitrate)
        body.addWidget(sec)

        # Music
        self.sec_music = sec = Section("Background music", expanded=False)
        self.music = self._reg("music", FilePicker(
            "Choose music", "Audio (*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus);;All files (*)"))
        self.music_volume = self._reg("music_volume", QSlider(Qt.Horizontal))
        self.music_volume.setRange(0, 200)
        self.music_volume.setValue(50)
        self.music_volume_label = QLabel("50%")
        self.music_volume_label.setFixedWidth(40)
        self.music_volume_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        music_row = QWidget()
        row = QHBoxLayout(music_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.music_volume, 1)
        row.addWidget(self.music_volume_label)
        self.music_loop = self._reg("music_loop", QCheckBox("Loop if shorter than the video"))
        self.music_loop.setChecked(True)
        sec.add("Track", self.music)
        sec.add("Volume", music_row)
        sec.add("", self.music_loop)
        body.addWidget(sec)

        # Transitions & fades
        self.sec_trans = sec = Section("Transitions and fades", expanded=False)
        self.transition_on = self._reg("transition_on", QCheckBox("Transition between clips"))
        self.transition = self._reg("transition", _combo([(TRANSITION_NAMES[t], t) for t in TRANSITIONS]))
        self.crossfade = self._reg("crossfade", _spin(0.1, 5.0, 0.5, 0.25, 2, " s"))
        self.fade_in = self._reg("fade_in", _spin(0, 10, 0, 0.5, 1, " s"))
        self.fade_in.setSpecialValueText("Off")
        self.fade_out = self._reg("fade_out", _spin(0, 10, 0, 0.5, 1, " s"))
        self.fade_out.setSpecialValueText("Off")
        sec.add("", self.transition_on)
        sec.add("Style", self.transition)
        sec.add("Length", self.crossfade)
        sec.add("Fade in", self.fade_in)
        sec.add("Fade out", self.fade_out)
        body.addWidget(sec)

        # Text & logo
        self.sec_text = sec = Section("Text and logo", expanded=False)
        self.text = self._reg("text", QPlainTextEdit())
        self.text.setPlaceholderText("Caption or title (optional)")
        self.text.setFixedHeight(self.text.fontMetrics().lineSpacing() * 3 + 12)
        self.text_pos = self._reg("text_pos", _combo(POSITIONS))
        self.text_size = self._reg("text_size", _spin(2, 25, 6, 0.5, 1, " %"))
        self.text_size.setToolTip("Text height as a percentage of the video's short side")
        self.text_color = self._reg("text_color", ColorButton("#ffffff"))
        self.text_box = self._reg("text_box", QCheckBox("Dark box behind text"))
        self.text_box.setChecked(True)
        self.text_start = self._reg("text_start", _spin(0, 36000, 0, 0.5, 1, " s"))
        self.text_duration = self._reg("text_duration", _spin(0, 36000, 0, 0.5, 1, " s"))
        self.text_duration.setSpecialValueText("Until the end")
        self.logo = self._reg("logo", FilePicker("Choose logo", "Images (*.png *.jpg *.jpeg *.webp *.bmp)"))
        self.logo_pos = self._reg("logo_pos", _combo(POSITIONS))
        _select(self.logo_pos, "bottom-right")
        self.logo_size = self._reg("logo_size", _spin(2, 60, 15, 1, 0, " %"))
        self.logo_size.setToolTip("Logo width as a percentage of the video width")
        self.logo_opacity = self._reg("logo_opacity", QSlider(Qt.Horizontal))
        self.logo_opacity.setRange(10, 100)
        self.logo_opacity.setValue(85)
        sec.add("Text", self.text)
        sec.add("Position", self.text_pos)
        sec.add("Size", self.text_size)
        sec.add("Colour", self.text_color)
        sec.add("", self.text_box)
        sec.add("Show from", self.text_start)
        sec.add("for", self.text_duration)
        sec.add("Logo", self.logo)
        sec.add("Position", self.logo_pos)
        sec.add("Size", self.logo_size)
        sec.add("Opacity", self.logo_opacity)
        body.addWidget(sec)
        body.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        scroll.setMinimumWidth(content.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width())

        # Pinned footer
        footer = QFrame()
        footer.setObjectName("exportFooter")
        footer.setStyleSheet("#exportFooter { border-top: 1px solid palette(mid); }")
        foot = QVBoxLayout(footer)
        foot.setContentsMargins(8, 10, 12, 8)
        foot.setSpacing(8)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        self.summary.setMinimumHeight(self.summary.fontMetrics().lineSpacing() * 2)
        self.preview_btn = QPushButton("Preview edit")
        self.preview_btn.setToolTip("Render a quick low-resolution version of the whole edit, with "
                                    "transitions, text and music, and play it.")
        self.queue_btn = QPushButton("Export each clip…")
        self.queue_btn.setToolTip("Export every clip as its own file with these settings. "
                                  "Exports run one after another in the queue (Ctrl+J).")
        side = QHBoxLayout()
        side.setSpacing(6)
        side.addWidget(self.preview_btn)
        side.addWidget(self.queue_btn)
        self.export_btn = QPushButton("Export…")
        self.export_btn.setMinimumHeight(38)
        self.export_btn.setStyleSheet("font-weight: bold; font-size: 11pt;")
        self.cancel_btn = QPushButton("Cancel current export")
        self.cancel_btn.hide()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: palette(placeholder-text);")
        foot.addWidget(self.summary)
        foot.addWidget(self.progress)
        foot.addLayout(side)
        foot.addWidget(self.export_btn)
        foot.addWidget(self.cancel_btn)
        foot.addWidget(self.status)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll, 1)
        outer.addWidget(footer)

        self.export_btn.clicked.connect(self.exportRequested)
        self.queue_btn.clicked.connect(self.queueRequested)
        self.preview_btn.clicked.connect(self.previewRequested)
        self.cancel_btn.clicked.connect(self.cancelRequested)
        self.platform.currentTextChanged.connect(self._apply_platform)
        self.format.currentIndexChanged.connect(self._fill_codecs)
        self.codec.currentIndexChanged.connect(self._fill_encoders)
        self.volume.valueChanged.connect(lambda v: self.volume_label.setText(f"{v}%"))
        self.music_volume.valueChanged.connect(lambda v: self.music_volume_label.setText(f"{v}%"))
        for w in self.fields.values():
            self._connect(w)
        self._fill_codecs()

    # -- field registry -------------------------------------------------------------------------

    def _reg(self, key: str, widget):
        self.fields[key] = widget
        return widget

    def _connect(self, w):
        if isinstance(w, QComboBox):
            (w.currentTextChanged if w.isEditable() else w.currentIndexChanged).connect(self._changed)
        elif isinstance(w, QCheckBox):
            w.toggled.connect(self._changed)
        elif isinstance(w, (QAbstractSpinBox, QSlider)):
            w.valueChanged.connect(self._changed)
        elif isinstance(w, QPlainTextEdit):
            w.textChanged.connect(self._changed)
        elif isinstance(w, (FilePicker, ColorButton)):
            w.changed.connect(self._changed)

    def get_state(self) -> dict:
        state = {}
        for key, w in self.fields.items():
            if isinstance(w, QComboBox):
                state[key] = w.currentText() if w.isEditable() else w.currentData()
            elif isinstance(w, QCheckBox):
                state[key] = w.isChecked()
            elif isinstance(w, (QAbstractSpinBox, QSlider)):
                state[key] = w.value()
            elif isinstance(w, QPlainTextEdit):
                state[key] = w.toPlainText()
            elif isinstance(w, FilePicker):
                state[key] = w.path()
            elif isinstance(w, ColorButton):
                state[key] = w.color()
        return state

    def set_state(self, state: dict):
        self._applying = True
        try:
            # Format and codec first: they decide which encoders are offered.
            order = ["format", "codec", "encoder"] + [k for k in state if k not in ("format", "codec", "encoder")]
            for key in order:
                if key not in state or key not in self.fields:
                    continue
                w, value = self.fields[key], state[key]
                if key in ("codec", "encoder") and not self._encoders:
                    if value:
                        self._saved_choice[key] = value
                    continue
                if isinstance(w, QComboBox):
                    if w.isEditable():
                        w.setCurrentText(str(value))
                    else:
                        if key in ("resolution", "aspect") and value is None:
                            value = 0 if key == "resolution" else ""
                        _select(w, value)
                elif isinstance(w, QCheckBox):
                    w.setChecked(bool(value))
                elif isinstance(w, QSpinBox):
                    w.setValue(int(value))
                elif isinstance(w, (QDoubleSpinBox, QSlider)):
                    w.setValue(type(w.value())(value))
                elif isinstance(w, QPlainTextEdit):
                    w.setPlainText(str(value))
                elif isinstance(w, FilePicker):
                    w.set_path(str(value or ""))
                elif isinstance(w, ColorButton):
                    w.set_color(str(value))
        finally:
            self._applying = False
        self._changed()

    def _apply_platform(self, name: str):
        if name == CUSTOM or self._applying:
            return
        self.set_state(preset_state(name))
        self.platform.blockSignals(True)
        self.platform.setCurrentText(name)
        self.platform.blockSignals(False)

    # -- encoders -------------------------------------------------------------------------------

    def set_encoders(self, encoders: list[Encoder]):
        self._encoders = encoders
        self._fill_codecs()

    def _fill_codecs(self):
        previous = self._saved_choice.get("codec") or self.codec.currentData()
        ext = self.extension
        self.codec.blockSignals(True)
        self.codec.clear()
        for codec, text in CODEC_LABELS.items():
            if any(e.codec == codec for e in self._encoders) and codec_allowed(ext, codec):
                self.codec.addItem(text, codec)
                self.codec.setItemData(self.codec.count() - 1, CODEC_TIPS[codec], Qt.ToolTipRole)
        _select(self.codec, previous)
        self.codec.blockSignals(False)
        self.codec.setToolTip(CODEC_TIPS.get(self.codec.currentData() or "", ""))
        self._fill_encoders()

    def _fill_encoders(self):
        previous = self._saved_choice.pop("encoder", None) or self.encoder.currentData()
        if self._encoders:
            self._saved_choice.pop("codec", None)
        self.codec.setToolTip(CODEC_TIPS.get(self.codec.currentData() or "", ""))
        self.encoder.blockSignals(True)
        self.encoder.clear()
        for e in self._encoders:
            if e.codec == self.codec.currentData():
                self.encoder.addItem(e.label, e.name)
                tip = ("Fast hardware encoding on your graphics card." if e.hardware else
                       "Software encoding on the processor: slower, slightly better compression.")
                self.encoder.setItemData(self.encoder.count() - 1, tip, Qt.ToolTipRole)
        _select(self.encoder, previous)
        self.encoder.blockSignals(False)
        self._changed()

    # -- state ----------------------------------------------------------------------------------

    @property
    def extension(self) -> str:
        return self.format.currentData()

    @property
    def kind(self) -> str:
        return output_kind("x" + self.extension)

    def _fps_value(self) -> str | None:
        text = self.fps.currentText().strip()
        if not text or text == FPS_CHOICES[0]:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
        return text if 1 <= value <= 240 else None

    def _changed(self, *_):
        if self._applying:
            return
        if self.sender() is not None and self.sender() is not self.platform and \
                self.platform.currentText() != CUSTOM and self.sender() in self.fields.values():
            self.platform.blockSignals(True)
            self.platform.setCurrentText(CUSTOM)
            self.platform.blockSignals(False)
        self._update_visibility()
        self.changed.emit()

    def _update_visibility(self):
        kind = self.kind
        mode = self.mode.currentData()
        copy = mode == "copy" and kind == "video"
        enc = ENCODERS.get(self.encoder.currentData() or "")
        visual = kind in ("video", "gif", "webp")
        audio_out = kind in ("video", "audio")

        v = self.sec_video
        for w in (self.codec, self.encoder, self.speed):
            v.set_row_visible(w, kind == "video")
        for w in (self.resolution, self.fps):
            v.set_row_visible(w, visual)
        v.set_row_visible(self.hw_decode, kind != "audio")
        for w in (self.codec, self.encoder, self.speed, self.resolution, self.fps, self.hw_decode):
            w.setEnabled(not copy)

        self.sec_frame.setVisible(visual)
        self.sec_frame.setEnabled(not copy)
        self.sec_text.setVisible(visual)
        self.sec_text.setEnabled(not copy)
        self.sec_music.setVisible(audio_out)
        self.sec_music.setEnabled(not copy)
        self.sec_trans.setEnabled(not copy)
        self.sec_trans.set_row_visible(self.transition, kind != "audio")
        self.sec_audio.setVisible(audio_out)

        c = self.sec_comp
        c.setVisible(kind != "audio")
        c.set_row_visible(self.mode, kind == "video")
        c.set_row_visible(self.preset, mode == "preset" or kind in ("gif", "webp"))
        size = kind == "video" and mode == "size"
        manual = kind == "video" and mode == "manual"
        for w in (self.size_mb, self.size_shortcuts, self.auto_res):
            c.set_row_visible(w, size)
        c.set_row_visible(self.two_pass, size and enc is not None and enc.family in TWO_PASS_FAMILIES)
        c.set_row_visible(self.rate, manual)
        c.set_row_visible(self.quality, manual and self.rate.currentData() == "quality")
        c.set_row_visible(self.bitrate, manual and self.rate.currentData() == "bitrate")

        has_track = not self.mute.isChecked() or bool(self.music.path())
        self.volume_row.setEnabled(not self.mute.isChecked() and not copy)
        self.normalize.setEnabled(has_track and not copy)
        self.audio_bitrate.setEnabled(has_track and not copy)
        self.audio_bitrate.setVisible(self.extension != ".wav")
        self.transition.setEnabled(self.transition_on.isChecked())
        self.crossfade.setEnabled(self.transition_on.isChecked())
        has_text = bool(self.text.toPlainText().strip())
        for w in (self.text_pos, self.text_size, self.text_color, self.text_box, self.text_start,
                  self.text_duration):
            w.setEnabled(has_text)
        for w in (self.logo_pos, self.logo_size, self.logo_opacity):
            w.setEnabled(bool(self.logo.path()))
        self.preview_btn.setEnabled(kind != "audio")
        # Exporting while another export runs just adds it to the queue.
        self.export_btn.setEnabled(enc is not None or kind != "video" or copy)

    def settings(self) -> ExportSettings:
        """Export settings without output path or overlays (the window renders those)."""
        kind = self.kind
        return ExportSettings(
            encoder=self.encoder.currentData() or "libx264",
            mode=self.mode.currentData() if kind == "video" else "preset",
            preset=self.preset.currentData(),
            target_size_mb=self.size_mb.value(),
            two_pass=self.two_pass.isChecked(),
            auto_resolution=self.auto_res.isChecked(),
            manual_rate=self.rate.currentData(),
            manual_quality=self.quality.value(),
            manual_bitrate_k=self.bitrate.value(),
            speed=self.speed.currentData(),
            height=self.resolution.currentData() or None,
            fps=self._fps_value(),
            aspect=self.aspect.currentData() or None,
            fit=self.fit.currentData(),
            hw_decode=self.hw_decode.isChecked(),
            mute=self.mute.isChecked(),
            volume=self.volume.value() / 100,
            audio_bitrate_k=self.audio_bitrate.currentData(),
            normalize=self.normalize.isChecked(),
            music=self.music.path() or None,
            music_volume=self.music_volume.value() / 100,
            music_loop=self.music_loop.isChecked(),
            crossfade=self.crossfade.value() if self.transition_on.isChecked() else 0.0,
            transition=self.transition.currentData(),
            fade_in=self.fade_in.value(),
            fade_out=self.fade_out.value(),
        )

    def overlay_spec(self) -> dict:
        return {
            "text": self.text.toPlainText().strip(),
            "text_pos": self.text_pos.currentData(),
            "text_size": self.text_size.value(),
            "text_color": self.text_color.color(),
            "text_box": self.text_box.isChecked(),
            "text_start": self.text_start.value(),
            "text_duration": self.text_duration.value(),
            "logo": self.logo.path(),
            "logo_pos": self.logo_pos.currentData(),
            "logo_size": self.logo_size.value(),
            "logo_opacity": self.logo_opacity.value() / 100,
        }

    def save_state(self, q: QSettings):
        q.setValue("export/state", json.dumps(self.get_state()))
        q.setValue("export/platform", self.platform.currentText())

    def restore_state(self, q: QSettings):
        raw = q.value("export/state")
        if raw:
            try:
                self.set_state(json.loads(raw))
            except (ValueError, TypeError):
                pass
        platform = q.value("export/platform", CUSTOM)
        if platform in PLATFORM_PRESETS:
            self.platform.blockSignals(True)
            self.platform.setCurrentText(platform)
            self.platform.blockSignals(False)

    # -- export progress ------------------------------------------------------------------------

    def set_busy(self, busy: bool):
        self._busy = busy
        self.progress.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        if busy and self.progress.value() <= 0:
            self.progress.setValue(0)
        self._update_visibility()

    def set_progress(self, fraction: float, text: str):
        self.progress.setValue(int(fraction * 1000))
        self.progress.setFormat(f"{fraction * 100:.0f}%")
        self.status.setText(text)
