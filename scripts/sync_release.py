"""Generate package/manifest version fields from release.json; --check detects drift."""

import argparse
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
version = json.loads((root / "release.json").read_text())["version"]
for name in (
    "frontend/package.json",
    "build2quote_bridge/package.json",
    "build2quote_bridge/manifest.json",
    "pyproject.toml",
):
    path = root / name
    text = path.read_text(encoding="utf-8")
    pattern = r'("version"\s*:\s*")[^"]+' if name.endswith("json") else r'(version = ")[^"]+'
    updated = re.sub(pattern, lambda m: m[1] + version, text, count=1)
    if args.check:
        if updated != text:
            raise SystemExit(f"Release version drift: {name}")
    else:
        path.write_text(updated, encoding="utf-8")
