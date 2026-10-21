"""Keep one QuickCut window: later launches (e.g. from Explorer) hand their files to the first."""
from __future__ import annotations

import getpass
import json
import os
import tempfile
import time

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = f"QuickCut-{getpass.getuser()}"
HANDOFF_SECONDS = 8


def send_to_running(files: list[str], timeout_ms: int = 800) -> bool:
    """True if another QuickCut accepted the files (this process should then exit)."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(json.dumps(files).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    filesReceived = Signal(list)

    def __init__(self, lock: QLockFile | None = None, parent=None):
        super().__init__(parent)
        self.lock = lock  # held for the life of the primary instance
        self.server = QLocalServer(self)
        if not self.server.listen(SERVER_NAME):
            # A stale name from a crashed run; clear it and try again.
            QLocalServer.removeServer(SERVER_NAME)
            self.server.listen(SERVER_NAME)
        self.server.newConnection.connect(self._accept)

    def close(self):
        self.server.close()
        if self.lock is not None:
            self.lock.unlock()

    def _accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()

            def read(sock=sock, buf=buf):
                buf.extend(bytes(sock.readAll()))

            def done(sock=sock, buf=buf):
                read()
                try:
                    files = json.loads(buf.decode("utf-8") or "[]")
                except ValueError:
                    files = []
                self.filesReceived.emit([f for f in files if isinstance(f, str)])
                sock.deleteLater()

            sock.readyRead.connect(read)
            sock.disconnected.connect(done)


def become_primary(files: list[str]) -> InstanceServer | None:
    """Returns a server if this process should open the window, or None if the files were
    handed to a QuickCut that's already running.

    Explorer starts one process per selected file, all at once; Windows lets several processes
    listen on the same pipe name, so a lock file decides which one becomes the window.
    """
    lock = QLockFile(os.path.join(tempfile.gettempdir(), f"{SERVER_NAME}.lock"))
    lock.setStaleLockTime(0)  # only a dead owner makes the lock stale, never its age
    deadline = time.monotonic() + HANDOFF_SECONDS
    while True:
        if lock.tryLock(50):
            return InstanceServer(lock)
        # Someone else is (or is becoming) the primary; its server may need a moment to start.
        if send_to_running(files, 300):
            return None
        if time.monotonic() > deadline:
            return InstanceServer()  # primary is stuck; open our own window rather than nothing
