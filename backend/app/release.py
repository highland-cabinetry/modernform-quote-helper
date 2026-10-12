import json
from pathlib import Path

RELEASE = json.loads((Path(__file__).resolve().parents[2] / "release.json").read_text(encoding="utf-8"))
