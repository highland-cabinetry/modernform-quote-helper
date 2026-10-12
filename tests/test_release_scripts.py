"""Delivery tooling: scripts/backup_database.py, scripts/cut_release.py and scripts/switch_release.py.

Every test works on a tiny fake project tree under tmp_path (via the scripts' --root option);
nothing here reads or writes the real data/, releases/ or deployment.json.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT / "scripts"
VERSION = "9.9.0"


@pytest.fixture
def scripts(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    modules = {}
    for name in ("backup_database", "cut_release", "switch_release"):
        modules[name] = importlib.import_module(name)
    return modules


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _age(root: Path, seconds: float = 3600) -> None:
    """Make every file in the tree older, so dists built 'later' are not reported as stale."""

    old = time.time() - seconds
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (old, old))


def set_version(root: Path, version: str) -> None:
    _write(root / "release.json", json.dumps({"version": version, "minimum_bridge_version": "2.8.0"}))
    _write(root / "frontend/package.json", json.dumps({"name": "fe", "version": version}))
    _write(root / "build2quote_bridge/package.json", json.dumps({"name": "bridge", "version": version}))
    _write(root / "build2quote_bridge/manifest.json", json.dumps({"manifest_version": 3, "version": version}))
    _write(root / "pyproject.toml", f'[project]\nname = "fake"\nversion = "{version}"\n')
    _write(root / "build2quote_bridge/dist/manifest.json", json.dumps({"manifest_version": 3, "version": version}))


def make_database(path: Path, rows: int = 3) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, sku TEXT NOT NULL)")
    connection.executemany("INSERT INTO items (sku) VALUES (?)", [(f"SKU{i}",) for i in range(rows)])
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def project(tmp_path) -> Path:
    root = tmp_path / "project"
    _write(root / "backend/app/__init__.py", "")
    _write(root / "backend/app/main.py", "app = None\n")
    _write(root / "backend/app/services/reports.py", "VALUE = 1\n")
    _write(root / "backend/app/__pycache__/main.cpython-312.pyc", "compiled")
    _write(root / "frontend/src/main.jsx", "export default 1;\n")
    _write(root / "frontend/index.html", '<script type="module" src="/src/main.jsx"></script>')
    _write(root / "build2quote_bridge/src/background.ts", "export {};\n")
    set_version(root, VERSION)
    _age(root)
    # Built after the sources were last changed.
    _write(root / "frontend/dist/index.html",
           '<link rel="stylesheet" href="/assets/main.css"><script type="module" src="/assets/main.js"></script>')
    _write(root / "frontend/dist/assets/main.js", "console.log('quote helper');\n")
    _write(root / "frontend/dist/assets/main.css", "body{}\n")
    _write(root / "build2quote_bridge/dist/background.js", "void 0;\n")
    _write(root / "build2quote_bridge/dist/manifest.json", json.dumps({"manifest_version": 3, "version": VERSION}))
    make_database(root / "data/quote_helper_v2.sqlite3")
    return root


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[tuple]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT id, sku FROM items ORDER BY id").fetchall()
    finally:
        connection.close()


# --------------------------------------------------------------------------- backup_database.py


def test_backup_copies_live_wal_data_verifies_and_leaves_source_untouched(project, scripts, capsys):
    database = project / "data/quote_helper_v2.sqlite3"
    # A "running app": committed rows that still sit in the WAL, with the connection held open.
    live = sqlite3.connect(database)
    live.execute("PRAGMA wal_autocheckpoint = 0")
    live.execute("INSERT INTO items (sku) VALUES ('IN-WAL')")
    live.commit()
    try:
        before = sha(database)
        assert scripts["backup_database"].main(["--root", str(project)]) == 0
        created = Path(capsys.readouterr().out.strip())
        assert sha(database) == before
    finally:
        live.close()

    assert created.parent == (project / "data/backups").resolve()
    assert created.name.startswith("quote-helper-v2-manual-") and created.name.endswith("Z.sqlite3")
    assert [sku for _, sku in rows(created)] == ["SKU0", "SKU1", "SKU2", "IN-WAL"]
    connection = sqlite3.connect(created)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        connection.close()
    assert not list(created.parent.glob("*.partial*"))


def test_backup_refuses_to_overwrite_and_reports_missing_database(project, scripts, capsys, tmp_path):
    existing = _write(tmp_path / "copy.sqlite3", "keep me")
    backup = scripts["backup_database"]
    assert backup.main(["--root", str(project), "--output", str(existing)]) == 1
    assert existing.read_text(encoding="utf-8") == "keep me"
    assert "Refusing to overwrite" in capsys.readouterr().err

    assert backup.main(["--database", str(tmp_path / "absent.sqlite3"), "--output-dir", str(tmp_path / "out")]) == 1
    assert "Database not found" in capsys.readouterr().err
    assert not (tmp_path / "absent.sqlite3").exists()
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.sqlite3"))

    database = project / "data/quote_helper_v2.sqlite3"
    assert backup.main(["--database", str(database), "--output", str(database)]) == 1
    assert "must not be written over" in capsys.readouterr().err


def test_backup_output_file_and_keep_prunes_only_manual_backups(project, scripts, capsys, tmp_path):
    backup = scripts["backup_database"]
    target = tmp_path / "preview/quote_helper_v2-preview.sqlite3"
    assert backup.main(["--root", str(project), "--output", str(target)]) == 0
    assert Path(capsys.readouterr().out.strip()) == target.resolve()
    assert len(rows(target)) == 3

    folder = tmp_path / "backups"
    old_manual = [_write(folder / f"quote-helper-v2-manual-2026010{day}T000000Z.sqlite3", "old") for day in (1, 2, 3)]
    automatic = _write(folder / "quote_helper_v2-before-replace-20260101.sqlite3", "automatic")
    unrelated = _write(folder / "notes.txt", "mine")
    assert backup.main(["--root", str(project), "--output-dir", str(folder), "--keep", "2"]) == 0
    created = Path(capsys.readouterr().out.strip())
    remaining = sorted(path.name for path in folder.glob("quote-helper-v2-manual-*.sqlite3"))
    assert remaining == sorted([old_manual[2].name, created.name])
    assert automatic.exists() and unrelated.exists()

    assert backup.main(["--root", str(project), "--output-dir", str(folder), "--keep", "0"]) == 1


def test_backup_runs_as_a_script(project, tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "backup_database.py"), "--root", str(project),
         "--output-dir", str(tmp_path / "cli")],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).is_file()


# --------------------------------------------------------------------------- cut_release.py


def test_cut_release_copies_runtime_files_and_writes_manifest(project, scripts, capsys):
    cut = scripts["cut_release"]
    assert cut.main(["--root", str(project)]) == 0
    output = capsys.readouterr()
    assert "deployment.json was not changed" in output.out
    assert "Warning" not in output.err
    release = project / "releases" / VERSION
    manifest = json.loads((release / "build-manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION
    assert manifest["created_at"].endswith("Z")
    assert "git_commit" in manifest
    assert set(manifest["files"]) == {
        "backend/app/__init__.py", "backend/app/main.py", "backend/app/services/reports.py",
        "frontend/dist/index.html", "frontend/dist/assets/main.js", "frontend/dist/assets/main.css",
        "build2quote_bridge/dist/background.js", "build2quote_bridge/dist/manifest.json", "release.json",
    }
    assert manifest["files"]["backend/app/main.py"] == sha(project / "backend/app/main.py")
    assert not (release / "data").exists()
    assert not (project / "deployment.json").exists()
    assert not list((project / "releases").glob(".*partial"))

    assert cut.main(["--root", str(project), "--verify", VERSION]) == 0
    assert "9 files match" in capsys.readouterr().out
    # A running release writes __pycache__ into its backend; verification ignores it.
    _write(release / "backend/app/__pycache__/main.cpython-312.pyc", "runtime")
    assert cut.verify_release(project, VERSION) == []


def test_cut_release_refuses_existing_release_and_missing_or_mismatched_dists(project, scripts, capsys):
    cut = scripts["cut_release"]
    _write(project / "releases" / VERSION / "keep.txt", "existing")
    assert cut.main(["--root", str(project)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert sorted(path.name for path in (project / "releases" / VERSION).iterdir()) == ["keep.txt"]
    (project / "releases" / VERSION / "keep.txt").unlink()
    (project / "releases" / VERSION).rmdir()

    (project / "frontend/dist/assets/main.js").unlink()
    assert cut.main(["--root", str(project)]) == 1
    assert "assets/main.js, which is not in the dist" in capsys.readouterr().err

    for path in sorted((project / "frontend/dist").rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    (project / "frontend/dist").rmdir()
    _write(project / "build2quote_bridge/dist/manifest.json", json.dumps({"version": "9.8.0"}))
    assert cut.main(["--root", str(project)]) == 1
    error = capsys.readouterr().err
    assert "frontend/dist/index.html is missing" in error
    assert "The built Bridge is version 9.8.0" in error
    assert not (project / "releases" / VERSION).exists()


def test_cut_release_refuses_version_drift_and_warns_on_stale_dist(project, scripts, capsys):
    cut = scripts["cut_release"]
    _write(project / "frontend/package.json", json.dumps({"name": "fe", "version": "9.8.0"}))
    assert cut.main(["--root", str(project)]) == 1
    assert "frontend/package.json says version 9.8.0" in capsys.readouterr().err

    _write(project / "frontend/package.json", json.dumps({"name": "fe", "version": VERSION}))
    old = time.time() - 7200
    os.utime(project / "frontend/package.json", (old, old))
    future = time.time() + 600
    os.utime(_write(project / "frontend/src/new_page.jsx", "export {};\n"), (future, future))
    assert cut.main(["--root", str(project)]) == 0
    assert "frontend/dist is older than frontend/src/new_page.jsx" in capsys.readouterr().err
    assert (project / "releases" / VERSION / "build-manifest.json").is_file()


def test_verify_detects_changed_missing_and_extra_files(project, scripts, capsys):
    cut = scripts["cut_release"]
    assert cut.main(["--root", str(project)]) == 0
    release = project / "releases" / VERSION
    _write(release / "backend/app/main.py", "app = 'tampered'\n")
    (release / "frontend/dist/assets/main.css").unlink()
    _write(release / "backend/app/extra.py", "x = 1\n")
    capsys.readouterr()
    assert cut.main(["--root", str(project), "--verify", VERSION]) == 1
    error = capsys.readouterr().err
    assert "changed: backend/app/main.py" in error
    assert "missing: frontend/dist/assets/main.css" in error
    assert "not in manifest: backend/app/extra.py" in error
    assert cut.main(["--root", str(project), "--verify", "0.0.1"]) == 1
    assert cut.main(["--root", str(project), "--verify", "../data"]) == 1


# --------------------------------------------------------------------------- switch_release.py


def two_releases(project: Path, cut) -> None:
    assert cut.main(["--root", str(project)]) == 0
    set_version(project, "9.9.1")
    assert cut.main(["--root", str(project)]) == 0
    _write(project / "deployment.json", json.dumps({"release_path": f"releases/{VERSION}"}, indent=2) + "\n")


def selection(project: Path, name: str = "deployment.json") -> str:
    return json.loads((project / name).read_text(encoding="utf-8"))["release_path"]


def test_switch_writes_deployment_and_previous_and_rolls_back(project, scripts, capsys):
    switch = scripts["switch_release"]
    two_releases(project, scripts["cut_release"])
    capsys.readouterr()

    assert switch.main(["--root", str(project), "--to", "9.9.1", "--force"]) == 0
    output = capsys.readouterr().out
    assert selection(project) == "releases/9.9.1"
    assert selection(project, "deployment.previous.json") == f"releases/{VERSION}"
    assert "--rollback" in output and f"--to {VERSION}" in output
    assert not list(project.glob("deployment.json.*.tmp"))

    assert switch.main(["--root", str(project), "--to", "9.9.1", "--force"]) == 0
    assert "already selected" in capsys.readouterr().out
    assert selection(project, "deployment.previous.json") == f"releases/{VERSION}"

    assert switch.main(["--root", str(project), "--rollback", "--force"]) == 0
    assert selection(project) == f"releases/{VERSION}"
    assert selection(project, "deployment.previous.json") == "releases/9.9.1"

    assert switch.main(["--root", str(project), "--status"]) == 0
    status = capsys.readouterr().out
    assert f"Current:  releases/{VERSION}" in status and "Previous: releases/9.9.1" in status
    assert f" * {VERSION}" in status


def test_switch_refuses_missing_or_tampered_release(project, scripts, capsys):
    switch = scripts["switch_release"]
    two_releases(project, scripts["cut_release"])
    before = (project / "deployment.json").read_bytes()
    capsys.readouterr()

    assert switch.main(["--root", str(project), "--to", "3.0.0", "--force"]) == 1
    assert "releases/3.0.0 does not exist" in capsys.readouterr().err
    _write(project / "releases/9.9.1/frontend/dist/assets/main.js", "tampered")
    assert switch.main(["--root", str(project), "--to", "9.9.1", "--force"]) == 1
    assert "changed: frontend/dist/assets/main.js" in capsys.readouterr().err
    assert (project / "deployment.json").read_bytes() == before
    assert not (project / "deployment.previous.json").exists()


def test_switch_refuses_while_quote_helper_answers(project, scripts, capsys, monkeypatch):
    switch = scripts["switch_release"]
    two_releases(project, scripts["cut_release"])
    before = (project / "deployment.json").read_bytes()
    monkeypatch.setattr(switch, "running_servers", lambda: [8000])
    capsys.readouterr()
    assert switch.main(["--root", str(project), "--to", "9.9.1"]) == 2
    assert "stop Quote Helper first" in capsys.readouterr().err
    assert (project / "deployment.json").read_bytes() == before
    assert switch.main(["--root", str(project), "--to", "9.9.1", "--force"]) == 0
    assert selection(project) == "releases/9.9.1"


def test_running_servers_detects_a_health_endpoint(scripts):
    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            status = 200 if self.path == "/api/health" else 404
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        assert scripts["switch_release"].running_servers(ports=[port], timeout=1) == [port]
    finally:
        server.shutdown()
        server.server_close()
    assert scripts["switch_release"].running_servers(ports=[port], timeout=0.5) == []
