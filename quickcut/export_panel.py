"""The right-hand panel: output format, video, compression, audio and join settings.

Settings scroll; the summary, Export button and progress stay pinned at the bottom.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget,
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
RATES = [("Constant quality", "quality"), ("Fixed bitrate", "bitrate")]
SPEEDS = [("Balanced", "balanced"), ("Slower, smaller file", "quality"), ("Faster", "fast")]
AUDIO_BITRATES = [64, 96, 128, 160, 192, 256, 320]
SIZE_SHORTCUTS = [8, 10, 25, 50, 100, 500]
LABEL_WIDTH = 92


def _combo(items) -> QComboBox:
    box = QComboBox()
    for label, data in items:
        box.addItem(label, data)
    return box


def _select(box: QComboBox, data):
    i = box.findData(data)
    if i >= 0:
        box.setCurrentIndex(i)


def _group(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(8)
    form.setContentsMargins(10, 12, 10, 10)
    return box, form


def _label(text: str) -> QLabel:
    label = QLabel(text)
    label.setFixedWidth(LABEL_WIDTH)  # same column width in every group, so fields line up
    return label


class ExportPanel(QWidget):
    changed = Signal()
    exportRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._encoders: list[Encoder] = []
        self._saved_choice: dict[str, str] = {}   # codec/encoder restored before detection finishes
        self._busy = False

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(8, 4, 12, 8)
        body.setSpacing(10)

        # Output
        box, form = _group("Output")
        self.format = _combo(FORMATS)
        form.addRow(_label("Format"), self.format)
        body.addWidget(box)

        # Video
        box, form = _group("Video")
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
        form.addRow(_label("Codec"), self.codec)
        form.addRow(_label("Encoder"), self.encoder)
        form.addRow(_label("Resolution"), self.resolution)
        form.addRow(_label("Frame rate"), self.fps)
        form.addRow(_label("Speed"), self.speed)
        body.addWidget(box)

        # Compression: one form, rows shown or hidden per mode so nothing leaves a gap.
        box, form = _group("Compression")
        self.comp_form = form
        self.mode = _combo(MODES)
        form.addRow(_label("Mode"), self.mode)

        self.preset = _combo(PRESETS)
        form.addRow(_label("Quality"), self.preset)

        self.size_mb = QDoubleSpinBox()
        self.size_mb.setRange(0.5, 100000)
        self.size_mb.setDecimals(1)
        self.size_mb.setSuffix(" MB")
        self.size_mb.setValue(25)
        form.addRow(_label("Max size"), self.size_mb)
        self.size_shortcuts = shortcuts = QWidget()
        row = QHBoxLayout(shortcuts)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for mb in SIZE_SHORTCUTS:
            b = QPushButton(str(mb))
            b.setToolTip(f"{mb} MB")
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.setMinimumWidth(28)
            b.clicked.connect(lambda _=False, mb=mb: self.size_mb.setValue(mb))
            row.addWidget(b)
        form.addRow(_label(""), shortcuts)
        self.two_pass = QCheckBox("Two-pass encoding")
        self.two_pass.setToolTip("Encodes twice to hit the size more precisely. CPU encoders only.")
        self.two_pass.setChecked(True)
        form.addRow(_label(""), self.two_pass)

        self.rate = _combo(RATES)
        form.addRow(_label("Rate control"), self.rate)
        self.quality = QSpinBox()
        self.quality.setRange(0, 63)
        self.quality.setValue(23)
        self.quality.setToolTip("CRF / CQ value. Lower = better quality, bigger file. "
                                "Typical: 18-28 for H.264, 22-32 for HEVC, 25-40 for AV1.")
        form.addRow(_label("CRF / CQ"), self.quality)
        self.bitrate = QSpinBox()
        self.bitrate.setRange(100, 500000)
        self.bitrate.setSingleStep(500)
        self.bitrate.setValue(4000)
        self.bitrate.setSuffix(" kbps")
        form.addRow(_label("Bitrate"), self.bitrate)
        body.addWidget(box)

        # Audio
        box, form = _group("Audio")
        self.mute = QCheckBox("Remove audio")
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 300)
        self.volume.setValue(100)
        self.volume_label = QLabel("100%")
        self.volume_label.setFixedWidth(40)
        self.volume_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        volume_row = QWidget()
        row = QHBoxLayout(volume_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.volume, 1)
        row.addWidget(self.volume_label)
        self.audio_bitrate = _combo([(f"{k} kbps", k) for k in AUDIO_BITRATES])
        _select(self.audio_bitrate, 160)
        form.addRow(_label(""), self.mute)
        form.addRow(_label("Volume"), volume_row)
        form.addRow(_label("Bitrate"), self.audio_bitrate)
        body.addWidget(box)

        # Join
        box, form = _group("Joining clips")
        self.crossfade_on = QCheckBox("Crossfade between clips")
        self.crossfade = QDoubleSpinBox()
        self.crossfade.setRange(0.1, 5.0)
        self.crossfade.setSingleStep(0.25)
        self.crossfade.setValue(0.5)
        self.crossfade.setSuffix(" s")
        form.addRow(_label(""), self.crossfade_on)
        form.addRow(_label("Fade length"), self.crossfade)
        body.addWidget(box)
        body.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        # Never narrower than the settings themselves, so nothing gets clipped on the right.
        scroll.setMinimumWidth(content.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width())

        # Pinned footer: summary, export, progress
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
        self.export_btn = QPushButton("Export…")
        self.export_btn.setMinimumHeight(38)
        self.export_btn.setStyleSheet("font-weight: bold; font-size: 11pt;")
        self.cancel_btn = QPushButton("Cancel export")
        self.cancel_btn.setMinimumHeight(38)
        self.cancel_btn.hide()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(True)
        self.progress.hide()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: palette(placeholder-text);")
        foot.addWidget(self.summary)
        foot.addWidget(self.progress)
        foot.addWidget(self.export_btn)
        foot.addWidget(self.cancel_btn)
        foot.addWidget(self.status)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll, 1)
        outer.addWidget(footer)

        self.export_btn.clicked.connect(self.exportRequested)
        self.cancel_btn.clicked.connect(self.cancelRequested)
        self.codec.currentIndexChanged.connect(self._fill_encoders)
        self.volume.valueChanged.connect(lambda v: self.volume_label.setText(f"{v}%"))
        for w in (self.format, self.encoder, self.resolution, self.speed, self.mode, self.preset,
                  self.rate, self.audio_bitrate):
            w.currentIndexChanged.connect(self._changed)
        self.fps.currentTextChanged.connect(self._changed)
        for w in (self.size_mb, self.crossfade, self.quality, self.bitrate, self.volume):
            w.valueChanged.connect(self._changed)
        for w in (self.two_pass, self.mute, self.crossfade_on):
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
        mode = self.mode.currentData()
        manual_bitrate = self.rate.currentData() == "bitrate"
        f = self.comp_form
        f.setRowVisible(self.preset, mode == "preset")
        f.setRowVisible(self.size_mb, mode == "size")
        f.setRowVisible(self.size_shortcuts, mode == "size")
        f.setRowVisible(self.two_pass, mode == "size" and enc is not None and enc.family in TWO_PASS_FAMILIES)
        f.setRowVisible(self.rate, mode == "manual")
        f.setRowVisible(self.quality, mode == "manual" and not manual_bitrate)
        f.setRowVisible(self.bitrate, mode == "manual" and manual_bitrate)
        self.volume.setEnabled(not self.mute.isChecked())
        self.audio_bitrate.setEnabled(not self.mute.isChecked())
        self.crossfade.setEnabled(self.crossfade_on.isChecked())
        self.export_btn.setEnabled(enc is not None and not self._busy)
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
            manual_rate=self.rate.currentData(),
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
                "preset": self.preset, "rate": self.rate, "audio_bitrate": self.audio_bitrate}

    # -- export progress ----------------------------------------------------------------------

    def set_busy(self, busy: bool):
        self._busy = busy
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
