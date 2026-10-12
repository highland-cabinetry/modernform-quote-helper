import os
from pathlib import Path

import app
from app.services.packing_jobs import worker_environment


def _app_parent() -> str:
    return str(Path(app.__file__).resolve().parents[1])


def test_worker_environment_puts_app_package_dir_first_and_keeps_existing_pythonpath(monkeypatch, tmp_path):
    existing = os.pathsep.join([str(tmp_path / "one"), str(tmp_path / "two")])
    monkeypatch.setenv("PYTHONPATH", existing)

    environment = worker_environment()

    assert environment["PYTHONPATH"] == os.pathsep.join([_app_parent(), existing])
    parts = environment["PYTHONPATH"].split(os.pathsep)
    assert parts[0] == _app_parent()
    assert (Path(parts[0]) / "app" / "__init__.py").resolve() == Path(app.__file__).resolve()
    assert parts[1:] == existing.split(os.pathsep)


def test_worker_environment_without_existing_pythonpath(monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)

    environment = worker_environment()

    assert environment["PYTHONPATH"] == _app_parent()
