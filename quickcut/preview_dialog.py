"""Plays a quick render of the whole edit."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QVBoxLayout


class PreviewDialog(QDialog):
    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle("Preview of the full edit (low resolution)")
        self.resize(960, 600)
        self.video = QVideoWidget()
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.play_btn = QPushButton()
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self.slider = QSlider(Qt.Horizontal)
        self.time = QLabel("0:00")
        close = QPushButton("Close")
        row = QHBoxLayout()
        row.addWidget(self.play_btn)
        row.addWidget(self.slider, 1)
        row.addWidget(self.time)
        row.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.video, 1)
        layout.addLayout(row)
        note = QLabel("This is a fast, low-resolution render to check timing, transitions, text and "
                      "music. The real export uses your full settings.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(note)

        self.play_btn.clicked.connect(self._toggle)
        close.clicked.connect(self.close)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, d))
        self.player.positionChanged.connect(self._position)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.player.playbackStateChanged.connect(self._state)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def _toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _state(self, state):
        icon = QStyle.SP_MediaPause if state == QMediaPlayer.PlayingState else QStyle.SP_MediaPlay
        self.play_btn.setIcon(self.style().standardIcon(icon))

    def _position(self, ms: int):
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.time.setText(f"{ms // 60000}:{ms // 1000 % 60:02d}")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space:
            self._toggle()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self.player.stop()
        self.player.setSource(QUrl())
        try:
            os.remove(self.path)
        except OSError:
            pass
        super().closeEvent(e)
