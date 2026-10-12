"""Cut a runnable release folder from the workspace, or verify one against its manifest.

Usage (from the project folder, after Setup has rebuilt frontend\\dist and the Bridge dist):

    .venv\\Scripts\\python.exe scripts\\cut_release.py                   # creates releases\\<release.json version>
    .venv\\Scripts\\python.exe scripts\\cut_release.py --verify 2.10.0    # re-hashes releases\\2.10.0

A cut copies these workspace files into releases\\<version>:

    backend\\app                   (without __pycache__ / *.pyc)
    frontend\\dist
    build2quote_bridge\\dist
    release.json

It then writes build-manifest.json: {version, created_at, git_commit, git_dirty, files: {path: sha256}}.
The version is always the "version" in release.json. Change it there and run
scripts\\sync_release.py before a cut.

The cut stops without writing anything when:
  * releases\\<version> already exists (a release is never overwritten; bump the version),
  * a dist is missing (run Setup Quote Helper V2.bat first),
  * frontend\\dist\\index.html names an asset that is not in the dist,
  * the package or manifest versions differ from release.json (run scripts\\sync_release.py),
  * the built Bridge manifest version differs from release.json (rebuild the Bridge).
It warns, but continues, when a dist is older than its source files.

The live database (data\\) is never part of a release. A cut does NOT change deployment.json;
use scripts\\switch_release.py --to <version> for that.

--verify exits 0 when every file in the manifest matches and no unlisted files exist, else 1.
--root points at another project folder (used by the tests).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "build-manifest.json"
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
IGNORED_DIRS = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}

# (workspace source, path inside the release)
RELEASE_TREES = (
    ("backend/app", "backend/app"),
    ("frontend/dist", "frontend/dist"),
    ("build2quote_bridge/dist", "build2quote_bridge/dist"),
)
RELEASE_FILES = (("release.json", "release.json"),)

# dist -> the source files whose changes require a rebuild
STALENESS_INPUTS = {
    "frontend/dist": ("frontend/src", "frontend/index.html", "frontend/vite.config.js", "frontend/package.json",
                      "frontend/pnpm-lock.yaml"),
    "build2quote_bridge/dist": ("build2quote_bridge/src", "build2quote_bridge/manifest.json",
                                "build2quote_bridge/build.mjs", "build2quote_bridge/package.json",
                                "build2quote_bridge/pnpm-lock.yaml"),
}
VERSIONED_FILES = ("frontend/package.json", "build2quote_bridge/package.json", "build2quote_bridge/manifest.json",
                   "pyproject.toml")


class ReleaseError(RuntimeError):
    pass


# --------------------------------------------------------------------------- helpers


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _included(path: Path) -> bool:
    return not (set(path.parts) & IGNORED_DIRS) and path.suffix.lower() not in IGNORED_SUFFIXES


def _files_under(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*") if path.is_file() and _included(path.relative_to(folder)))


def releases_dir(root: Path) -> Path:
    return Path(root) / "releases"


def validate_version(version: str) -> str:
    if not VERSION_PATTERN.match(version or "") or ".." in version:
        raise ReleaseError(f"Not a valid release name: {version!r}")
    return version


def release_version(root: Path) -> str:
    try:
        version = json.loads((Path(root) / "release.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError) as exc:
        raise ReleaseError(f"Cannot read the version from release.json: {exc}") from exc
    return validate_version(str(version))


def git_state(root: Path) -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                                timeout=20, check=True).stdout.strip()
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True, timeout=60, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    return (commit or None), bool(status.strip())


def _newest_mtime(root: Path, inputs: tuple[str, ...]) -> tuple[float, Path | None]:
    newest, newest_path = 0.0, None
    for name in inputs:
        path = root / name
        candidates = _files_under(path) if path.is_dir() else [path] if path.is_file() else []
        for candidate in candidates:
            mtime = candidate.stat().st_mtime
            if mtime > newest:
                newest, newest_path = mtime, candidate
    return newest, newest_path


def check_workspace(root: Path, version: str) -> list[str]:
    """Raise ReleaseError for anything that blocks a cut; return non-blocking warnings."""

    problems, warnings = [], []
    if not (root / "backend/app/main.py").is_file():
        problems.append("backend/app/main.py is missing.")
    index = root / "frontend/dist/index.html"
    if not index.is_file():
        problems.append("frontend/dist/index.html is missing. Run 'Setup Quote Helper V2.bat' to build the interface.")
    else:
        html = index.read_text(encoding="utf-8", errors="replace")
        for reference in re.findall(r"""(?:src|href)=["']/?(assets/[^"'?#]+)""", html):
            if not (root / "frontend/dist" / reference).is_file():
                problems.append(f"frontend/dist/index.html refers to {reference}, which is not in the dist.")
    bridge_manifest = root / "build2quote_bridge/dist/manifest.json"
    if not bridge_manifest.is_file():
        problems.append("build2quote_bridge/dist/manifest.json is missing. Run 'Setup Quote Helper V2.bat' to build "
                        "the Bridge.")
    else:
        try:
            built = json.loads(bridge_manifest.read_text(encoding="utf-8")).get("version")
        except ValueError:
            built = None
        if built != version:
            problems.append(f"The built Bridge is version {built}, not {version}. Rebuild it (Setup, or "
                            "'pnpm build' in build2quote_bridge).")
    for name in VERSIONED_FILES:
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        pattern = r'"version"\s*:\s*"([^"]+)' if name.endswith(".json") else r'version = "([^"]+)'
        match = re.search(pattern, text)
        if match and match[1] != version:
            problems.append(f"{name} says version {match[1]} but release.json says {version}. "
                            "Run scripts/sync_release.py.")
    if problems:
        raise ReleaseError("Cannot cut the release:\n  - " + "\n  - ".join(problems))

    for dist, inputs in STALENESS_INPUTS.items():
        built_at, _ = _newest_mtime(root, (dist,))
        changed_at, changed = _newest_mtime(root, inputs)
        if changed is not None and changed_at > built_at + 1:
            warnings.append(f"{dist} is older than {changed.relative_to(root).as_posix()}; it may need a rebuild.")
    return warnings


# --------------------------------------------------------------------------- cut and verify


def build_manifest(release: Path, version: str, root: Path) -> dict:
    commit, dirty = git_state(root)
    files = {path.relative_to(release).as_posix(): sha256_file(path) for path in _files_under(release)
             if path.relative_to(release).as_posix() != MANIFEST_NAME}
    return {
        "version": version,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "git_commit": commit,
        "git_dirty": dirty,
        "source_root": str(root),
        "files": files,
    }


def cut_release(root: Path) -> tuple[Path, list[str]]:
    root = Path(root).resolve()
    version = release_version(root)
    target = releases_dir(root) / version
    if target.exists():
        raise ReleaseError(f"releases/{version} already exists. Releases are never overwritten; bump the version "
                           "in release.json (then run scripts/sync_release.py and rebuild) or remove the folder "
                           "deliberately.")
    warnings = check_workspace(root, version)
    staging = releases_dir(root) / f".{version}.partial"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        for source, destination in RELEASE_TREES:
            for path in _files_under(root / source):
                copy_to = staging / destination / path.relative_to(root / source)
                copy_to.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, copy_to)
        for source, destination in RELEASE_FILES:
            shutil.copy2(root / source, staging / destination)
        manifest = build_manifest(staging, version, root)
        (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        problems = verify_folder(staging)
        if problems:
            raise ReleaseError("The copied release failed verification:\n  - " + "\n  - ".join(problems))
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target, warnings


def _manifest_files(manifest: dict) -> dict[str, str]:
    files = manifest.get("files")
    if isinstance(files, dict):
        entries = files.items()
    elif isinstance(files, list):  # tolerate [{"path": ..., "sha256": ...}] manifests
        entries = ((entry.get("path"), entry.get("sha256") or entry.get("hash")) for entry in files)
    else:
        raise ReleaseError("build-manifest.json has no file list.")
    result = {}
    for path, digest in entries:
        if not path or not digest:
            raise ReleaseError("build-manifest.json has an incomplete file entry.")
        result[str(path).replace("\\", "/")] = str(digest).lower()
    return result


def verify_folder(release: Path) -> list[str]:
    """Return a list of problems (empty when the folder matches its manifest)."""

    release = Path(release)
    manifest_path = release / MANIFEST_NAME
    if not release.is_dir():
        return [f"{release} does not exist."]
    if not manifest_path.is_file():
        return [f"{manifest_path} is missing."]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = _manifest_files(manifest)
    except (ValueError, ReleaseError) as exc:
        return [f"{MANIFEST_NAME} cannot be read: {exc}"]
    problems = []
    for relative, digest in sorted(expected.items()):
        path = release / relative
        if not path.is_file():
            problems.append(f"missing: {relative}")
        elif sha256_file(path) != digest:
            problems.append(f"changed: {relative}")
    for path in _files_under(release):
        relative = path.relative_to(release).as_posix()
        if relative != MANIFEST_NAME and relative not in expected:
            problems.append(f"not in manifest: {relative}")
    if not expected:
        problems.append("the manifest lists no files.")
    return problems


def verify_release(root: Path, version: str) -> list[str]:
    return verify_folder(releases_dir(root) / validate_version(version))


# --------------------------------------------------------------------------- command line


def _console_safe() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _console_safe()
    parser = argparse.ArgumentParser(description="Cut releases/<version> from the workspace, or verify a release.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Project folder (default: this checkout).")
    parser.add_argument("--verify", metavar="VERSION", help="Re-hash releases/VERSION against its build-manifest.json.")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.verify:
            problems = verify_release(root, args.verify)
            if problems:
                print(f"releases/{args.verify} does NOT match its manifest:", file=sys.stderr)
                for problem in problems:
                    print(f"  - {problem}", file=sys.stderr)
                return 1
            count = len(_manifest_files(json.loads((releases_dir(root) / args.verify / MANIFEST_NAME)
                                                   .read_text(encoding="utf-8"))))
            print(f"releases/{args.verify} verified: {count} files match build-manifest.json.")
            return 0
        target, warnings = cut_release(root)
    except (ReleaseError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    manifest = json.loads((target / MANIFEST_NAME).read_text(encoding="utf-8"))
    commit = manifest.get("git_commit") or "unknown"
    dirty = " (uncommitted changes present)" if manifest.get("git_dirty") else ""
    print(f"Created {target} with {len(manifest['files'])} files; source commit {commit[:12]}{dirty}.")
    print(f"deployment.json was not changed. To deploy: stop Quote Helper, then run\n"
          f"  .venv\\Scripts\\python.exe scripts\\switch_release.py --to {manifest['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
