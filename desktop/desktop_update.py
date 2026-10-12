"""Update packages: install a newer Modernform Quote Helper without running an installer.

A package, ``ModernformQuoteHelperUpdate-<version>.zip``, holds only the program's own files:

    update-manifest.json   version, the runtime it needs, build commit, SHA-256 of every file
    update-manifest.sig    optional: Ed25519 signature of update-manifest.json (base64)
    app/  bridge/  desktop/

It never contains the Python runtime or any executable or script type Windows runs by itself
(.exe, .dll, .pyd, .bat, .ps1, ...), so applying it adds nothing new for SmartScreen, AppLocker or
antivirus to evaluate: the same PSF-signed python.exe/pythonw.exe run the new code. The data folder
and config.json are outside the program folder and are never touched.

Applying (``desktop_launcher --apply-update``) checks the package (signature when a key is pinned,
newer version, compatible runtime, every file's SHA-256), extracts it into
``<install>/update-work/incoming`` and checks the extracted files again, stops the server, then
replaces each program folder with two renames. Every rename is written to a journal first; if
anything fails, or the computer stops half-way, the renames are undone (now, or by ``recover`` at
the next start), so the previous version always stays runnable. The replaced version is kept in
``<install>/previous`` for ``--rollback-update``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import desktop_common as common

FORMAT = 1
PRODUCT = "ModernformQuoteHelper"
PARTS = ("app", "bridge", "desktop")
MANIFEST_NAME = "update-manifest.json"
SIGNATURE_NAME = "update-manifest.sig"
PUBLIC_KEY_NAME = "update-signing-key.pub"  # in desktop/: the key update packages must be signed with
RUNTIME_INFO_NAME = "runtime.json"  # in runtime/: written by scripts/build_desktop.py
PACKAGE_PREFIX = "ModernformQuoteHelperUpdate-"
WORK_DIR = "update-work"
PREVIOUS_DIR = "previous"
REQUIRED_FILES = ("app/release.json", "desktop/launcher.pyw")
# The launcher modules the update step runs from a copy of (their originals are being replaced).
LAUNCHER_MODULES = ("desktop_common.py", "desktop_launcher.py", "desktop_update.py")
# File types an update never contains: native code and anything Windows runs as a program or script.
BLOCKED_SUFFIXES = frozenset({
    ".exe", ".dll", ".pyd", ".so", ".sys", ".ocx", ".cpl", ".scr", ".com", ".msi", ".msix", ".msp", ".appx",
    ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".reg",
    ".jar", ".pth",
})
RENAME_RETRY_SECONDS = 15.0  # antivirus and indexers briefly hold files that were just written
LOCK_STALE_SECONDS = 30 * 60
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class UpdateError(common.LauncherError):
    """An update that cannot be applied, explained in plain words."""

    def __init__(self, message: str, *, needs_installer: bool = False) -> None:
        super().__init__(message)
        self.needs_installer = needs_installer


# --------------------------------------------------------------------------- versions


_VERSION = re.compile(r"^(\d+(?:\.\d+){0,3})(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def version_key(version: Any) -> tuple:
    """Order Modernform Quote Helper versions.

    Modernform Quote Helper does not publish pre-releases: a suffix marks a later build of the same version
    (3.1.0-ci.2 and 3.1.0-hotfix.1 are newer than 3.1.0). Numeric suffix parts compare as numbers.
    """
    match = _VERSION.match(version.strip()) if isinstance(version, str) else None
    if not match:
        raise UpdateError(f"'{version}' is not a Modernform Quote Helper version number.")
    numbers = tuple(int(part) for part in match.group(1).split("."))
    numbers += (0,) * (4 - len(numbers))
    suffix: tuple = ()
    if match.group(2):
        suffix = tuple((0, int(part), "") if part.isdigit() else (1, 0, part.lower())
                       for part in re.split(r"[.-]", match.group(2)) if part)
    return numbers, suffix


def _numbers(version: Any) -> tuple[int, ...]:
    found = re.match(r"^(\d+(?:\.\d+)*)", str(version or ""))
    return tuple(int(part) for part in found.group(1).split(".")) if found else ()


# --------------------------------------------------------------------------- runtime


def normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(text: str) -> dict[str, str]:
    """``name==version`` lines of a lock file."""
    packages = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*==\s*([^\s;#]+)", line)
        if match:
            packages[normalize_package(match.group(1))] = match.group(2)
    return packages


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def installed_runtime(install: Path) -> dict[str, Any] | None:
    """The bundled runtime's Python version and packages, or None without a runtime folder."""
    runtime = install / "runtime"
    if not runtime.is_dir():
        return None
    info = _read_json(runtime / RUNTIME_INFO_NAME)
    info = info if isinstance(info, dict) else {}
    python = None
    try:
        if Path(sys.executable).resolve().parent == runtime.resolve():
            python = platform.python_version()
    except OSError:
        pass
    python = python or info.get("python")
    if not python:
        for pth in runtime.glob("python3*._pth"):
            found = re.match(r"^python(\d)(\d+)\._pth$", pth.name)
            if found:
                python = f"{found.group(1)}.{found.group(2)}"
    packages: dict[str, str] = {}
    site_packages = runtime / "Lib" / "site-packages"
    for dist in site_packages.glob("*.dist-info") if site_packages.is_dir() else ():
        name, _, version = dist.name[: -len(".dist-info")].rpartition("-")
        if name and version:
            packages[normalize_package(name)] = version
    if not packages and isinstance(info.get("packages"), dict):
        packages = {normalize_package(str(name)): str(version) for name, version in info["packages"].items()}
    return {"python": python, "packages": packages}


def runtime_problem(requires: dict[str, Any], runtime: dict[str, Any] | None) -> str | None:
    """Why the installed runtime cannot run a package's code, or None when it can."""
    if runtime is None:
        return "needs the bundled runtime, which this installation does not have"
    need = str(requires.get("python") or "")
    if need:
        have, want = _numbers(runtime.get("python")), _numbers(need)
        if not have or have[:2] != want[:2] or have < want:
            return f"needs Python {need} (this installation has {runtime.get('python') or 'an unknown version'})"
    installed = runtime.get("packages") or {}
    missing = [f"{name} {version}" for name, version in sorted((requires.get("packages") or {}).items())
               if installed.get(normalize_package(name)) != str(version)]
    if missing:
        listed = ", ".join(missing[:4]) + (f" and {len(missing) - 4} more" if len(missing) > 4 else "")
        return f"needs runtime packages this installation does not have ({listed})"
    return None


# --------------------------------------------------------------------------- packages


@dataclass(frozen=True)
class Package:
    path: Path
    manifest: dict[str, Any]
    manifest_bytes: bytes
    signature: bytes | None

    @property
    def version(self) -> str:
        return self.manifest["version"]

    @property
    def files(self) -> dict[str, str]:
        return self.manifest["files"]


def check_member_name(name: str) -> None:
    """Refuse paths that leave the program folders and file types an update never contains."""
    path = PurePosixPath(name)
    unsafe = ("\\" in name or ":" in name or name.startswith("/") or not path.parts or ".." in path.parts
              or "." in path.parts or path.parts[0] not in PARTS or len(path.parts) < 2)
    if unsafe:
        raise UpdateError(f"The update package contains a file outside the program folders: {name}")
    if "__pycache__" in path.parts or path.suffix.lower() in BLOCKED_SUFFIXES | {".pyc", ".pyo"}:
        raise UpdateError(f"The update package contains a file type that updates never contain: {name}")


def _validate_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise UpdateError("The update package's manifest is damaged.")
    if manifest.get("product") != PRODUCT:
        raise UpdateError("This file is not a Modernform Quote Helper update package.")
    if not isinstance(manifest.get("format"), int) or manifest["format"] > FORMAT:
        raise UpdateError("This update package was made for a newer Modernform Quote Helper. Install that "
                          "version with its "
                          "ModernformQuoteHelperSetup installer instead.", needs_installer=True)
    version_key(manifest.get("version"))
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise UpdateError("The update package's manifest lists no files.")
    for name, digest in files.items():
        if not isinstance(name, str) or not isinstance(digest, str) or not _SHA256.match(digest):
            raise UpdateError(f"The update package's manifest has a bad entry for {name}.")
        check_member_name(name)
    for name in REQUIRED_FILES:
        if name not in files:
            raise UpdateError(f"The update package is incomplete: {name} is missing.")
    for part in PARTS:
        if not any(name.startswith(part + "/") for name in files):
            raise UpdateError(f"The update package is incomplete: it has no {part} folder.")
    requires = manifest.get("requires")
    if not isinstance(requires, dict) or not isinstance(requires.get("packages", {}), dict):
        raise UpdateError("The update package's manifest does not say which runtime it needs.")
    return manifest


def read_package(path: Path) -> Package:
    """Open a package and check its manifest and file list (not yet the file contents)."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = [info.filename for info in archive.infolist() if not info.is_dir()]
            if MANIFEST_NAME not in names:
                raise UpdateError(f"{Path(path).name} is not a Modernform Quote Helper update package "
                                  f"(it has no {MANIFEST_NAME}).")
            manifest_bytes = archive.read(MANIFEST_NAME)
            signature = archive.read(SIGNATURE_NAME) if SIGNATURE_NAME in names else None
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
        raise UpdateError(f"{Path(path).name} could not be read as an update package ({exc}). If it is still being "
                          "copied or synced, wait for that to finish.") from exc
    try:
        manifest = _validate_manifest(json.loads(manifest_bytes.decode("utf-8")))
    except (UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("The update package's manifest is damaged.") from exc
    if len(set(names)) != len(names):
        raise UpdateError("The update package lists a file twice.")
    expected = set(manifest["files"]) | {MANIFEST_NAME} | ({SIGNATURE_NAME} if signature is not None else set())
    extra, missing = set(names) - expected, expected - set(names)
    if extra or missing:
        problem = (f"contains files its manifest does not list ({sorted(extra)[0]})" if extra
                   else f"is missing {sorted(missing)[0]}")
        raise UpdateError(f"The update package {problem}.")
    return Package(Path(path), manifest, manifest_bytes, signature)


def pinned_keys(install: Path) -> list[bytes]:
    """Ed25519 public keys the installed copy accepts update packages from (desktop/update-signing-key.pub)."""
    path = install / "desktop" / PUBLIC_KEY_NAME
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    keys = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            key = base64.b64decode(line, validate=True)
        except ValueError:
            key = b""
        if len(key) != 32:
            raise UpdateError(f"The update signing key in {path} is damaged. Reinstall Modernform Quote Helper.")
        keys.append(key)
    return keys


def verify_signature(package: Package, keys: list[bytes]) -> bool:
    """True when a pinned key signed the manifest. Without pinned keys, unsigned packages are accepted."""
    if not keys:
        return False
    if package.signature is None:
        raise UpdateError("This update package is not signed, and this Modernform Quote Helper only installs "
                          "signed updates. "
                          "Use the ModernformQuoteHelperUpdate file published on the official release.")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # cryptography is part of the bundled runtime
        raise UpdateError("Modernform Quote Helper cannot check update signatures on this computer. Use the "
                          "installer.") from exc
    try:
        signature = base64.b64decode(package.signature.strip(), validate=True)
    except ValueError as exc:
        raise UpdateError("The update package's signature is damaged.") from exc
    for key in keys:
        try:
            Ed25519PublicKey.from_public_bytes(key).verify(signature, package.manifest_bytes)
            return True
        except (InvalidSignature, ValueError):
            continue
    raise UpdateError("The update package's signature does not match. It was changed after it was published or "
                      "it is not from the publisher of this Modernform Quote Helper, so it was not installed.")


def _hash_stream(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def verify_package_files(package: Package) -> None:
    """Every file in the zip matches its SHA-256 in the manifest, and release.json names the version."""
    try:
        with zipfile.ZipFile(package.path) as archive:
            for name, expected in package.files.items():
                with archive.open(name) as stream:
                    if _hash_stream(stream) != expected:
                        raise UpdateError(f"{name} in the update package does not match its checksum: the package "
                                          "is damaged or was changed, so it was not installed.")
            release = json.loads(archive.read("app/release.json").decode("utf-8"))
    except (OSError, zipfile.BadZipFile, ValueError, RuntimeError) as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(f"The update package is damaged ({exc}).") from exc
    if not isinstance(release, dict) or release.get("version") != package.version:
        raise UpdateError("The update package's release.json does not match its manifest.")


def check_package(package: Package, install: Path, *, reinstall: bool = False, verify_files: bool = True) -> bool:
    """Raise UpdateError unless the package can be applied to ``install``. Returns whether it was signed."""
    installed = common.installed_version(install / "app")
    signed = verify_signature(package, pinned_keys(install))
    new, current = version_key(package.version), version_key(installed)
    if new < current:
        raise UpdateError(f"The update package is version {package.version}, older than the installed {installed}. "
                          "To go back to the version before the last update, use --rollback-update.")
    if new == current and not reinstall:
        raise UpdateError(f"Modernform Quote Helper {installed} is already installed.")
    problem = runtime_problem(package.manifest["requires"], installed_runtime(install))
    if problem:
        raise UpdateError(f"Modernform Quote Helper {package.version} {problem}, so it cannot be installed as an "
                          f"update package. "
                          f"Run ModernformQuoteHelperSetup-{package.version}.exe instead; your data and settings "
                          f"are kept.",
                          needs_installer=True)
    if verify_files:
        verify_package_files(package)
    return signed


def is_installed_layout(install: Path) -> bool:
    return ((install / "app" / "release.json").is_file() and (install / "desktop").is_dir()
            and (install / "runtime").is_dir())


# --------------------------------------------------------------------------- finding packages


def update_folders(config: common.Config) -> list[Path]:
    folders = [config.update_folder] if config.update_folder else []
    folders.append(common.updates_dir())
    unique: list[Path] = []
    for folder in folders:
        if folder not in unique:
            unique.append(folder)
    return unique


_CHECKED: dict[tuple, dict[str, Any]] = {}


def _describe(package: Package, install: Path) -> dict[str, Any]:
    stat = package.path.stat()
    key = (str(package.path), stat.st_size, stat.st_mtime_ns, common.installed_version(install / "app"),
           tuple(pinned_keys(install)))
    if key not in _CHECKED:
        entry = {"version": package.version, "file": str(package.path), "built_at": package.manifest.get("built_at"),
                 "git_commit": package.manifest.get("git_commit"), "signed": package.signature is not None,
                 "can_apply": True, "needs_installer": False, "reason": None}
        try:
            check_package(package, install)
        except UpdateError as exc:
            entry.update(can_apply=False, needs_installer=exc.needs_installer, reason=str(exc))
        if len(_CHECKED) > 32:
            _CHECKED.clear()
        _CHECKED[key] = entry
    return dict(_CHECKED[key])


def find_update(install: Path, config: common.Config) -> dict[str, Any] | None:
    """The newest package newer than the installed version that can be applied; else the newest one found
    with the reason it cannot be (so the person or IT can see why); else None."""
    installed = version_key(common.installed_version(install / "app"))
    candidates: list[Package] = []
    unreadable: dict[str, Any] | None = None
    for folder in update_folders(config):
        try:
            paths = sorted(path for path in folder.glob(f"{PACKAGE_PREFIX}*.zip") if path.is_file())
        except OSError:
            continue
        for path in paths:
            try:
                package = read_package(path)
            except UpdateError as exc:
                unreadable = unreadable or {"version": None, "file": str(path), "can_apply": False,
                                            "needs_installer": exc.needs_installer, "reason": str(exc), "signed": None}
                continue
            if version_key(package.version) > installed:
                candidates.append(package)
    candidates.sort(key=lambda package: version_key(package.version), reverse=True)
    first_problem = None
    for package in candidates:
        entry = _describe(package, install)
        if entry["can_apply"]:
            return entry
        first_problem = first_problem or entry
    return first_problem or unreadable


def read_last_result() -> dict[str, Any] | None:
    result = _read_json(common.update_result_path())
    return result if isinstance(result, dict) else None


def write_result(result: dict[str, Any]) -> None:
    path = common.update_result_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temporary.replace(path)


def previous_version(install: Path) -> str | None:
    release = _read_json(install / PREVIOUS_DIR / "app" / "release.json")
    return str(release["version"]) if isinstance(release, dict) and "version" in release else None


def update_status(install: Path, config: common.Config) -> dict[str, Any]:
    status: dict[str, Any] = {
        "supported": False, "reason": None, "installed_version": None, "previous_version": None,
        "folders": [str(folder) for folder in update_folders(config)], "available": None,
        "last_result": read_last_result(), "applying": False, "signed_updates_required": False,
    }
    if not is_installed_layout(install):
        status["reason"] = "Update packages apply only to the installed Modernform Quote Helper."
        return status
    status.update(supported=True, installed_version=common.installed_version(install / "app"),
                  previous_version=previous_version(install), applying=apply_in_progress(install))
    try:
        status["signed_updates_required"] = bool(pinned_keys(install))
        status["available"] = find_update(install, config)
    except UpdateError as exc:
        status["reason"] = str(exc)
    return status


# --------------------------------------------------------------------------- applying


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _rename(source: Path, destination: Path) -> None:
    """Rename, retrying while antivirus or the indexer briefly holds a file. Never overwrites."""
    deadline = time.monotonic() + RENAME_RETRY_SECONDS
    while True:
        try:
            os.rename(source, destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def _remove_tree(path: Path) -> None:
    def retry(function, name, _error):  # read-only files (e.g. copied from a read-only share)
        os.chmod(name, 0o700)
        function(name)

    if path.is_dir() and not path.is_symlink():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=retry)
        else:  # pragma: no cover - the bundled runtime is 3.13
            shutil.rmtree(path, onerror=retry)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _work(install: Path) -> Path:
    return install / WORK_DIR


def _journal_path(install: Path) -> Path:
    return _work(install) / "journal.json"


def _write_journal(install: Path, journal: dict[str, Any]) -> None:
    path = _journal_path(install)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(journal, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _undo(journal: dict[str, Any]) -> None:
    for source, destination in reversed(journal.get("moves", [])):
        source, destination = Path(source), Path(destination)
        if destination.exists() and not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            _rename(destination, source)


def _finish(install: Path) -> None:
    """Make the replaced folders the new ``previous`` (after a complete swap)."""
    staged, previous = _work(install) / "previous-new", install / PREVIOUS_DIR
    trash = None
    if staged.exists():
        if previous.exists():
            trash = _work(install) / f"trash-{time.time_ns()}"
            _rename(previous, trash)
        _rename(staged, previous)
    _journal_path(install).unlink(missing_ok=True)
    if trash is not None:
        _remove_tree(trash)


def recover(install: Path) -> str | None:
    """Undo (or complete) an update that was interrupted. Returns what was done, or None."""
    journal = _read_json(_journal_path(install))
    if not isinstance(journal, dict):
        return None
    if journal.get("state") == "swapped":
        _finish(install)
        return f"An interrupted update to {journal.get('to_version')} was completed."
    _undo(journal)
    _journal_path(install).unlink(missing_ok=True)
    _remove_tree(_work(install) / "previous-new")
    return f"An interrupted update was undone; Modernform Quote Helper {journal.get('from_version')} is in place."


def swap_in(install: Path, source: Path, *, from_version: str, to_version: str, action: str) -> None:
    """Replace app/, bridge/ and desktop/ with those in ``source``; the replaced ones become previous/.

    A failure part-way undoes the renames already made and raises UpdateError.
    """
    for part in PARTS:
        if not (source / part).is_dir():
            raise UpdateError(f"{source / part} is missing, so nothing was changed.")
    staged = _work(install) / "previous-new"
    _remove_tree(staged)
    staged.mkdir(parents=True)
    journal: dict[str, Any] = {"state": "swapping", "action": action, "from_version": from_version,
                               "to_version": to_version, "started_at": _now(), "moves": []}

    def move(origin: Path, target: Path) -> None:
        journal["moves"].append([str(origin), str(target)])
        _write_journal(install, journal)  # written before the rename, so recover() can always undo it
        _rename(origin, target)

    current_part = PARTS[0]
    try:
        for current_part in PARTS:  # one folder at a time: at most one is ever missing, for one rename
            if (install / current_part).exists():
                move(install / current_part, staged / current_part)
            move(source / current_part, install / current_part)
    except OSError as exc:
        try:
            _undo(journal)
        except OSError as undo_error:
            raise UpdateError(f"Modernform Quote Helper could not replace its {current_part} folder ({exc}) nor "
                              f"put the previous "
                              f"files back ({undo_error}). Open Modernform Quote Helper again to retry, or "
                              f"reinstall it with "
                              "the installer; your data is not affected.") from undo_error
        _journal_path(install).unlink(missing_ok=True)
        _remove_tree(staged)
        raise UpdateError(f"Modernform Quote Helper could not replace its {current_part} folder ({exc}). Nothing "
                          f"was changed: "
                          f"version {from_version} is still installed. Close programs that may use the "
                          f"Modernform Quote Helper "
                          "folder (or restart the computer) and try again.") from exc
    journal["state"] = "swapped"
    _write_journal(install, journal)
    _finish(install)


def extract(package: Package, destination: Path) -> None:
    """Unpack the package's files into ``destination`` and check them again on disk."""
    _remove_tree(destination)
    destination.mkdir(parents=True)
    try:
        with zipfile.ZipFile(package.path) as archive:
            for name in package.files:
                check_member_name(name)
                target = destination.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise UpdateError(f"The update package is damaged ({exc}).") from exc
    verify_tree(destination, package.files)
    release = _read_json(destination / "app" / "release.json")
    if not isinstance(release, dict) or release.get("version") != package.version:
        raise UpdateError("The update package's release.json does not match its manifest.")


def verify_tree(folder: Path, files: dict[str, str]) -> None:
    """``folder`` holds exactly ``files`` with those SHA-256 values (ignoring Python caches)."""
    found = {path.relative_to(folder).as_posix() for path in folder.rglob("*")
             if path.is_file() and "__pycache__" not in path.parts}
    if found != set(files):
        odd = sorted(found ^ set(files))[0]
        raise UpdateError(f"After unpacking, {odd} is not as the update package lists it (an antivirus program may "
                          "have removed or added a file). Nothing was changed.")
    for name, expected in files.items():
        with open(folder.joinpath(*PurePosixPath(name).parts), "rb") as stream:
            if _hash_stream(stream) != expected:
                raise UpdateError(f"After unpacking, {name} does not match its checksum. Nothing was changed.")


class ApplyLock:
    """Only one update or rollback at a time; the launcher waits for it before starting the server."""

    def __init__(self, install: Path) -> None:
        self.path = _work(install) / "apply.lock"

    def __enter__(self) -> ApplyLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                with open(self.path, "x", encoding="utf-8") as handle:
                    handle.write(str(os.getpid()))
                return self
            except FileExistsError:
                if _lock_held(self.path):
                    raise UpdateError("Another Modernform Quote Helper update is being installed right now. "
                                      "Wait for it to finish.") from None
                self.path.unlink(missing_ok=True)
        raise UpdateError("Modernform Quote Helper could not start installing the update. Try again.")

    def __exit__(self, *_exc: object) -> None:
        self.path.unlink(missing_ok=True)


def _lock_held(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8").strip() or 0)
        age = time.time() - path.stat().st_mtime
    except (OSError, ValueError):
        return False
    # The update step itself starts the new version's launcher, which must not wait for its parent.
    return pid not in {os.getpid(), os.getppid()} and age < LOCK_STALE_SECONDS and common.pid_alive(pid)


def apply_in_progress(install: Path) -> bool:
    path = _work(install) / "apply.lock"
    return path.exists() and _lock_held(path)


def wait_for_apply(install: Path, timeout: float = 300.0) -> bool:
    """Wait while an update is being applied. Returns False if it is still going after ``timeout``."""
    deadline = time.monotonic() + timeout
    while apply_in_progress(install):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)
    return True


def clean_work(install: Path, keep: Path | None = None) -> None:
    """Remove leftovers of earlier update runs (never while a journal says a swap is unfinished)."""
    work = _work(install)
    if not work.is_dir() or _journal_path(install).exists():
        return
    for entry in work.iterdir():
        if entry.name == "apply.lock" or (keep is not None and entry.resolve() == keep.resolve()):
            continue
        try:
            _remove_tree(entry)
        except OSError:
            pass


def prepare_launcher_copy(install: Path) -> Path:
    """Copy the launcher modules out of desktop/, which the update replaces, and return the copy's folder."""
    copy = _work(install) / f"launcher-{os.getpid()}-{time.time_ns()}"
    copy.mkdir(parents=True)
    for name in LAUNCHER_MODULES:
        shutil.copy2(install / "desktop" / name, copy / name)
    return copy


def apply_package(install: Path, package_path: Path, *, stop: Callable[[], None], start: Callable[[], bool],
                  reinstall: bool = False, log: Callable[[str], None] = lambda _message: None) -> dict[str, Any]:
    """Apply an update package: check, unpack, stop, swap, start. Returns (and records) the result.

    ``stop`` stops the running server (raising UpdateError if it cannot); ``start`` starts the
    installed version and returns whether it answered. If the new version does not start, the
    previous one is put back and started.
    """
    result: dict[str, Any] = {"action": "update", "ok": False, "package": str(package_path), "started_at": _now()}
    stopped = False
    try:
        with ApplyLock(install):
            note = recover(install)
            if note:
                log(note)
            clean_work(install, keep=Path(__file__).resolve().parent)  # not the launcher copy running this
            installed = common.installed_version(install / "app")
            result["from_version"] = installed
            package = read_package(Path(package_path))
            result["to_version"] = package.version
            result["signed"] = check_package(package, install, reinstall=reinstall)
            incoming = _work(install) / "incoming"
            extract(package, incoming)
            log(f"update: {package.path.name} verified ({len(package.files)} files); stopping Modernform Quote "
                f"Helper {installed}")
            stop()
            stopped = True
            swap_in(install, incoming, from_version=installed, to_version=package.version, action="update")
            remove_tree_quietly(incoming)
            log(f"update: program files replaced, {installed} kept in {PREVIOUS_DIR}\\; starting {package.version}")
            if start():
                result.update(ok=True, message=f"Modernform Quote Helper was updated from {installed} to "
                                               f"{package.version}.")
            else:
                log(f"update: {package.version} did not start; putting {installed} back")
                swap_in(install, install / PREVIOUS_DIR, from_version=package.version, to_version=installed,
                        action="restore")
                started = start()
                result["message"] = (f"Modernform Quote Helper {package.version} did not start, so {installed} "
                                     f"was put back"
                                     + ("" if started else " (it did not start either)")
                                     + f". Details are in {common.log_path()}.")
    except UpdateError as exc:
        result["message"] = str(exc)
        result["needs_installer"] = exc.needs_installer
        if stopped:
            start()  # the previous version is still (or again) in place
    except common.LauncherError as exc:
        result["message"] = str(exc)
    except Exception as exc:  # never leave Modernform Quote Helper stopped without saying why
        result["message"] = f"The update could not be installed: {exc}"
        if stopped:
            start()
    finally:
        if not _journal_path(install).exists():  # an unfinished swap may still need the new files
            remove_tree_quietly(_work(install) / "incoming")
    result["finished_at"] = _now()
    log(f"update: {'done' if result['ok'] else 'not installed'}: {result.get('message')}")
    write_result(result)
    return result


def rollback(install: Path, *, stop: Callable[[], None], start: Callable[[], bool],
             log: Callable[[str], None] = lambda _message: None) -> dict[str, Any]:
    """Swap the current program files with those in previous/ (running it again rolls forward)."""
    result: dict[str, Any] = {"action": "rollback", "ok": False, "started_at": _now()}
    stopped = False
    try:
        with ApplyLock(install):
            note = recover(install)
            if note:
                log(note)
            current = common.installed_version(install / "app")
            target = previous_version(install)
            result.update(from_version=current, to_version=target)
            previous = install / PREVIOUS_DIR
            if not target or not all((previous / part).is_dir() for part in PARTS) \
                    or not (previous / "desktop" / "launcher.pyw").is_file():
                raise UpdateError("There is no previous version to go back to (it is kept only after an update "
                                  "package was installed). Use the older version's installer instead.")
            stop()
            stopped = True
            swap_in(install, previous, from_version=current, to_version=target, action="rollback")
            started = start()
            result.update(ok=started, message=f"Modernform Quote Helper {target} is back in use ({current} is kept in "
                          f"{PREVIOUS_DIR}\\)." + ("" if started else f" It did not start: see {common.log_path()}."))
    except UpdateError as exc:
        result["message"] = str(exc)
        if stopped:
            start()
    except common.LauncherError as exc:
        result["message"] = str(exc)
    except Exception as exc:  # never leave Modernform Quote Helper stopped without saying why
        result["message"] = f"Modernform Quote Helper could not go back to the previous version: {exc}"
        if stopped:
            start()
    result["finished_at"] = _now()
    log(f"rollback:{'done' if result['ok'] else 'not done'}: {result.get('message')}")
    write_result(result)
    return result


def remove_tree_quietly(path: Path) -> None:
    try:
        _remove_tree(path)
    except OSError:
        pass
