"""One-click export presets for common destinations.

Each preset is a partial export-panel state; anything it doesn't mention is left as it is,
except the keys in RESET, which go back to neutral so an earlier preset doesn't leak through.
"""
from __future__ import annotations

RESET = {
    "aspect": None, "fit": "pad", "resolution": None, "fps": "Same as source",
    "mode": "preset", "preset": "medium", "auto_res": True,
}

PLATFORM_PRESETS: dict[str, dict] = {
    "Discord (10 MB)": {
        "format": ".mp4", "codec": "h264", "mode": "size", "size_mb": 10.0,
    },
    "Discord Nitro (500 MB)": {
        "format": ".mp4", "codec": "h264", "mode": "size", "size_mb": 500.0,
    },
    "WhatsApp (16 MB)": {
        "format": ".mp4", "codec": "h264", "mode": "size", "size_mb": 16.0, "fps": "30",
    },
    "Email attachment (20 MB)": {
        "format": ".mp4", "codec": "h264", "mode": "size", "size_mb": 20.0,
    },
    "YouTube (high quality)": {
        "format": ".mp4", "codec": "h264", "preset": "high", "audio_bitrate": 192,
    },
    "Shorts / Reels / TikTok (9:16)": {
        "format": ".mp4", "codec": "h264", "aspect": "9:16", "fit": "blur", "resolution": 1080,
        "fps": "30", "preset": "high",
    },
    "Instagram post (4:5)": {
        "format": ".mp4", "codec": "h264", "aspect": "4:5", "fit": "blur", "resolution": 1080,
        "fps": "30", "preset": "high",
    },
    "X / Twitter": {
        "format": ".mp4", "codec": "h264", "resolution": 1080, "preset": "medium",
    },
    "GIF (small, 480p)": {
        "format": ".gif", "resolution": 480, "fps": "15", "preset": "medium",
    },
    "Audio only (MP3)": {
        "format": ".mp3", "audio_bitrate": 192,
    },
}


def preset_state(name: str) -> dict:
    return {**RESET, **PLATFORM_PRESETS[name]}
