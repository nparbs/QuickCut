import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__, shell_integration
from .config import app_settings
from .ffmpeg import find_tools
from .main_window import MainWindow
from .single_instance import SERVER_NAME, become_primary
from .theme import apply_theme

ICON = Path(__file__).with_name("resources") / "icon.png"


def _smoke_test(window: MainWindow, result_path: str):
    """QUICKCUT_SMOKE_TEST=<file>: once encoders are detected, write what was found and quit.
    Lets the build check that a packaged copy starts and finds its bundled FFmpeg."""
    def check():
        if window.panel.encoder.count() == 0:
            QTimer.singleShot(200, check)
            return
        Path(result_path).write_text(json.dumps({
            "version": __version__, "ffmpeg": window.tools.ffmpeg,
            "encoders": [window.panel.encoder.itemData(i) for i in range(window.panel.encoder.count())],
        }), encoding="utf-8")
        window._dirty = False
        window.close()
    QTimer.singleShot(200, check)


def _tools_or_warn():
    tools = find_tools()
    if tools is None:
        QMessageBox.critical(None, "QuickCut needs FFmpeg", (
            "QuickCut couldn't find ffmpeg and ffprobe.\n\n"
            "Install FFmpeg (for example: winget install Gyan.FFmpeg), then restart QuickCut. "
            "You can also put an 'ffmpeg' folder next to QuickCut or set QUICKCUT_FFMPEG_DIR."))
    return tools


def _convert(app: QApplication, files: list[str]) -> int:
    """Explorer's "Convert to MP4": a small converter window instead of the editor. Explorer
    starts one process per selected file, so they gather into one window like the editor does."""
    from .quick_convert import ConvertWindow
    server = become_primary(files, f"{SERVER_NAME}-convert")
    if server is None:
        return 0
    apply_theme(app, app_settings().value("view/theme", "system"))
    tools = _tools_or_warn()
    if tools is None:
        server.close()
        return 1
    window = ConvertWindow(tools)
    server.filesReceived.connect(lambda paths: (window.add_files(paths), window.bring_to_front()))
    window.show()
    window.add_files(files)
    code = app.exec()
    server.close()
    return code


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("QuickCut")
    app.setOrganizationName("QuickCut")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(ICON)))
    files = sys.argv[1:]
    if files and files[0] == shell_integration.CONVERT_FLAG:
        return _convert(app, files[1:])

    # A second launch (e.g. "Open with QuickCut" on several files) hands its files to the first window.
    server = become_primary(files)
    if server is None:
        return 0

    settings = app_settings()
    apply_theme(app, settings.value("view/theme", "system"))
    app.styleHints().colorSchemeChanged.connect(
        lambda _: apply_theme(app, "system") if settings.value("view/theme", "system") == "system" else None)

    tools = _tools_or_warn()
    if tools is None:
        server.close()
        return 1
    try:
        # Menu turned on by an older version (no Convert to MP4) or a copy that has since moved.
        if shell_integration.needs_refresh():
            shell_integration.register()
    except OSError:
        pass

    window = MainWindow(tools)
    server.filesReceived.connect(lambda paths: (window.add_files(paths) if paths else None,
                                                window.bring_to_front()))
    window.show()
    if not os.environ.get("QUICKCUT_SMOKE_TEST"):
        window.offer_recovery()
    if files:
        window.add_files(files)
    if os.environ.get("QUICKCUT_SMOKE_TEST"):
        _smoke_test(window, os.environ["QUICKCUT_SMOKE_TEST"])
    code = app.exec()
    # Tear down media and worker threads while Qt is still alive, or the process hangs on exit.
    window.shutdown()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    server.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
