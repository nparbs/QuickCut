import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from .ffmpeg import find_tools
from .main_window import MainWindow


def dark_palette() -> QPalette:
    p = QPalette()
    colors = {
        QPalette.Window: "#202124", QPalette.WindowText: "#e8eaed", QPalette.Base: "#2b2c30",
        QPalette.AlternateBase: "#323338", QPalette.ToolTipBase: "#2b2c30",
        QPalette.ToolTipText: "#e8eaed", QPalette.Text: "#e8eaed", QPalette.Button: "#303135",
        QPalette.ButtonText: "#e8eaed", QPalette.BrightText: "#ffffff", QPalette.Highlight: "#c98f12",
        QPalette.HighlightedText: "#000000", QPalette.Link: "#f5b82e",
        QPalette.PlaceholderText: "#9aa0a6",
    }
    for role, color in colors.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor("#6f7378"))
    return p


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("QuickCut")
    app.setStyle("Fusion")
    app.setPalette(dark_palette())

    tools = find_tools()
    if tools is None:
        QMessageBox.critical(None, "QuickCut needs FFmpeg", (
            "QuickCut couldn't find ffmpeg and ffprobe.\n\n"
            "Install FFmpeg (for example: winget install Gyan.FFmpeg), then restart QuickCut. "
            "You can also put an 'ffmpeg' folder next to QuickCut or set QUICKCUT_FFMPEG_DIR."))
        return 1

    window = MainWindow(tools)
    window.show()
    if len(sys.argv) > 1:
        window.add_files(sys.argv[1:])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
