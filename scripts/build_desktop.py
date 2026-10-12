"""Assemble the desktop app folder that the Windows installer packages, and the update package.

Run after the frontend and Bridge are built (pnpm build in frontend/ and build2quote_bridge/):

    python scripts/build_desktop.py --out build/desktop --python-embed python-3.13.16-embed-amd64.zip

Produces:

    build/desktop/app/backend/app        backend code (no caches)
    build/desktop/app/frontend/dist      built interface
    build/desktop/app/release.json
    build/desktop/bridge                 Build2Quote Bridge, loaded unpacked in Chrome
    build/desktop/desktop                launcher, server, updater and icon
                                         (+ update-signing-key.pub when a signing key is given)
    build/desktop/runtime                Windows embeddable Python + pinned packages, runtime.json
    build/desktop/build-manifest.json    version, commit and SHA-256 of every app file

Update package (installs later versions without running an installer; see desktop/desktop_update.py):

    python scripts/build_desktop.py --from-folder build/desktop --update-package ModernformQuoteHelperUpdate-3.1.0.zip

It holds app/, bridge/ and desktop/ only (never runtime/ or an executable) and update-manifest.json
with the SHA-256 of every file. When the environment variable named by --signing-key-env
(default UPDATE_SIGNING_KEY) holds an Ed25519 private key, the folder pins its public key and the
manifest is signed (update-manifest.sig); installed copies with a pinned key accept only packages
signed with it. Create a key pair with --generate-signing-key.

--skip-runtime leaves out the Python runtime (for checks on any OS). Installing packages needs
network access and targets Windows x64, CPython of the embeddable package's version.
The data folder is never part of the build.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

import desktop_update as updates  # noqa: E402

IGNORED_DIRS = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
TREES = (
    ("backend/app", "app/backend/app"),
    ("frontend/dist", "app/frontend/dist"),
    ("build2quote_bridge/dist", "bridge"),
)
FILES = (
    ("release.json", "app/release.json"),
    ("desktop/desktop_common.py", "desktop/desktop_common.py"),
    ("desktop/desktop_launcher.py", "desktop/desktop_launcher.py"),
    ("desktop/desktop_server.py", "desktop/desktop_server.py"),
    ("desktop/desktop_update.py", "desktop/desktop_update.py"),
    ("desktop/launcher.pyw", "desktop/launcher.pyw"),
    ("desktop/quote-helper.ico", "desktop/quote-helper.ico"),
)
EMBED_NAME = re.compile(r"^python(\d)(\d+)\._pth$")


class BuildError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_tree(source: Path, destination: Path) -> None:
    def ignore(folder: str, names: list[str]) -> set[str]:
        return {name for name in names if name in IGNORED_DIRS or Path(name).suffix in IGNORED_SUFFIXES}

    shutil.copytree(source, destination, ignore=ignore)


def check_inputs(root: Path) -> str:
    release = json.loads((root / "release.json").read_text(encoding="utf-8"))
    version = str(release["version"])
    index = root / "frontend/dist/index.html"
    if not index.exists():
        raise BuildError("frontend/dist is missing: run 'pnpm build' in frontend/ first.")
    for asset in re.findall(r'(?:src|href)="/(assets/[^"]+)"', index.read_text(encoding="utf-8")):
        if not (root / "frontend/dist" / asset).exists():
            raise BuildError(f"frontend/dist/index.html names {asset}, which is not in the dist. Rebuild it.")
    bridge = root / "build2quote_bridge/dist/manifest.json"
    if not bridge.exists():
        raise BuildError("build2quote_bridge/dist is missing: run 'pnpm build' in build2quote_bridge/ first.")
    bridge_version = json.loads(bridge.read_text(encoding="utf-8")).get("version")
    if bridge_version != version:
        raise BuildError(f"The built Bridge is version {bridge_version}, release.json says {version}. Rebuild it.")
    return version


def install_runtime(out: Path, embed_zip: Path, requirements: Path, python_version: str | None = None) -> str:
    runtime = out / "runtime"
    with zipfile.ZipFile(embed_zip) as archive:
        archive.extractall(runtime)
    pth = [path for path in runtime.glob("python*._pth") if EMBED_NAME.match(path.name)]
    if len(pth) != 1:
        raise BuildError(f"{embed_zip} is not a Windows embeddable Python package (no python3XX._pth).")
    major, minor = EMBED_NAME.match(pth[0].name).groups()
    if python_version and not python_version.startswith(f"{major}.{minor}."):
        raise BuildError(f"--python-version {python_version} does not match the embeddable package ({major}.{minor}).")
    # The embeddable Python ignores PYTHONPATH; this file is its whole import path.
    pth[0].write_text("\n".join([
        f"python{major}{minor}.zip", ".", "Lib\\site-packages", "..\\app\\backend", "..\\desktop", "import site", "",
    ]), encoding="utf-8")
    site_packages = runtime / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", "--only-binary=:all:",
        "--platform", "win_amd64", "--implementation", "cp", "--python-version", f"{major}.{minor}",
        "--no-cache-dir", "--target", str(site_packages), "-r", str(requirements),
    ], check=True)
    # What update packages are checked against. No dates: the runtime stays byte-identical between
    # releases while the pinned Python and requirements.lock do not change.
    info = {"python": python_version or f"{major}.{minor}",
            "packages": updates.parse_requirements(requirements.read_text(encoding="utf-8"))}
    (runtime / updates.RUNTIME_INFO_NAME).write_text(json.dumps(info, indent=2, sort_keys=True) + "\n",
                                                     encoding="utf-8")
    return f"{major}.{minor}"


# --------------------------------------------------------------------------- signing keys


def load_signing_key(text: str):
    """An Ed25519 private key from base64 (32 raw bytes, as --generate-signing-key prints) or PEM."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    text = text.strip()
    try:
        if "BEGIN" in text:
            key = serialization.load_pem_private_key(text.encode("ascii"), password=None)
        else:
            key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(text, validate=True))
    except ValueError as exc:
        raise BuildError(f"The update signing key could not be read: {exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise BuildError("The update signing key must be an Ed25519 key.")
    return key


def public_key_text(private_key) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def signing_key_from_env(name: str | None):
    value = os.environ.get(name or "", "").strip()
    return load_signing_key(value) if value else None


def generate_signing_key() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
    return base64.b64encode(raw).decode("ascii"), public_key_text(key)


# --------------------------------------------------------------------------- folder


def write_manifest(out: Path, version: str, python: str | None) -> dict:
    files = {}
    for folder in ("app", "bridge", "desktop"):
        for path in sorted((out / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files[path.relative_to(out).as_posix()] = sha256_file(path)
    manifest = {"version": version, "built_at": datetime.now(UTC).isoformat(), "git_commit": git_commit(),
                "python": python, "files": files}
    (out / "build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def build(out: Path, *, root: Path = ROOT, embed_zip: Path | None = None, requirements: Path | None = None,
          version_override: str | None = None, python_version: str | None = None, signing_key=None) -> dict:
    version = check_inputs(root)
    if out.exists():
        if not (out / "build-manifest.json").exists() and any(out.iterdir()):
            raise BuildError(f"{out} exists and is not a previous desktop build; choose an empty folder.")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for source, target in TREES:
        copy_tree(root / source, out / target)
    for source, target in FILES:
        (out / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / source, out / target)
    if version_override:
        release_path = out / "app/release.json"
        release = json.loads(release_path.read_text(encoding="utf-8"))
        release["version"] = version = version_override
        release_path.write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    if signing_key is not None:
        (out / "desktop" / updates.PUBLIC_KEY_NAME).write_text(
            "# Quote Helper installs only update packages signed with this Ed25519 key.\n"
            f"{public_key_text(signing_key)}\n", encoding="utf-8")
    python = None
    if embed_zip:
        python = install_runtime(out, embed_zip, requirements or root / "desktop/requirements.lock", python_version)
    return write_manifest(out, version, python)


# --------------------------------------------------------------------------- update package


def runtime_requirements(folder: Path, requirements: Path, minimum_python: str | None) -> dict:
    """The runtime the package's code needs: the folder's runtime (or the lock file without one)."""
    info = json.loads((folder / "runtime" / updates.RUNTIME_INFO_NAME).read_text(encoding="utf-8")) \
        if (folder / "runtime" / updates.RUNTIME_INFO_NAME).exists() else None
    if info:
        packages = {updates.normalize_package(name): str(value) for name, value in info.get("packages", {}).items()}
        python = minimum_python or ".".join(str(info.get("python", "")).split(".")[:2])
    else:
        packages = updates.parse_requirements(requirements.read_text(encoding="utf-8"))
        python = minimum_python or f"{sys.version_info.major}.{sys.version_info.minor}"
    if not re.match(r"^\d+\.\d+(\.\d+)?$", python or ""):
        raise BuildError(f"Cannot tell which Python the update needs ('{python}'); pass --minimum-python.")
    return {"python": python, "packages": dict(sorted(packages.items()))}


def package_files(folder: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for part in updates.PARTS:
        if not (folder / part).is_dir():
            raise BuildError(f"{folder / part} is missing: assemble the folder first.")
        for path in sorted((folder / part).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in IGNORED_SUFFIXES:
                continue
            name = path.relative_to(folder).as_posix()
            try:
                updates.check_member_name(name)
            except updates.UpdateError as exc:
                raise BuildError(f"{exc} Update packages cannot carry it; ship it with the installer instead.") from exc
            files[name] = sha256_file(path)
    return files


def package_update(folder: Path, out: Path, *, signing_key=None, minimum_python: str | None = None,
                   requirements: Path | None = None, built_at: str | None = None) -> dict:
    """Write the update package for an assembled folder. Returns its manifest."""
    release = json.loads((folder / "app" / "release.json").read_text(encoding="utf-8"))
    version = str(release["version"])
    files = package_files(folder)
    build_manifest = folder / "build-manifest.json"
    commit = json.loads(build_manifest.read_text(encoding="utf-8")).get("git_commit") \
        if build_manifest.exists() else None
    manifest = {
        "format": updates.FORMAT,
        "product": updates.PRODUCT,
        "version": version,
        "built_at": built_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": commit or git_commit(),
        "requires": runtime_requirements(folder, requirements or ROOT / "desktop" / "requirements.lock",
                                         minimum_python),
        "files": files,
    }
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".partial")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(updates.MANIFEST_NAME, manifest_bytes)
        if signing_key is not None:
            archive.writestr(updates.SIGNATURE_NAME, base64.b64encode(signing_key.sign(manifest_bytes)) + b"\n")
        for name in files:
            archive.write(folder / name, name)
    package = updates.read_package(temporary)  # the app's own reader must accept it
    updates.verify_package_files(package)
    if signing_key is not None:
        updates.verify_signature(package, [base64.b64decode(public_key_text(signing_key))])
    temporary.replace(out)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "desktop")
    parser.add_argument("--python-embed", type=Path, help="Windows embeddable Python zip")
    parser.add_argument("--python-version", help="Full version of the embeddable Python (recorded in runtime.json)")
    parser.add_argument("--skip-runtime", action="store_true")
    parser.add_argument("--requirements", type=Path, default=ROOT / "desktop" / "requirements.lock")
    parser.add_argument("--version-override", help="Build under another version (installer upgrade tests only).")
    parser.add_argument("--from-folder", type=Path, help="Package this assembled folder instead of building one.")
    parser.add_argument("--update-package", type=Path, help="Also write the update package "
                                                            "(ModernformQuoteHelperUpdate-*.zip).")
    parser.add_argument("--minimum-python", help="Oldest Python the update's code runs on (default: the runtime's "
                                                 "major.minor, e.g. 3.13).")
    parser.add_argument("--signing-key-env", default="UPDATE_SIGNING_KEY",
                        help="Environment variable holding the Ed25519 update signing key (unsigned when empty).")
    parser.add_argument("--generate-signing-key", action="store_true",
                        help="Print a new update signing key pair and exit.")
    args = parser.parse_args(argv)
    if args.generate_signing_key:
        private, public = generate_signing_key()
        print("Private key (store it only as the repository secret UPDATE_SIGNING_KEY; never commit it):")
        print(private)
        print("Public key (builds made with the secret write it to desktop/update-signing-key.pub):")
        print(public)
        return 0
    if not args.from_folder and not args.skip_runtime and not args.python_embed:
        parser.error("--python-embed is required unless --skip-runtime or --from-folder is given")
    if args.from_folder and not args.update_package:
        parser.error("--from-folder needs --update-package")
    try:
        key = signing_key_from_env(args.signing_key_env)
        folder = args.from_folder or args.out
        if not args.from_folder:
            manifest = build(args.out, embed_zip=None if args.skip_runtime else args.python_embed,
                             requirements=args.requirements, version_override=args.version_override,
                             python_version=args.python_version, signing_key=key)
            print(f"Built Quote Helper {manifest['version']} desktop folder in {args.out} "
                  f"({len(manifest['files'])} files).")
        if args.update_package:
            package = package_update(folder, args.update_package, signing_key=key, minimum_python=args.minimum_python,
                                     requirements=args.requirements)
            print(f"Wrote update package {args.update_package} for {package['version']} "
                  f"({len(package['files'])} files, {'signed' if key else 'unsigned'}, needs Python "
                  f"{package['requires']['python']}).")
    except (BuildError, updates.UpdateError) as exc:
        print(f"Build stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
