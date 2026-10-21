"""Saving and opening QuickCut projects (.qcut files: JSON with the clips and export settings)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import __version__
from .models import Clip

PROJECT_EXT = ".qcut"
FORMAT_VERSION = 1
CLIP_FIELDS = ("start", "end", "speed", "rotate", "hflip", "vflip", "reverse")


def clip_to_dict(clip: Clip, project_dir: Path) -> dict:
    data = {"path": clip.info.path}
    try:
        data["relative"] = os.path.relpath(clip.info.path, project_dir)
    except ValueError:  # different drive on Windows
        pass
    data.update({k: getattr(clip, k) for k in CLIP_FIELDS})
    return data


def save_project(path: str, clips: list[Clip], export_state: dict):
    project_dir = Path(path).resolve().parent
    data = {
        "app": "QuickCut",
        "app_version": __version__,
        "format": FORMAT_VERSION,
        "clips": [clip_to_dict(c, project_dir) for c in clips],
        "export": export_state,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load_project(path: str) -> tuple[list[dict], dict]:
    """(clip entries with a resolved 'path' or 'missing': True, export state)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("app") != "QuickCut":
        raise ValueError("This isn't a QuickCut project file.")
    if data.get("format", 0) > FORMAT_VERSION:
        raise ValueError("This project was saved by a newer version of QuickCut.")
    project_dir = Path(path).resolve().parent
    clips = []
    for entry in data.get("clips", []):
        entry = dict(entry)
        candidates = [entry.get("path", "")]
        if entry.get("relative"):
            candidates.append(str(project_dir / entry["relative"]))
        found = next((p for p in candidates if p and os.path.isfile(p)), None)
        entry["missing"] = found is None
        if found:
            entry["path"] = found
        clips.append(entry)
    return clips, data.get("export", {})


def apply_clip_fields(clip: Clip, entry: dict):
    for key in CLIP_FIELDS:
        if key in entry:
            setattr(clip, key, type(getattr(clip, key))(entry[key]))
    clip.end = min(clip.end, clip.info.duration)
    clip.start = max(0.0, min(clip.start, clip.end - 0.1))
