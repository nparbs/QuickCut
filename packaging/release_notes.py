"""Print the CHANGELOG.md section for one version (used for GitHub release notes)."""
import re
import sys
from pathlib import Path

version = sys.argv[1]
text = Path(__file__).resolve().parent.parent.joinpath("CHANGELOG.md").read_text(encoding="utf-8")
match = re.search(rf"^## \[?{re.escape(version)}\]?.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
body = match.group(1).strip() if match else f"QuickCut {version}"
print(body)
print("\n---\nDownload the zip below, extract it anywhere and run **QuickCut.exe**. FFmpeg is included.")
