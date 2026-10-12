"""Select which releases\\<version> folder Quote Helper V2 runs (deployment.json), or show the selection.

Usage (from the project folder):

    .venv\\Scripts\\python.exe scripts\\switch_release.py --status
    .venv\\Scripts\\python.exe scripts\\switch_release.py --to 2.11.0
    .venv\\Scripts\\python.exe scripts\\switch_release.py --rollback      # back to deployment.previous.json

Steps for a switch:
  1. Stop Quote Helper ('Stop Quote Helper V2.bat' or Ctrl+C in its Run window).
  2. Run --to <version>. It checks that releases\\<version> exists and still matches its
     build-manifest.json. It stops with exit code 2 if a Quote Helper server still answers on
     127.0.0.1:8000-8009 (use --force only if that answer is not Quote Helper).
  3. Start 'Run Quote Helper V2.bat'.

deployment.json is replaced atomically. The selection it had before is saved in
deployment.previous.json, which --rollback (or --to <that version>) uses.
The live database in data\\ is not touched. The backend migrates the database additively on start and
makes a verified backup first. Older releases continue to run against the migrated database.
Restoring older database bytes is a separate recovery decision.

--root points at another project folder (used by the tests).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cut_release import (  # noqa: E402
    MANIFEST_NAME,
    ReleaseError,
    releases_dir,
    validate_version,
    verify_folder,
)

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = "deployment.json"
PREVIOUS = "deployment.previous.json"
PRODUCTION_PORTS = range(8000, 8010)
EXIT_SERVER_RUNNING = 2


def running_servers(ports=PRODUCTION_PORTS, timeout: float = 1.0) -> list[int]:
    """Ports on 127.0.0.1 where GET /api/health answers successfully."""

    answering = []
    for port in ports:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as response:
                if 200 <= response.status < 300:
                    answering.append(port)
        except (OSError, urllib.error.URLError, ValueError):
            continue
    return answering


def read_selection(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        raise ReleaseError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"{path.name} must contain a JSON object.")
    return value


def version_of(selection: dict | None) -> str | None:
    if not selection or not selection.get("release_path"):
        return None
    parts = str(selection["release_path"]).replace("\\", "/").strip("/").split("/")
    return parts[1] if len(parts) == 2 and parts[0] == "releases" else None


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in range(10):  # Windows: a reader (or OneDrive) may hold the file for a moment
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 9:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.2)


def check_release(root: Path, version: str) -> Path:
    release = releases_dir(root) / validate_version(version)
    if not release.is_dir():
        available = ", ".join(list_releases(root)) or "none"
        raise ReleaseError(f"releases/{version} does not exist. Available releases: {available}.")
    for required in ("backend/app/main.py", "frontend/dist/index.html", "release.json", MANIFEST_NAME):
        if not (release / required).is_file():
            raise ReleaseError(f"releases/{version} is incomplete: {required} is missing.")
    problems = verify_folder(release)
    if problems:
        shown = "\n  - ".join(problems[:20]) + ("\n  - ..." if len(problems) > 20 else "")
        raise ReleaseError(f"releases/{version} does not match its {MANIFEST_NAME}:\n  - {shown}")
    return release


def list_releases(root: Path) -> list[str]:
    folder = releases_dir(root)
    if not folder.is_dir():
        return []
    return sorted(path.name for path in folder.iterdir() if path.is_dir() and not path.name.startswith("."))


def switch(root: Path, version: str, *, force: bool = False, server_check=None) -> tuple[str | None, bool]:
    """Select releases/<version>. Returns (previous version, changed)."""

    root = Path(root).resolve()
    check_release(root, version)
    deployment_path = root / DEPLOYMENT
    current = read_selection(deployment_path)
    if version_of(current) == version:
        return version, False
    if not force:
        ports = (server_check or running_servers)()
        if ports:
            raise ServerRunning(ports)
    if current is not None:
        write_json_atomic(root / PREVIOUS, current)
    updated = dict(current or {})
    updated["release_path"] = f"releases/{version}"
    write_json_atomic(deployment_path, updated)
    return version_of(current), True


class ServerRunning(RuntimeError):
    def __init__(self, ports: list[int]):
        super().__init__(ports)
        self.ports = ports


def print_status(root: Path) -> None:
    current = read_selection(root / DEPLOYMENT)
    previous = read_selection(root / PREVIOUS)
    print(f"Current:  {current['release_path'] if current else '(no deployment.json: run.ps1 uses the workspace)'}")
    print(f"Previous: {previous['release_path'] if previous else '(none recorded)'}")
    names = list_releases(root)
    print("Releases:" if names else "Releases: none")
    for name in names:
        marker = "*" if name == version_of(current) else " "
        manifest_path = releases_dir(root) / name / MANIFEST_NAME
        detail = "no build-manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                commit = (manifest.get("git_commit") or "unknown")[:12]
                detail = f"created {manifest.get('created_at', '?')}, commit {commit}"
            except ValueError:
                detail = "unreadable build-manifest.json"
        print(f" {marker} {name:<28} {detail}")


def _console_safe() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _console_safe()
    parser = argparse.ArgumentParser(description="Select the release Quote Helper V2 runs.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Project folder (default: this checkout).")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--to", metavar="VERSION", help="Select releases/VERSION.")
    action.add_argument("--rollback", action="store_true", help="Select the release in deployment.previous.json.")
    action.add_argument("--status", action="store_true", help="Show the current and previous selection.")
    parser.add_argument("--force", action="store_true", help="Skip the running-server check.")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.status:
            print_status(root)
            return 0
        version = args.to
        if args.rollback:
            version = version_of(read_selection(root / PREVIOUS))
            if not version:
                raise ReleaseError("deployment.previous.json does not name a release to roll back to.")
        previous, changed = switch(root, version, force=args.force)
    except ServerRunning as exc:
        ports = ", ".join(str(port) for port in exc.ports)
        print(f"Quote Helper is running (answered on port {ports}): stop Quote Helper first "
              "('Stop Quote Helper V2.bat' or Ctrl+C in its Run window), then run this again.", file=sys.stderr)
        return EXIT_SERVER_RUNNING
    except (ReleaseError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not changed:
        print(f"releases/{version} is already selected and verified; deployment.json was not changed.")
        return 0
    print(f"deployment.json now selects releases/{version} (manifest verified).")
    print("Start 'Run Quote Helper V2.bat' to use it.")
    if previous:
        print(f"Previous selection releases/{previous} is saved in {PREVIOUS}. To roll back:")
        print("  1. Stop Quote Helper ('Stop Quote Helper V2.bat').")
        print(f"  2. .venv\\Scripts\\python.exe scripts\\switch_release.py --rollback   (or --to {previous})")
        print("  3. Start 'Run Quote Helper V2.bat'.")
    else:
        print("There was no previous deployment.json; rolling back means choosing another release with --to.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
