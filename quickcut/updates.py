"""Opt-in check for a newer QuickCut release on GitHub."""
from __future__ import annotations

import json
import re

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__

REPO = "nparbs/QuickCut"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3]) or (0,)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


class UpdateChecker(QObject):
    # (latest version, release page url); latest is "" if already up to date
    result = Signal(str, str)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.net = QNetworkAccessManager(self)

    def check(self):
        req = QNetworkRequest(QUrl(RELEASES_API))
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        req.setRawHeader(b"User-Agent", f"QuickCut/{__version__}".encode())
        reply = self.net.get(req)
        reply.finished.connect(lambda: self._done(reply))

    def _done(self, reply: QNetworkReply):
        reply.deleteLater()
        if reply.error() != QNetworkReply.NoError:
            # 404 just means no release has been published yet.
            if reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) == 404:
                self.result.emit("", RELEASES_PAGE)
            else:
                self.failed.emit(reply.errorString())
            return
        try:
            data = json.loads(bytes(reply.readAll()).decode("utf-8"))
        except ValueError:
            self.failed.emit("Unexpected reply from GitHub")
            return
        tag = data.get("tag_name", "")
        url = data.get("html_url", RELEASES_PAGE)
        self.result.emit(tag.lstrip("v") if is_newer(tag) else "", url)
