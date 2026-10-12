"""Update packages: built and verified, applied by renames that never leave a half-updated program,
rolled back, and offered to the app window only through local, same-origin requests."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(ROOT / "scripts"))

import build_desktop  # noqa: E402
import desktop_common as common  # noqa: E402
import desktop_update as updates  # noqa: E402
from desktop_server import DesktopControl, UpdateService  # noqa: E402

PACKAGES = {"fastapi": "0.141.1", "pydantic-core": "2.46.4"}
PORT = 8123


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    monkeypatch.setenv("QUOTE_HELPER_HOME", str(path))
    monkeypatch.delenv("QUOTE_HELPER_APP_DIR", raising=False)
    monkeypatch.delenv(common.INSTALL_DIR_ENV, raising=False)
    monkeypatch.delenv("UPDATE_SIGNING_KEY", raising=False)
    monkeypatch.setattr(updates, "RENAME_RETRY_SECONDS", 0.0)
    updates._CHECKED.clear()
    return path


def program_folder(folder: Path, version: str, *, marker: str = "") -> Path:
    """The app/, bridge/ and desktop/ folders of one version (as scripts/build_desktop.py assembles them)."""
    (folder / "app/backend/app").mkdir(parents=True)
    (folder / "app/backend/app/main.py").write_text(f"VERSION = {version!r}  # {marker}\n")
    (folder / "app/backend/app/__pycache__").mkdir()
    (folder / "app/backend/app/__pycache__/main.cpython-313.pyc").write_bytes(b"cache")
    (folder / "app/release.json").write_text(json.dumps({"version": version}))
    (folder / "bridge").mkdir()
    (folder / "bridge/manifest.json").write_text(json.dumps({"version": version}))
    (folder / "desktop").mkdir()
    for name in ("desktop_common.py", "desktop_launcher.py", "desktop_server.py", "desktop_update.py",
                 "launcher.pyw", "quote-helper.ico"):
        shutil.copy2(DESKTOP / name, folder / "desktop" / name)
    return folder


def make_install(tmp_path: Path, version: str = "3.1.0", *, python: str = "3.13.16", key: str | None = None,
                 packages: dict[str, str] | None = None) -> Path:
    install = program_folder(tmp_path / "Modernform Quote Helper", version)
    runtime = install / "runtime"
    (runtime / "Lib/site-packages").mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"signed python")
    (runtime / "runtime.json").write_text(json.dumps({"python": python, "packages": PACKAGES}))
    for name, value in (PACKAGES if packages is None else packages).items():
        (runtime / "Lib/site-packages" / f"{name.replace('-', '_')}-{value}.dist-info").mkdir()
    if key:
        (install / "desktop" / updates.PUBLIC_KEY_NAME).write_text(f"# pinned\n{key}\n")
    return install


def make_package(tmp_path: Path, version: str = "3.2.0", *, signing_key=None, python: str = "3.13",
                 packages: dict[str, str] | None = None, name: str | None = None) -> Path:
    folder = program_folder(tmp_path / f"build-{version}-{time.time_ns()}", version, marker="new")
    lock = folder.parent / f"lock-{version}-{time.time_ns()}.txt"
    lock.write_text("".join(f"{key}=={value}\n" for key, value in (PACKAGES if packages is None else packages).items()))
    out = tmp_path / "out" / (name or f"ModernformQuoteHelperUpdate-{version}.zip")
    build_desktop.package_update(folder, out, signing_key=signing_key, minimum_python=python, requirements=lock)
    return out


def tree_hashes(folder: Path) -> dict[str, str]:
    return {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def rewrite_zip(source: Path, target: Path, change) -> Path:
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    change(entries)
    with zipfile.ZipFile(target, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return target


def signing_pair():
    pytest.importorskip("cryptography")
    private, public = build_desktop.generate_signing_key()
    return build_desktop.load_signing_key(private), public


# --------------------------------------------------------------------------- versions and settings


def test_versions_order_numbers_numerically_and_build_suffixes_after_the_plain_version():
    key = updates.version_key
    assert key("3.1.10") > key("3.1.9") > key("3.1") == key("3.1.0")
    assert key("3.1.0-ci.2") > key("3.1.0") and key("3.1.0-ci.10") > key("3.1.0-ci.2")
    assert key("3.2.0") > key("3.1.0-ci.2")
    with pytest.raises(updates.UpdateError):
        key("latest")


def test_update_folder_setting_is_read_and_unknown_settings_are_kept(home, tmp_path):
    home.mkdir()
    shared = tmp_path / "OneDrive" / "Quote Helper updates"
    (home / "config.json").write_text(json.dumps({"data_dir": str(tmp_path / "data"), "port": 8000,
                                                  "update_folder": str(shared), "future_key": [1]}))
    config = common.load_config()
    assert config.update_folder == shared
    assert updates.update_folders(config) == [shared, home / "updates"]
    common.save_config(common.Config(data_dir=tmp_path / "other", port=8001))
    saved = json.loads((home / "config.json").read_text())
    assert saved["future_key"] == [1] and saved["update_folder"] == str(shared) and saved["port"] == 8001
    (home / "config.json").write_text(json.dumps({"update_folder": 5}))
    with pytest.raises(common.LauncherError, match="update_folder"):
        common.load_config()


# --------------------------------------------------------------------------- building


def test_update_package_holds_only_program_files_with_their_checksums(tmp_path):
    package_path = make_package(tmp_path, "3.2.0")
    with zipfile.ZipFile(package_path) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read(updates.MANIFEST_NAME))
        assert all(archive.read(name) and hashlib.sha256(archive.read(name)).hexdigest() == digest
                   for name, digest in manifest["files"].items())
    assert manifest["product"] == updates.PRODUCT and manifest["format"] == updates.FORMAT
    assert manifest["version"] == "3.2.0" and manifest["built_at"]
    assert manifest["requires"] == {"python": "3.13", "packages": PACKAGES}
    assert names == set(manifest["files"]) | {updates.MANIFEST_NAME}  # unsigned: no signature file
    assert {name.split("/")[0] for name in manifest["files"]} == {"app", "bridge", "desktop"}
    assert "desktop/desktop_update.py" in names and "app/release.json" in names
    assert not any("__pycache__" in name or name.startswith("runtime/") for name in names)


def test_update_package_uses_the_assembled_runtime_and_the_full_build(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "backend/app").mkdir(parents=True)
    (root / "backend/app/main.py").write_text("app = None\n")
    (root / "frontend/dist").mkdir(parents=True)
    (root / "frontend/dist/index.html").write_text("<html></html>")
    (root / "build2quote_bridge/dist").mkdir(parents=True)
    (root / "build2quote_bridge/dist/manifest.json").write_text(json.dumps({"version": "3.2.0"}))
    (root / "release.json").write_text(json.dumps({"version": "3.2.0"}))
    shutil.copytree(DESKTOP, root / "desktop", ignore=shutil.ignore_patterns("__pycache__"))
    out = tmp_path / "build"
    build_desktop.build(out, root=root)
    (out / "runtime").mkdir()  # as install_runtime records it
    (out / "runtime/runtime.json").write_text(json.dumps({"python": "3.13.16", "packages": {"FastAPI": "1.0"}}))
    (out / "runtime/python.exe").write_bytes(b"exe")
    manifest = build_desktop.package_update(out, tmp_path / "update.zip")
    assert manifest["requires"] == {"python": "3.13", "packages": {"fastapi": "1.0"}}
    assert manifest["git_commit"] == json.loads((out / "build-manifest.json").read_text())["git_commit"]
    assert not any(name.startswith("runtime/") for name in manifest["files"])

    (out / "bridge/helper.exe").write_bytes(b"MZ")
    with pytest.raises(build_desktop.BuildError, match="helper.exe"):
        build_desktop.package_update(out, tmp_path / "update.zip")


def test_signed_builds_pin_the_public_key_and_sign_the_manifest(tmp_path, monkeypatch):
    key, public = signing_pair()
    private_text = base64.b64encode(key.private_bytes_raw()).decode()
    monkeypatch.setenv("QH_TEST_KEY", private_text)
    assert build_desktop.public_key_text(build_desktop.signing_key_from_env("QH_TEST_KEY")) == public
    assert build_desktop.signing_key_from_env("QH_UNSET_KEY") is None
    package = updates.read_package(make_package(tmp_path, signing_key=key))
    assert package.signature is not None
    assert updates.verify_signature(package, [base64.b64decode(public)]) is True
    with pytest.raises(build_desktop.BuildError, match="could not be read"):
        build_desktop.load_signing_key("not a key")


# --------------------------------------------------------------------------- verification


def test_a_valid_newer_package_passes_every_check(home, tmp_path):
    install = make_install(tmp_path)
    package = updates.read_package(make_package(tmp_path))
    assert updates.check_package(package, install) is False  # accepted, unsigned (no key pinned)


def test_a_changed_file_fails_its_checksum(home, tmp_path):
    install = make_install(tmp_path)
    good = make_package(tmp_path)

    def tamper(entries):
        entries["app/backend/app/main.py"] = b"import os; os.system('evil')\n"

    package = updates.read_package(rewrite_zip(good, tmp_path / "bad.zip", tamper))
    with pytest.raises(updates.UpdateError, match="main.py .*checksum"):
        updates.check_package(package, install)


@pytest.mark.parametrize("change, message", [
    (lambda entries: entries.__setitem__("app/extra.py", b"x"), "does not list"),
    (lambda entries: entries.pop("bridge/manifest.json"), "missing bridge/manifest.json"),
    (lambda entries: entries.pop(updates.MANIFEST_NAME), "not a Modernform Quote Helper update package"),
])
def test_unlisted_or_missing_files_are_refused(tmp_path, change, message):
    bad = rewrite_zip(make_package(tmp_path), tmp_path / "bad.zip", change)
    with pytest.raises(updates.UpdateError, match=message):
        updates.read_package(bad)


@pytest.mark.parametrize("name", ["../evil.py", "runtime/python.exe", "app/../../x.py", "bridge/tool.exe",
                                  "desktop/run.ps1", "app/backend/x.pyd", "C:/x.py", "app\\x.py"])
def test_manifests_naming_files_outside_the_program_or_executables_are_refused(tmp_path, name):
    def change(entries):
        manifest = json.loads(entries[updates.MANIFEST_NAME])
        manifest["files"][name] = "0" * 64
        entries[updates.MANIFEST_NAME] = json.dumps(manifest).encode()
        entries[name] = b"x"

    with pytest.raises(updates.UpdateError, match="outside the program folders|file type"):
        updates.read_package(rewrite_zip(make_package(tmp_path), tmp_path / "bad.zip", change))


def test_older_and_same_versions_are_refused_unless_reinstalling(home, tmp_path):
    install = make_install(tmp_path, "3.2.0")
    with pytest.raises(updates.UpdateError, match="older than the installed 3.2.0"):
        updates.check_package(updates.read_package(make_package(tmp_path, "3.1.9")), install)
    same = updates.read_package(make_package(tmp_path, "3.2.0"))
    with pytest.raises(updates.UpdateError, match="already installed"):
        updates.check_package(same, install)
    updates.check_package(same, install, reinstall=True)


@pytest.mark.parametrize("python, packages, message", [
    ("3.14", None, "needs Python 3.14"),
    ("3.13.20", None, "needs Python 3.13.20"),
    ("3.13", {**PACKAGES, "fastapi": "0.200.0"}, "fastapi 0.200.0"),
    ("3.13", {**PACKAGES, "newlib": "1.0"}, "newlib 1.0"),
])
def test_a_package_needing_a_newer_runtime_asks_for_the_full_installer(home, tmp_path, python, packages, message):
    install = make_install(tmp_path, python="3.13.16")
    package = updates.read_package(make_package(tmp_path, python=python, packages=packages))
    with pytest.raises(updates.UpdateError, match=message) as error:
        updates.check_package(package, install)
    assert error.value.needs_installer and "ModernformQuoteHelperSetup-3.2.0.exe" in str(error.value)


def test_runtime_packages_are_read_from_the_installed_runtime(tmp_path):
    install = make_install(tmp_path, packages={"pydantic_core": "2.46.4", "fastapi": "0.141.1"})
    (install / "runtime/runtime.json").unlink()
    runtime = updates.installed_runtime(install)
    assert runtime["packages"] == {"pydantic-core": "2.46.4", "fastapi": "0.141.1"}
    (install / "runtime/python313._pth").write_text("")
    assert updates.installed_runtime(install)["python"] in {"3.13", sys.version.split()[0]}
    assert updates.installed_runtime(tmp_path / "nowhere") is None


def test_with_a_pinned_key_only_packages_signed_with_it_are_accepted(home, tmp_path):
    key, public = signing_pair()
    other, _ = signing_pair()
    install = make_install(tmp_path, key=public)
    with pytest.raises(updates.UpdateError, match="not signed"):
        updates.check_package(updates.read_package(make_package(tmp_path)), install)
    with pytest.raises(updates.UpdateError, match="signature does not match"):
        updates.check_package(updates.read_package(make_package(tmp_path, signing_key=other)), install)
    signed = make_package(tmp_path, signing_key=key)
    assert updates.check_package(updates.read_package(signed), install) is True

    def loosen(entries):  # change the manifest after signing, e.g. to ask for a different runtime
        entries[updates.MANIFEST_NAME] = entries[updates.MANIFEST_NAME].replace(b'"3.13"', b'"3.12"')

    tampered = updates.read_package(rewrite_zip(signed, tmp_path / "tampered.zip", loosen))
    with pytest.raises(updates.UpdateError, match="signature does not match"):
        updates.check_package(tampered, install)


def test_without_a_pinned_key_signed_and_unsigned_packages_are_accepted(home, tmp_path):
    key, _ = signing_pair()
    install = make_install(tmp_path)
    assert updates.check_package(updates.read_package(make_package(tmp_path, signing_key=key)), install) is False


# --------------------------------------------------------------------------- applying


class Recorder:
    def __init__(self, starts=(True,)):
        self.calls: list[str] = []
        self.starts = list(starts)

    def stop(self):
        self.calls.append("stop")

    def start(self):
        self.calls.append("start")
        return self.starts.pop(0) if self.starts else True


def user_files(home: Path, tmp_path: Path) -> dict[str, bytes]:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "quote_helper_v2.sqlite3").write_bytes(b"database")
    common.save_config(common.Config(data_dir=data, port=8000))
    return {"config": (home / "config.json").read_bytes(), "db": (data / "quote_helper_v2.sqlite3").read_bytes()}


def test_applying_swaps_the_program_folders_and_keeps_the_previous_version(home, tmp_path):
    install = make_install(tmp_path, "3.1.0")
    before = user_files(home, tmp_path)
    runtime_before = tree_hashes(install / "runtime")
    old_app = tree_hashes(install / "app")
    recorder = Recorder()
    result = updates.apply_package(install, make_package(tmp_path, "3.2.0"), stop=recorder.stop,
                                   start=recorder.start)
    assert result["ok"], result
    assert result["from_version"] == "3.1.0" and result["to_version"] == "3.2.0" and result["signed"] is False
    assert recorder.calls == ["stop", "start"]
    assert common.installed_version(install / "app") == "3.2.0"
    assert "# new" in (install / "app/backend/app/main.py").read_text()
    assert not (install / "app/backend/app/__pycache__").exists()  # no stale caches carried over
    assert updates.previous_version(install) == "3.1.0" and tree_hashes(install / "previous/app") == old_app
    assert tree_hashes(install / "runtime") == runtime_before  # the runtime is never touched
    assert {"config": (home / "config.json").read_bytes(),
            "db": (tmp_path / "data/quote_helper_v2.sqlite3").read_bytes()} == before
    work = install / updates.WORK_DIR
    assert not (work / "incoming").exists() and not (work / "journal.json").exists()
    assert not (work / "apply.lock").exists()
    assert updates.read_last_result()["ok"] is True


def test_rollback_swaps_back_and_again_forward(home, tmp_path):
    install = make_install(tmp_path, "3.1.0")
    recorder = Recorder()
    assert updates.apply_package(install, make_package(tmp_path, "3.2.0"), stop=recorder.stop,
                                 start=recorder.start)["ok"]
    result = updates.rollback(install, stop=recorder.stop, start=recorder.start)
    assert result["ok"] and result["to_version"] == "3.1.0"
    assert common.installed_version(install / "app") == "3.1.0" and updates.previous_version(install) == "3.2.0"
    assert updates.rollback(install, stop=recorder.stop, start=recorder.start)["ok"]
    assert common.installed_version(install / "app") == "3.2.0"
    assert recorder.calls == ["stop", "start"] * 3


def test_rollback_without_a_previous_version_changes_nothing(home, tmp_path):
    install = make_install(tmp_path)
    recorder = Recorder()
    result = updates.rollback(install, stop=recorder.stop, start=recorder.start)
    assert not result["ok"] and "no previous version" in result["message"] and recorder.calls == []


def test_a_bad_package_never_stops_the_running_version(home, tmp_path):
    install = make_install(tmp_path, "3.2.0")
    recorder = Recorder()
    result = updates.apply_package(install, make_package(tmp_path, "3.1.0"), stop=recorder.stop,
                                   start=recorder.start)
    assert not result["ok"] and "older" in result["message"] and recorder.calls == []
    assert common.installed_version(install / "app") == "3.2.0" and updates.read_last_result()["ok"] is False


@pytest.mark.parametrize("failing_part", ["app", "bridge", "desktop"])
def test_a_failure_part_way_puts_every_folder_back(home, tmp_path, monkeypatch, failing_part):
    install = make_install(tmp_path, "3.1.0")
    before = tree_hashes(install)
    real_rename = updates._rename

    def flaky(source, destination):
        if Path(source).parent.name == "incoming" and Path(destination) == install / failing_part:
            raise PermissionError(13, "The process cannot access the file", str(destination))
        real_rename(source, destination)

    monkeypatch.setattr(updates, "_rename", flaky)
    recorder = Recorder()
    result = updates.apply_package(install, make_package(tmp_path, "3.2.0"), stop=recorder.stop,
                                   start=recorder.start)
    assert not result["ok"] and f"could not replace its {failing_part} folder" in result["message"]
    assert "3.1.0 is still installed" in result["message"]
    assert recorder.calls == ["stop", "start"]  # the previous version is started again
    after = {name: digest for name, digest in tree_hashes(install).items() if not name.startswith("update-work/")}
    assert after == before


class PowerCut(BaseException):
    """Stops the process in the middle of a step, like a crash or power cut (nothing gets cleaned up)."""


def test_an_interrupted_swap_is_undone_at_the_next_start(home, tmp_path, monkeypatch):
    install = make_install(tmp_path, "3.1.0")
    before = tree_hashes(install)
    real_rename = updates._rename
    moves = []

    def cut(source, destination):
        moves.append(destination)
        if len(moves) == 4:  # app swapped, bridge moved out: the new bridge never arrives
            raise PowerCut
        real_rename(source, destination)

    monkeypatch.setattr(updates, "_rename", cut)
    with pytest.raises(PowerCut):
        updates.apply_package(install, make_package(tmp_path, "3.2.0"), stop=lambda: None, start=lambda: True)
    assert not (install / "bridge").exists()  # half-way: this is what a crash would leave
    monkeypatch.setattr(updates, "_rename", real_rename)
    assert "undone" in updates.recover(install)
    after = {name: digest for name, digest in tree_hashes(install).items() if not name.startswith("update-work/")}
    assert after == before
    assert updates.recover(install) is None


def test_an_interrupted_finish_is_completed_at_the_next_start(home, tmp_path, monkeypatch):
    install = make_install(tmp_path, "3.1.0")
    real_finish = updates._finish
    monkeypatch.setattr(updates, "_finish", lambda install: (_ for _ in ()).throw(PowerCut()))
    with pytest.raises(PowerCut):
        updates.apply_package(install, make_package(tmp_path, "3.2.0"), stop=lambda: None, start=lambda: True)
    monkeypatch.setattr(updates, "_finish", real_finish)
    assert "completed" in updates.recover(install)
    assert common.installed_version(install / "app") == "3.2.0" and updates.previous_version(install) == "3.1.0"


def test_a_new_version_that_does_not_start_is_replaced_by_the_previous_one(home, tmp_path):
    install = make_install(tmp_path, "3.1.0")
    recorder = Recorder(starts=(False, True))
    result = updates.apply_package(install, make_package(tmp_path, "3.2.0"), stop=recorder.stop,
                                   start=recorder.start)
    assert not result["ok"] and "did not start, so 3.1.0 was put back" in result["message"]
    assert common.installed_version(install / "app") == "3.1.0" and updates.previous_version(install) == "3.2.0"
    assert recorder.calls == ["stop", "start", "start"]


def test_only_one_update_runs_at_a_time(home, tmp_path):
    install = make_install(tmp_path)
    lock = install / updates.WORK_DIR / "apply.lock"
    lock.parent.mkdir(parents=True)
    helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock.write_text(str(helper.pid))
        assert updates.apply_in_progress(install)
        result = updates.apply_package(install, make_package(tmp_path), stop=lambda: None, start=lambda: True)
        assert not result["ok"] and "being installed" in result["message"]
    finally:
        helper.kill()
        helper.wait()
    assert not updates.apply_in_progress(install)  # a lock left by a process that ended is ignored
    assert updates.apply_package(install, make_package(tmp_path), stop=lambda: None, start=lambda: True)["ok"]


def test_the_update_step_runs_from_a_copy_of_the_launcher(tmp_path):
    install = make_install(tmp_path)
    copy = updates.prepare_launcher_copy(install)
    assert copy.parent == install / updates.WORK_DIR
    assert sorted(path.name for path in copy.iterdir()) == sorted(updates.LAUNCHER_MODULES)


# --------------------------------------------------------------------------- finding packages


def test_status_offers_the_newest_package_that_can_be_installed(home, tmp_path):
    install = make_install(tmp_path, "3.1.0")
    shared = tmp_path / "shared"
    shared.mkdir()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps({"data_dir": str(tmp_path / "data"), "update_folder": str(shared)}))
    shutil.copy(make_package(tmp_path, "3.0.9"), shared)  # older: ignored
    shutil.copy(make_package(tmp_path, "3.2.0"), shared)
    (home / "updates").mkdir()
    shutil.copy(make_package(tmp_path, "3.3.0", python="3.14"), home / "updates")  # needs the installer
    (shared / "ModernformQuoteHelperUpdate-9.9.9.zip").write_bytes(b"still syncing")
    status = updates.update_status(install, common.load_config())
    assert status["supported"] and status["installed_version"] == "3.1.0" and status["previous_version"] is None
    assert status["folders"] == [str(shared), str(home / "updates")]
    available = status["available"]
    assert available["version"] == "3.2.0" and available["can_apply"] and available["file"].startswith(str(shared))

    (shared / "ModernformQuoteHelperUpdate-3.2.0.zip").unlink()
    available = updates.update_status(install, common.load_config())["available"]
    assert available["version"] == "3.3.0" and not available["can_apply"] and available["needs_installer"]


def test_status_outside_an_installation_says_updates_do_not_apply(home):
    status = updates.update_status(ROOT, common.load_config())
    assert not status["supported"] and status["available"] is None


# --------------------------------------------------------------------------- the app window's endpoints


def update_client(tmp_path, home, *, port: int = PORT):
    install = make_install(tmp_path, "3.1.0")
    (home / "updates").mkdir(parents=True)
    shutil.copy(make_package(tmp_path, "3.2.0"), home / "updates")
    spawned: list[Path] = []
    app = Starlette(routes=[Route("/api/health", lambda request: PlainTextResponse("ok"))])
    control = DesktopControl(app, "token", lambda: None, port=port,
                             updates=UpdateService(install, spawn=spawned.append))
    return TestClient(control, base_url=f"http://127.0.0.1:{port}"), spawned


def test_update_status_answers_the_local_window_only(home, tmp_path):
    client, _ = update_client(tmp_path, home)
    answer = client.get(common.UPDATE_STATUS_PATH)
    assert answer.status_code == 200 and answer.headers["cache-control"] == "no-store"
    assert answer.json()["available"]["version"] == "3.2.0" and answer.json()["installed_version"] == "3.1.0"
    assert client.get(common.UPDATE_STATUS_PATH, headers={"Host": f"localhost:{PORT}"}).status_code == 200
    # A page on another site that rebinds its DNS name to 127.0.0.1 still sends its own Host.
    assert client.get(common.UPDATE_STATUS_PATH, headers={"Host": f"evil.example:{PORT}"}).status_code == 403
    assert client.get(common.UPDATE_STATUS_PATH, headers={"Host": "127.0.0.1:9999"}).status_code == 403
    assert client.post(common.UPDATE_STATUS_PATH).status_code == 405
    assert client.get("/api/health").text == "ok"


def test_installing_needs_the_header_and_a_same_origin_request(home, tmp_path):
    client, spawned = update_client(tmp_path, home)
    origin = f"http://127.0.0.1:{PORT}"
    good = {"X-Quote-Helper": "1", "Origin": origin}
    assert client.post(common.APPLY_UPDATE_PATH, json={}).status_code == 403  # no header, no origin
    assert client.post(common.APPLY_UPDATE_PATH, json={}, headers={"Origin": origin}).status_code == 403
    assert client.post(common.APPLY_UPDATE_PATH, json={}, headers={"X-Quote-Helper": "1"}).status_code == 403
    for bad_origin in ("http://evil.example", "null", f"http://127.0.0.1:{PORT + 1}", f"https://127.0.0.1:{PORT}"):
        answer = client.post(common.APPLY_UPDATE_PATH, json={}, headers={**good, "Origin": bad_origin})
        assert answer.status_code == 403, bad_origin
    answer = client.post(common.APPLY_UPDATE_PATH, json={}, headers={**good, "Host": f"evil.example:{PORT}"})
    assert answer.status_code == 403
    assert client.get(common.APPLY_UPDATE_PATH, headers=good).status_code == 405
    assert spawned == []
    assert client.post(common.APPLY_UPDATE_PATH, json={"version": "3.3.0"}, headers=good).status_code == 409
    answer = client.post(common.APPLY_UPDATE_PATH, json={"version": "3.2.0"},
                         headers={**good, "Origin": f"http://localhost:{PORT}", "Host": f"localhost:{PORT}"})
    assert answer.status_code == 202 and answer.json() == {"status": "updating", "from_version": "3.1.0",
                                                           "version": "3.2.0"}
    assert [path.name for path in spawned] == ["ModernformQuoteHelperUpdate-3.2.0.zip"]


def test_installing_when_nothing_can_be_installed_is_refused(home, tmp_path):
    client, spawned = update_client(tmp_path, home)
    (home / "updates" / "ModernformQuoteHelperUpdate-3.2.0.zip").unlink()
    answer = client.post(common.APPLY_UPDATE_PATH, json={},
                         headers={"X-Quote-Helper": "1", "Origin": f"http://127.0.0.1:{PORT}"})
    assert answer.status_code == 409 and "No update" in answer.json()["detail"] and spawned == []


def test_update_routes_do_not_exist_outside_the_desktop_server(api):
    """The development server and Preview have no update routes, so the banner stays hidden."""
    assert api.get(common.UPDATE_STATUS_PATH).status_code == 404
    assert api.post(common.APPLY_UPDATE_PATH, headers={"X-Quote-Helper": "1"}).status_code in {404, 405}
    plain = DesktopControl(Starlette(), "token", lambda: None)  # a desktop server without the update service
    assert TestClient(plain).get(common.UPDATE_STATUS_PATH).status_code == 404


@pytest.mark.skipif(sys.platform != "win32", reason="Windows dialog structure")
def test_the_open_file_dialog_structure_has_the_size_windows_expects():
    import ctypes

    import desktop_launcher

    assert ctypes.sizeof(desktop_launcher.open_file_dialog_structure()) == (152 if sys.maxsize > 2**32 else 88)


# --------------------------------------------------------------------------- end to end


def free_port() -> int:
    for port in range(18300, 18400):
        if common.port_free(port):
            return port
    raise RuntimeError("no free port")


def health(port: int) -> dict | None:
    return common.health(port, timeout=2.0)


def real_install(tmp_path: Path, version: str) -> Path:
    """An installed layout around this checkout's backend, run by the current Python."""
    folder = tmp_path / f"Quote Helper {version}"
    shutil.copytree(ROOT / "backend/app", folder / "app/backend/app", ignore=shutil.ignore_patterns("__pycache__"))
    release = json.loads((ROOT / "release.json").read_text())
    (folder / "app/release.json").write_text(json.dumps({**release, "version": version}))
    (folder / "bridge").mkdir()
    (folder / "bridge/manifest.json").write_text(json.dumps({"version": version}))
    (folder / "desktop").mkdir()
    for name in ("desktop_common.py", "desktop_launcher.py", "desktop_server.py", "desktop_update.py",
                 "launcher.pyw", "quote-helper.ico"):
        shutil.copy2(DESKTOP / name, folder / "desktop" / name)
    return folder


def test_launcher_applies_an_update_package_and_rolls_it_back(home, tmp_path, monkeypatch):
    """The real launcher, server and backend: no installer, data kept, backup made, rollback works."""
    monkeypatch.setattr(updates, "RENAME_RETRY_SECONDS", 15.0)
    version = json.loads((ROOT / "release.json").read_text())["version"]
    install = real_install(tmp_path, version)
    (install / "runtime").mkdir()
    python = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    (install / "runtime/runtime.json").write_text(json.dumps({"python": python, "packages": {}}))
    next_folder = real_install(tmp_path, f"{version}-ci.2")
    lock = tmp_path / "empty.lock"
    lock.write_text("")
    package = tmp_path / f"ModernformQuoteHelperUpdate-{version}-ci.2.zip"
    build_desktop.package_update(next_folder, package, requirements=lock,
                                 minimum_python=f"{sys.version_info.major}.{sys.version_info.minor}")
    data = tmp_path / "data folder"
    port = free_port()
    common.save_config(common.Config(data_dir=data, port=port))
    config_bytes = (home / "config.json").read_bytes()
    launcher = install / "desktop" / "launcher.pyw"

    def run(*arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(launcher), *arguments], capture_output=True, text=True,
                              timeout=300, cwd=str(tmp_path))

    try:
        assert run("--no-window").returncode == 0
        assert health(port)["version"] == version
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/orders", method="POST",
                                         data=json.dumps({"order_number": "UPD-1", "lines": []}).encode(),
                                         headers={"Content-Type": "application/json"})
        with common._OPENER.open(request, timeout=10) as response:
            order = json.loads(response.read())

        applied = run("--apply-update", str(package), "--no-window")
        assert applied.returncode == 0, applied.stdout + applied.stderr
        assert json.loads(applied.stdout)["ok"] is True
        assert health(port)["version"] == f"{version}-ci.2"
        assert common.read_state()["version"] == f"{version}-ci.2"
        with common._OPENER.open(f"http://127.0.0.1:{port}/api/orders/{order['id']}", timeout=10) as response:
            assert json.loads(response.read())["order"]["order_number"] == order["order_number"]
        assert list((data / "backups").glob(f"quote-helper-v2-before-update-{version}-ci.2-*.sqlite3"))
        assert (home / "config.json").read_bytes() == config_bytes
        assert updates.previous_version(install) == version
        assert not list((install / updates.WORK_DIR).glob("launcher-*"))  # the launcher copy is removed

        rolled = run("--rollback-update", "--no-window")
        assert rolled.returncode == 0, rolled.stdout + rolled.stderr
        assert health(port)["version"] == version
        assert updates.previous_version(install) == f"{version}-ci.2"
    finally:
        run("--stop")
    assert health(port) is None
