"""Windows Explorer right-click "Open with QuickCut", registered for the current user only."""
from __future__ import annotations

import sys
from pathlib import Path

SUPPORTED = sys.platform == "win32"
VERB = "QuickCut"
EXTENSIONS = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg",
              ".ts", ".mts", ".m2ts", ".3gp"]
PROJECT_EXT = ".qcut"


def launch_command() -> tuple[str, str]:
    """(command line with %1, icon path) for the running copy of QuickCut."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return f'"{exe}" "%1"', exe
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    runner = Path(__file__).resolve().parent.parent / "run.pyw"
    return f'"{pythonw}" "{runner}" "%1"', str(pythonw)


def _key(ext: str) -> str:
    return rf"Software\Classes\SystemFileAssociations\{ext}\shell\{VERB}"


def is_registered() -> bool:
    if not SUPPORTED:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _key(".mp4")):
            return True
    except OSError:
        return False


def register():
    import winreg
    command, icon = launch_command()
    for ext in EXTENSIONS + [PROJECT_EXT]:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _key(ext)) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, "Open with QuickCut")
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
            # Lets one Explorer action pass several selected files at once.
            winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _key(ext) + r"\command") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, command)
    _notify()


def unregister():
    import winreg
    for ext in EXTENSIONS + [PROJECT_EXT]:
        for sub in (r"\command", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _key(ext) + sub)
            except OSError:
                pass
    _notify()


def _notify():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except (AttributeError, OSError):
        pass
