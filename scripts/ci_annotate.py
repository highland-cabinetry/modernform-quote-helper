"""Print pytest JUnit failures as GitHub Actions error annotations (used by CI only)."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main(path: str) -> int:
    report = Path(path)
    if not report.exists():
        print(f"No test report at {report}.")
        return 0
    count = 0
    for case in ET.parse(report).getroot().iter("testcase"):
        for problem in [*case.findall("failure"), *case.findall("error")]:
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            detail = (problem.text or problem.get("message") or "")[-6000:]
            print(f"::error title={escape(name)}::{escape(detail)}")
            count += 1
    print(f"{count} failing test(s) reported.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "pytest.xml"))
