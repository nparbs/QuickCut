"""The right-hand panel: output format, video, compression, audio and join settings."""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QProgressBar, QPushButton, QRadioButton, QSlider, QSpinBox, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .command import TWO_PASS_FAMILIES
from .ffmpeg import CODEC_LABELS, ENCODERS, Encoder
from .models import ExportSettings

FORMATS = [("MP4", ".mp4"), ("MKV", ".mkv"), ("MOV", ".mov")]
RESOLUTIONS = [("Same as source", None), ("2160p (4K)", 2160), ("1440p", 1440), ("1080p", 1080),
               ("720p", 720), ("480p", 480), ("360p", 360), ("240p", 240)]
FPS_CHOICES = ["Same as source", "120", "60", "50", "30", "25", "24", "15", "10"]
PRESETS = [("High quality", "high"), ("Balanced", "medium"), ("Small file", "low"),
           ("Smallest file", "smallest")]
MODES = [("Quality preset", "preset"), ("Target file size", "size"), ("Manual", "manual")]
SPEEDS = [("Balanced", "balanced"), ("Slower, better compression", "quality"), ("Faster", "fast")]
AUDIO_BITRATES = [64, 96, 128, 160, 192, 256, 320]
SIZE_SHORTCUTS = [8, 10, 25, 50, 100, 500]


def _combo(items, parent=None) -> QComboBox:
    box = QComboBox(parent)
    for label, data in items:
        box.addItem(label, data)
    return box


def _select(box: QComboBox, data):
    i = box.findData(data)
    if i >= 0:
        box.setCurrentIndex(i)


class ExportPanel(QWidget):
    changed = Signal()
    exportRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._encoders: list[Encoder] = []
        self._saved_choice: dict[str, str] = {}   # codec/encoder restored before detection finishes
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Output
        out_box = QGroupBox("Output")
        form = QFormLayout(out_box)
        self.format = _combo(FORMATS)
        form.addRow("Format", self.format)
        layout.addWidget(out_box)

        # Video
        video_box = QGroupBox("Video")
        form = QFormLayout(video_box)
        self.codec = QComboBox()
        self.encoder = QComboBox()
        self.encoder.setToolTip("GPU encoders are much faster. CPU encoders compress a little better "
                                "and hit target sizes more precisely (two-pass).")
        self.resolution = _combo(RESOLUTIONS)
        self.resolution.setToolTip("Height of the output. Width follows the first clip's shape; "
                                   "clips with a different shape get black bars.")
        self.fps = QComboBox()
        self.fps.setEditable(True)
        self.fps.addItems(FPS_CHOICES)
        self.fps.setToolTip("Pick a frame rate or type your own, e.g. 29.97")
        self.speed = _combo(SPEEDS)
        form.addRow("Codec", self.codec)
        form.addRow("Encoder", self.encoder)
        form.addRow("Resolution", self.resolution)
        form.addRow("Frame rate", self.fps)
        form.addRow("Encode speed", self.speed)
        layout.addWidget(video_box)

        # Compression
        comp_box = QGroupBox("Compression")
        comp = QVBoxLayout(comp_box)
        self.mode = _combo(MODES)
        comp.addWidget(self.mode)
        self.pages = QStackedWidget()
        comp.addWidget(self.pages)

        page = QWidget()
        f = QFormLayout(page)
        f.setContentsMargins(0, 0, 0, 0)
        self.preset = _combo(PRESETS)
        f.addRow("Quality", self.preset)
        self.pages.addWidget(page)

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.size_mb = QDoubleSpinBox()
        self.size_mb.setRange(0.5, 100000)
        self.size_mb.setDecimals(1)
        self.size_mb.setSuffix(" MB")
        self.size_mb.setValue(25)
        row.addWidget(QLabel("Max size"))
        row.addWidget(self.size_mb, 1)
        v.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(4)
        for mb in SIZE_SHORTCUTS:
            b = QPushButton(f"{mb}")
            b.setFixedWidth(40)
            b.setToolTip(f"{mb} MB")
            b.clicked.connect(lambda _=False, mb=mb: self.size_mb.setValue(mb))
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        self.two_pass = QCheckBox("Two-pass (more accurate size, CPU encoders only)")
        self.two_pass.setChecked(True)
        v.addWidget(self.two_pass)
        self.pages.addWidget(page)

        page = QWidget()
        f = QFormLayout(page)
        f.setContentsMargins(0, 0, 0, 0)
        self.rate_quality = QRadioButton("Constant quality")
        self.rate_bitrate = QRadioButton("Bitrate")
        self.rate_quality.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.rate_quality)
        group.addButton(self.rate_bitrate)
        self.quality = QSpinBox()
        self.quality.setRange(0, 63)
        self.quality.setValue(23)
        self.quality.setToolTip("CRF / CQ value. Lower = better quality, bigger file. "
                                "Typical: 18-28 for H.264, 22-32 for HEVC, 25-40 for AV1.")
        self.bitrate = QSpinBox()
        self.bitrate.setRange(100, 500000)
        self.bitrate.setSingleStep(500)
        self.bitrate.setValue(4000)
        self.bitrate.setSuffix(" kbps")
        f.addRow(self.rate_quality, self.quality)
        f.addRow(self.rate_bitrate, self.bitrate)
        self.pages.addWidget(page)
        layout.addWidget(comp_box)

        # Audio
        audio_box = QGroupBox("Audio")
        form = QFormLayout(audio_box)
        self.mute = QCheckBox("Remove audio")
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 300)
        self.volume.setValue(100)
        self.volume_label = QLabel("100%")
        self.volume_label.setMinimumWidth(40)
        row = QHBoxLayout()
        row.addWidget(self.volume, 1)
        row.addWidget(self.volume_label)
        self.audio_bitrate = _combo([(f"{k} kbps", k) for k in AUDIO_BITRATES])
        _select(self.audio_bitrate, 160)
        form.addRow(self.mute)
        form.addRow("Volume", row)
        form.addRow("Bitrate", self.audio_bitrate)
        layout.addWidget(audio_box)

        # Join
        join_box = QGroupBox("Joining clips")
        form = QFormLayout(join_box)
        self.crossfade_on = QCheckBox("Crossfade between clips")
        self.crossfade = QDoubleSpinBox()
        self.crossfade.setRange(0.1, 5.0)
        self.crossfade.setSingleStep(0.25)
        self.crossfade.setValue(0.5)
        self.crossfade.setSuffix(" s")
        form.addRow(self.crossfade_on)
        form.addRow("Fade length", self.crossfade)
        layout.addWidget(join_box)

        # Summary + export
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        layout.addWidget(self.summary)
        self.export_btn = QPushButton("Export…")
        self.export_btn.setMinimumHeight(38)
        self.export_btn.setStyleSheet("font-weight: bold; font-size: 11pt;")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.hide()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.export_btn)
        layout.addWidget(self.cancel_btn)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addStretch()

        self.export_btn.clicked.connect(self.exportRequested)
        self.cancel_btn.clicked.connect(self.cancelRequested)
        self.codec.currentIndexChanged.connect(self._fill_encoders)
        self.mode.currentIndexChanged.connect(lambda i: self.pages.setCurrentIndex(i))
        self.volume.valueChanged.connect(lambda v: self.volume_label.setText(f"{v}%"))
        for w in (self.format, self.encoder, self.resolution, self.speed, self.mode, self.preset,
                  self.audio_bitrate):
            w.currentIndexChanged.connect(self._changed)
        self.fps.currentTextChanged.connect(self._changed)
        for w in (self.size_mb, self.crossfade, self.quality, self.bitrate, self.volume):
            w.valueChanged.connect(self._changed)
        for w in (self.two_pass, self.mute, self.crossfade_on, self.rate_quality):
            w.toggled.connect(self._changed)
        self._changed()

    # -- encoders -----------------------------------------------------------------------------

    def set_encoders(self, encoders: list[Encoder]):
        self._encoders = encoders
        previous = self._saved_choice.get("codec") or self.codec.currentData()
        self.codec.blockSignals(True)
        self.codec.clear()
        for codec, label in CODEC_LABELS.items():
            if any(e.codec == codec for e in encoders):
                self.codec.addItem(label, codec)
        _select(self.codec, previous)
        self.codec.blockSignals(False)
        self._fill_encoders()

    def _fill_encoders(self):
        previous = self._saved_choice.pop("encoder", None) or self.encoder.currentData()
        self._saved_choice.pop("codec", None)
        self.encoder.blockSignals(True)
        self.encoder.clear()
        for e in self._encoders:
            if e.codec == self.codec.currentData():
                self.encoder.addItem(e.label, e.name)
        _select(self.encoder, previous)
        self.encoder.blockSignals(False)
        self._changed()

    # -- state --------------------------------------------------------------------------------

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
        enc = ENCODERS.get(self.encoder.currentData() or "")
        self.two_pass.setEnabled(enc is not None and enc.family in TWO_PASS_FAMILIES)
        self.quality.setEnabled(self.rate_quality.isChecked())
        self.bitrate.setEnabled(not self.rate_quality.isChecked())
        self.volume.setEnabled(not self.mute.isChecked())
        self.audio_bitrate.setEnabled(not self.mute.isChecked())
        self.crossfade.setEnabled(self.crossfade_on.isChecked())
        self.export_btn.setEnabled(enc is not None and not self.progress.isVisible())
        self.changed.emit()

    @property
    def extension(self) -> str:
        return self.format.currentData()

    def settings(self) -> ExportSettings:
        return ExportSettings(
            encoder=self.encoder.currentData() or "libx264",
            mode=self.mode.currentData(),
            preset=self.preset.currentData(),
            target_size_mb=self.size_mb.value(),
            two_pass=self.two_pass.isChecked(),
            manual_rate="quality" if self.rate_quality.isChecked() else "bitrate",
            manual_quality=self.quality.value(),
            manual_bitrate_k=self.bitrate.value(),
            speed=self.speed.currentData(),
            height=self.resolution.currentData(),
            fps=self._fps_value(),
            mute=self.mute.isChecked(),
            volume=self.volume.value() / 100,
            audio_bitrate_k=self.audio_bitrate.currentData(),
            crossfade=self.crossfade.value() if self.crossfade_on.isChecked() else 0.0,
        )

    def save_state(self, q: QSettings):
        q.beginGroup("export")
        for key, box in self._combos().items():
            q.setValue(key, box.currentData())
        q.setValue("fps", self.fps.currentText())
        q.setValue("size_mb", self.size_mb.value())
        q.setValue("two_pass", self.two_pass.isChecked())
        q.setValue("manual_bitrate", self.rate_bitrate.isChecked())
        q.setValue("quality", self.quality.value())
        q.setValue("bitrate", self.bitrate.value())
        q.setValue("mute", self.mute.isChecked())
        q.setValue("volume", self.volume.value())
        q.setValue("crossfade_on", self.crossfade_on.isChecked())
        q.setValue("crossfade", self.crossfade.value())
        q.endGroup()

    def restore_state(self, q: QSettings):
        q.beginGroup("export")
        try:
            for key in ("codec", "encoder"):
                if q.value(key):
                    self._saved_choice[key] = str(q.value(key))
            for key, box in self._combos().items():
                if q.contains(key):
                    value = q.value(key)
                    if key in ("resolution", "audio_bitrate") and value not in (None, ""):
                        value = int(value)
                    _select(box, value)
            self.fps.setCurrentText(str(q.value("fps", FPS_CHOICES[0])))
            self.size_mb.setValue(float(q.value("size_mb", 25)))
            self.two_pass.setChecked(q.value("two_pass", True, type=bool))
            (self.rate_bitrate if q.value("manual_bitrate", False, type=bool)
             else self.rate_quality).setChecked(True)
            self.quality.setValue(int(q.value("quality", 23)))
            self.bitrate.setValue(int(q.value("bitrate", 4000)))
            self.mute.setChecked(q.value("mute", False, type=bool))
            self.volume.setValue(int(q.value("volume", 100)))
            self.crossfade_on.setChecked(q.value("crossfade_on", False, type=bool))
            self.crossfade.setValue(float(q.value("crossfade", 0.5)))
        finally:
            q.endGroup()

    def _combos(self) -> dict[str, QComboBox]:
        return {"format": self.format, "codec": self.codec, "encoder": self.encoder,
                "resolution": self.resolution, "speed": self.speed, "mode": self.mode,
                "preset": self.preset, "audio_bitrate": self.audio_bitrate}

    # -- export progress ----------------------------------------------------------------------

    def set_busy(self, busy: bool):
        self.progress.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.export_btn.setVisible(not busy)
        if busy:
            self.progress.setValue(0)
        self._changed()

    def set_progress(self, fraction: float, text: str):
        self.progress.setValue(int(fraction * 1000))
        self.progress.setFormat(f"{fraction * 100:.0f}%")
        self.status.setText(text)
