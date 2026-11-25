"""Windows Explorer right-click "Open with QuickCut" and "Convert to MP4", registered for the
current user only."""
from __future__ import annotations

import sys
from pathlib import Path

SUPPORTED = sys.platform == "win32"
VERB = "QuickCut"
CONVERT_VERB = "QuickCutConvertMP4"
CONVERT_FLAG = "--convert-mp4"
EXTENSIONS = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg",
              ".ts", ".mts", ".m2ts", ".3gp"]
PROJECT_EXT = ".qcut"
# Videos that "Convert to MP4" is offered on (everything but .mp4 itself).
CONVERT_EXTENSIONS = [e for e in EXTENSIONS if e != ".mp4"]


def launch_command(flag: str = "") -> tuple[str, str]:
    """(command line with %1, icon path) for the running copy of QuickCut."""
    extra = f"{flag} " if flag else ""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return f'"{exe}" {extra}"%1"', exe
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    runner = Path(__file__).resolve().parent.parent / "run.pyw"
    return f'"{pythonw}" "{runner}" {extra}"%1"', str(pythonw)


def _key(ext: str, verb: str = VERB) -> str:
    return rf"Software\Classes\SystemFileAssociations\{ext}\shell\{verb}"


def _entries() -> list[tuple[str, str, str, str]]:
    """(extension, verb, menu text, command flag) for every menu entry QuickCut adds."""
    entries = [(ext, VERB, "Open with QuickCut", "") for ext in EXTENSIONS + [PROJECT_EXT]]
    entries += [(ext, CONVERT_VERB, "Convert to MP4 (QuickCut)", CONVERT_FLAG)
                for ext in CONVERT_EXTENSIONS]
    return entries


def is_registered() -> bool:
    if not SUPPORTED:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _key(".mp4")):
            return True
    except OSError:
        return False


def needs_refresh() -> bool:
    """True when the menu is on but missing an entry or pointing at another copy of QuickCut
    (e.g. it was turned on before Convert to MP4 existed, or QuickCut moved)."""
    if not is_registered():
        return False
    import winreg
    for flag, verb in (("", VERB), (CONVERT_FLAG, CONVERT_VERB)):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _key(".mov", verb) + r"\command") as k:
                if winreg.QueryValueEx(k, None)[0] != launch_command(flag)[0]:
                    return True
        except OSError:
            return True
    return False


def register():
    import winreg
    for ext, verb, text, flag in _entries():
        command, icon = launch_command(flag)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _key(ext, verb)) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, text)
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
            # Lets one Explorer action pass several selected files at once.
            winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _key(ext, verb) + r"\command") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, command)
    _notify()


def unregister():
    import winreg
    for ext, verb, _text, _flag in _entries():
        for sub in (r"\command", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _key(ext, verb) + sub)
            except OSError:
                pass
    _notify()


def _notify():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except (AttributeError, OSError):
        pass
