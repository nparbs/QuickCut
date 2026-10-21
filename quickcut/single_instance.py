"""Keep one QuickCut window: later launches (e.g. from Explorer) hand their files to the first."""
from __future__ import annotations

import getpass
import json

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = f"QuickCut-{getpass.getuser()}"


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        if not self.server.listen(SERVER_NAME):
            # A stale name from a crashed run; clear it and try again.
            QLocalServer.removeServer(SERVER_NAME)
            self.server.listen(SERVER_NAME)
        self.server.newConnection.connect(self._accept)

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
