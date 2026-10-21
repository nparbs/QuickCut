"""Build a standalone Windows copy of QuickCut with PyInstaller.

    python packaging/build.py --ffmpeg-dir "C:/path/to/ffmpeg/bin"

Produces dist/QuickCut/ (run QuickCut.exe) and dist/QuickCut-<version>-windows-x64.zip.
--ffmpeg-dir is the folder holding ffmpeg.exe and ffprobe.exe; for "shared" FFmpeg builds the
DLLs next to them are copied too. Without it, the build expects FFmpeg to be installed separately.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from quickcut import __version__  # noqa: E402

DIST = ROOT / "dist"
APP = DIST / "QuickCut"


def build_exe():
    work = ROOT / "build"
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", "QuickCut",
        "--icon", str(ROOT / "packaging" / "quickcut.ico"),
        "--add-data", f"{ROOT / 'quickcut' / 'resources'}{os.pathsep}quickcut/resources",
        "--exclude-module", "tkinter",
        "--distpath", str(DIST), "--workpath", str(work), "--specpath", str(work),
        str(ROOT / "packaging" / "entry.py"),
    ], check=True, cwd=ROOT)


def bundle_ffmpeg(src: Path):
    exe = ".exe" if sys.platform == "win32" else ""
    for name in ("ffmpeg", "ffprobe"):
        if not (src / f"{name}{exe}").is_file():
            raise SystemExit(f"{name}{exe} not found in {src}")
    dest = APP / "ffmpeg" / "bin"
    dest.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.name in (f"ffmpeg{exe}", f"ffprobe{exe}") or f.suffix.lower() == ".dll":
            shutil.copy2(f, dest / f.name)
    # FFmpeg's licence travels with it (usually one folder up from bin/).
    for candidate in (src / "LICENSE.txt", src.parent / "LICENSE.txt", src.parent / "LICENSE"):
        if candidate.is_file():
            shutil.copy2(candidate, APP / "ffmpeg" / "LICENSE.txt")
            break


def smoke_test():
    result = Path(tempfile.mkdtemp()) / "smoke.json"
    env = dict(os.environ, QUICKCUT_SMOKE_TEST=str(result),
               QUICKCUT_SETTINGS=str(result.with_name("settings.ini")))
    env.pop("QUICKCUT_FFMPEG_DIR", None)
    subprocess.run([str(APP / "QuickCut.exe")], env=env, timeout=120, check=True)
    data = json.loads(result.read_text(encoding="utf-8"))
    print(f"Smoke test: QuickCut {data['version']} started, ffmpeg = {data['ffmpeg']}")
    print(f"            encoders offered: {', '.join(data['encoders'])}")
    return data


def make_zip() -> Path:
    archive = DIST / f"QuickCut-{__version__}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in APP.rglob("*"):
            z.write(f, Path("QuickCut") / f.relative_to(APP))
    return archive


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ffmpeg-dir", type=Path, help="folder with ffmpeg.exe and ffprobe.exe to bundle")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--no-smoke-test", action="store_true")
    args = ap.parse_args()

    build_exe()
    for doc in ("README.md", "LICENSE", "THIRD_PARTY.md"):
        if (ROOT / doc).is_file():
            shutil.copy2(ROOT / doc, APP / doc)
    if args.ffmpeg_dir:
        bundle_ffmpeg(args.ffmpeg_dir)
        if not args.no_smoke_test:
            data = smoke_test()
            if not Path(data["ffmpeg"]).resolve().is_relative_to(APP.resolve()):
                raise SystemExit("The packaged app didn't use its bundled FFmpeg")
    if not args.no_zip:
        print(f"Wrote {make_zip()}")


if __name__ == "__main__":
    main()
