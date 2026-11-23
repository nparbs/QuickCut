"""Saving and opening QuickCut projects (.qcut files: JSON with the clips and export settings)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from pathlib import Path

from . import __version__
from .models import Clip, Layer

PROJECT_EXT = ".qcut"
FORMAT_VERSION = 1
CLIP_FIELDS = ("start", "end", "speed", "rotate", "hflip", "vflip", "reverse", "volume", "muted",
               "pan_x", "pan_y")
LAYER_FIELDS = {f.name: f for f in fields(Layer)}


def _relative(path: str, project_dir: Path) -> str | None:
    try:
        return os.path.relpath(path, project_dir)
    except ValueError:  # different drive on Windows
        return None


def clip_to_dict(clip: Clip, project_dir: Path) -> dict:
    data = {"path": clip.info.path}
    rel = _relative(clip.info.path, project_dir)
    if rel:
        data["relative"] = rel
    data.update({k: getattr(clip, k) for k in CLIP_FIELDS})
    data["zoom"] = clip.zoom
    return data


def layer_to_dict(layer: Layer, project_dir: Path) -> dict:
    data = asdict(layer)
    if layer.image:
        rel = _relative(layer.image, project_dir)
        if rel:
            data["image_relative"] = rel
    return data


def layer_from_dict(entry: dict, project_dir: Path) -> Layer:
    layer = Layer()
    for key, value in entry.items():
        if key in LAYER_FIELDS and value is not None:
            try:
                setattr(layer, key, type(getattr(layer, key))(value))
            except (TypeError, ValueError):
                pass
    if layer.image and not os.path.isfile(layer.image) and entry.get("image_relative"):
        moved = project_dir / entry["image_relative"]
        if moved.is_file():
            layer.image = str(moved)
    if layer.kind not in ("text", "image"):
        layer.kind = "text"
    return layer


def save_project(path: str, clips: list[Clip], export_state: dict, autosave_of: str | None = None,
                 layers: list[Layer] | None = None):
    project_dir = Path(path).resolve().parent
    data = {
        "app": "QuickCut",
        "app_version": __version__,
        "format": FORMAT_VERSION,
        "clips": [clip_to_dict(c, project_dir) for c in clips],
        "export": export_state,
        "layers": [layer_to_dict(layer, project_dir) for layer in layers or []],
    }
    if autosave_of:
        data["autosave_of"] = autosave_of
    os.makedirs(project_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load_project(path: str) -> tuple[list[dict], dict]:
    """(clip entries with a resolved 'path' or 'missing': True, export state). Use load_layers for layers."""
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


def load_layers(path: str) -> list[Layer] | None:
    """The project's layers, or None for a project saved before layers existed."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "layers" not in data:
        return None
    project_dir = Path(path).resolve().parent
    return [layer_from_dict(e, project_dir) for e in data["layers"] if isinstance(e, dict)]


def autosave_origin(path: str) -> str | None:
    """The project an autosave file belongs to, if it had one."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("autosave_of")
    except (OSError, ValueError):
        return None


def apply_clip_fields(clip: Clip, entry: dict):
    for key in CLIP_FIELDS:
        if key in entry:
            setattr(clip, key, type(getattr(clip, key))(entry[key]))
    zoom = entry.get("zoom")
    clip.zoom = float(zoom) if isinstance(zoom, (int, float)) else None
    clip.end = min(clip.end, clip.info.duration)
    clip.start = max(0.0, min(clip.start, clip.end - 0.1))
