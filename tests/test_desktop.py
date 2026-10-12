"""Desktop launcher: settings outside the program folder, background server start/stop, shutdown token."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

DESKTOP = Path(__file__).resolve().parents[1] / "desktop"
sys.path.insert(0, str(DESKTOP))

import desktop_common as common  # noqa: E402
import desktop_launcher as launcher  # noqa: E402
from desktop_server import DesktopControl  # noqa: E402


@pytest.fixture
def desktop_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("QUOTE_HELPER_HOME", str(home))
    monkeypatch.delenv("QUOTE_HELPER_APP_DIR", raising=False)
    monkeypatch.delenv("QUOTE_HELPER_V2_DATABASE", raising=False)
    return home


def free_port() -> int:
    for port in range(18100, 18200):
        if common.port_free(port):
            return port
    raise RuntimeError("no free port")


# --------------------------------------------------------------------------- configuration


def test_settings_default_to_a_data_folder_outside_the_program(desktop_home):
    config = common.load_config()
    assert config.data_dir == desktop_home / "data" and config.port == common.DEFAULT_PORT
    assert config.database == desktop_home / "data" / "quote_helper_v2.sqlite3"
    assert common.install_dir() not in config.data_dir.parents


def test_settings_written_by_the_installer_are_read(desktop_home):
    desktop_home.mkdir()
    existing = desktop_home.parent / "Modernform data"
    # Inno Setup writes JSON with doubled backslashes; a UTF-8 BOM must not matter either.
    (desktop_home / "config.json").write_text("﻿" + json.dumps({"data_dir": str(existing), "port": 8001}),
                                              encoding="utf-8")
    assert common.load_config() == common.Config(data_dir=existing, port=8001)


@pytest.mark.parametrize("content", ["{not json", "[]", json.dumps({"port": 80})])
def test_damaged_settings_are_reported_in_plain_words(desktop_home, content):
    desktop_home.mkdir()
    (desktop_home / "config.json").write_text(content, encoding="utf-8")
    with pytest.raises(common.LauncherError, match="config.json"):
        common.load_config()


def test_running_from_a_checkout_uses_the_checkout_as_the_app_folder(monkeypatch):
    monkeypatch.delenv("QUOTE_HELPER_APP_DIR", raising=False)
    assert common.app_dir() == DESKTOP.parent
    assert common.installed_version() == json.loads((DESKTOP.parent / "release.json").read_text())["version"]


# --------------------------------------------------------------------------- shutdown route


def control_client(token: str = "secret-token"):
    calls: list[str] = []
    app = Starlette(routes=[Route("/api/health", lambda request: PlainTextResponse("ok"))])
    return TestClient(DesktopControl(app, token, lambda: calls.append("stop"))), calls


def test_shutdown_needs_the_token_and_a_post():
    client, calls = control_client()
    assert client.post(common.SHUTDOWN_PATH).status_code == 403
    assert client.post(common.SHUTDOWN_PATH, headers={"X-Desktop-Token": "wrong"}).status_code == 403
    assert client.get(common.SHUTDOWN_PATH, headers={"X-Desktop-Token": "secret-token"}).status_code == 403
    assert calls == []
    assert client.post(common.SHUTDOWN_PATH, headers={"X-Desktop-Token": "secret-token"}).status_code == 202
    assert calls == ["stop"]
    assert client.get("/api/health").text == "ok"  # everything else reaches the app


def test_an_empty_token_never_allows_shutdown():
    client, calls = control_client(token="")
    assert client.post(common.SHUTDOWN_PATH, headers={"X-Desktop-Token": ""}).status_code == 403
    assert calls == []


# --------------------------------------------------------------------------- start and stop


def test_launcher_starts_reuses_and_stops_the_background_server(desktop_home):
    data = desktop_home.parent / "data folder"
    port = free_port()
    common.save_config(common.Config(data_dir=data, port=port))
    try:
        assert launcher.main(["--no-window"]) == 0
        state = common.read_state()
        assert state and state["port"] == port and state["data_dir"] == str(data)
        assert common.pid_alive(state["pid"])
        answer = common.health(port)
        assert answer and answer["version"] == common.installed_version()
        assert (data / "quote_helper_v2.sqlite3").exists()  # the database lives in the configured folder
        status = launcher.status()
        assert status["running"] and status["port"] == port and status["data_dir"] == str(data)

        assert launcher.main(["--no-window"]) == 0  # already running: reused, not restarted
        assert common.read_state()["pid"] == state["pid"]
    finally:
        assert launcher.main(["--stop"]) == 0
    assert common.read_state() is None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and common.pid_alive(state["pid"]):
        time.sleep(0.2)
    assert not common.pid_alive(state["pid"])
    assert common.health(port, timeout=0.5) is None
    assert "Quote Helper stopped" in common.log_path().read_text(encoding="utf-8")
    assert launcher.main(["--stop"]) == 0  # stopping when nothing runs is fine


def test_another_quote_helper_on_the_port_is_reported_not_replaced(desktop_home, monkeypatch):
    port = free_port()
    common.save_config(common.Config(data_dir=desktop_home / "data", port=port))
    monkeypatch.setattr(common, "port_free", lambda candidate: candidate != port)
    monkeypatch.setattr(common, "health", lambda candidate, timeout=2.0: {"status": "ok", "version": "2.10.0"}
                        if candidate == port else None)
    messages: list[str] = []
    monkeypatch.setattr(launcher, "notify", lambda message, error=False: messages.append(message))
    assert launcher.main(["--no-window"]) == 1
    assert "version 2.10.0" in messages[0] and "Stop Modernform Quote Helper" in messages[0]
    assert common.read_state() is None


def test_a_server_that_cannot_start_shows_the_reason(desktop_home, monkeypatch):
    """A database changed by a newer incompatible version stops the start with that explanation."""
    import sqlite3

    data = desktop_home / "data"
    data.mkdir(parents=True)
    with sqlite3.connect(data / "quote_helper_v2.sqlite3") as connection:
        connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany("INSERT INTO app_meta VALUES (?, ?)",
                               [("last_app_version", "9.0.0"), ("database_compatibility", "99")])
    common.save_config(common.Config(data_dir=data, port=free_port()))
    monkeypatch.setattr(launcher, "START_TIMEOUT", 60.0)
    messages: list[str] = []
    monkeypatch.setattr(launcher, "notify", lambda message, error=False: messages.append(message))
    assert launcher.main(["--no-window"]) == 1
    assert "could not start" in messages[0] and "9.0.0" in messages[0] and "server.log" in messages[0]
    assert common.read_state() is None


# --------------------------------------------------------------------------- build folder


def fake_checkout(tmp_path: Path, version: str = "3.1.0") -> Path:
    root = tmp_path / "checkout"
    (root / "backend/app/__pycache__").mkdir(parents=True)
    (root / "backend/app/main.py").write_text("app = None\n")
    (root / "backend/app/__pycache__/main.cpython-313.pyc").write_bytes(b"cache")
    (root / "frontend/dist/assets").mkdir(parents=True)
    (root / "frontend/dist/index.html").write_text('<script src="/assets/index-1.js"></script>')
    (root / "frontend/dist/assets/index-1.js").write_text("// ui")
    (root / "build2quote_bridge/dist").mkdir(parents=True)
    (root / "build2quote_bridge/dist/manifest.json").write_text(json.dumps({"version": version}))
    (root / "release.json").write_text(json.dumps({"version": version}))
    (root / "desktop").mkdir()
    for name in ("desktop_common.py", "desktop_launcher.py", "desktop_server.py", "desktop_update.py",
                 "launcher.pyw", "quote-helper.ico"):
        (root / "desktop" / name).write_bytes((DESKTOP / name).read_bytes())
    return root


def test_build_folder_has_the_installed_layout_and_a_manifest(tmp_path):
    sys.path.insert(0, str(DESKTOP.parent / "scripts"))
    import build_desktop

    root = fake_checkout(tmp_path)
    out = tmp_path / "out"
    manifest = build_desktop.build(out, root=root)
    assert manifest["version"] == "3.1.0" and manifest["python"] is None
    assert (out / "app/backend/app/main.py").exists()
    assert not (out / "app/backend/app/__pycache__").exists()
    assert (out / "app/frontend/dist/assets/index-1.js").exists()
    assert (out / "bridge/manifest.json").exists() and (out / "desktop/launcher.pyw").exists()
    assert json.loads((out / "app/release.json").read_text())["version"] == "3.1.0"
    assert set(manifest["files"]) >= {"app/release.json", "bridge/manifest.json", "desktop/desktop_server.py"}
    # The installed layout is what the launcher expects.
    assert (out / "app/release.json").exists() and (out / "desktop").is_dir()

    again = build_desktop.build(out, root=root, version_override="3.1.0-ci.2")  # rebuilds over its own output
    assert again["version"] == "3.1.0-ci.2"
    assert json.loads((out / "app/release.json").read_text())["version"] == "3.1.0-ci.2"


def test_build_refuses_stale_or_missing_parts(tmp_path):
    sys.path.insert(0, str(DESKTOP.parent / "scripts"))
    import build_desktop

    root = fake_checkout(tmp_path)
    (root / "build2quote_bridge/dist/manifest.json").write_text(json.dumps({"version": "3.0.0"}))
    with pytest.raises(build_desktop.BuildError, match="Bridge is version 3.0.0"):
        build_desktop.build(tmp_path / "out", root=root)
    (root / "build2quote_bridge/dist/manifest.json").write_text(json.dumps({"version": "3.1.0"}))
    (root / "frontend/dist/assets/index-1.js").unlink()
    with pytest.raises(build_desktop.BuildError, match="assets/index-1.js"):
        build_desktop.build(tmp_path / "out", root=root)
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "notes.txt").write_text("keep")
    (root / "frontend/dist/assets/index-1.js").write_text("// ui")
    with pytest.raises(build_desktop.BuildError, match="not a previous desktop build"):
        build_desktop.build(precious, root=root)
    assert (precious / "notes.txt").read_text() == "keep"
