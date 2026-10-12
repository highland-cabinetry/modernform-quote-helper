"""Updates never lose data: a verified backup before a new version first opens the database, and
no version opens a database that a newer one changed in a way it cannot read."""

from __future__ import annotations

import sqlite3

import pytest
from app import database
from app.database import DATABASE_COMPATIBILITY, UPDATE_BACKUP_PREFIX, DatabaseTooNewError, Repository
from app.release import RELEASE


def meta(path) -> dict[str, str]:
    with sqlite3.connect(path) as connection:
        return dict(connection.execute("SELECT key, value FROM app_meta").fetchall())


def versions(path) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        query = "SELECT version, previous_version, backup_path FROM app_versions ORDER BY id"
        return connection.execute(query).fetchall()


def update_backups(repo: Repository) -> list:
    return sorted(repo.backup_directory.glob(f"{UPDATE_BACKUP_PREFIX}*.sqlite3"))


def test_a_new_database_records_its_version_without_a_backup(tmp_path):
    repo = Repository(tmp_path / "new.sqlite3")
    repo.initialize()
    assert meta(repo.database_path) == {"last_app_version": RELEASE["version"],
                                        "database_compatibility": str(DATABASE_COMPATIBILITY)}
    assert update_backups(repo) == []
    repo.initialize()  # reopening with the same version changes nothing
    assert len(versions(repo.database_path)) == 1 and update_backups(repo) == []


def test_a_new_version_backs_up_the_database_exactly_as_the_old_one_left_it(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "live.sqlite3")
    monkeypatch.setitem(RELEASE, "version", "3.0.0")
    repo.initialize()
    repo.save_settings(repo.get_settings().model_copy(update={"truckload_standard_max_weight": 2222}))
    monkeypatch.setitem(RELEASE, "version", "3.1.0")
    repo.initialize()
    [backup] = update_backups(repo)
    assert backup.name.startswith(f"{UPDATE_BACKUP_PREFIX}3.1.0-")
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert dict(connection.execute("SELECT key, value FROM app_meta"))["last_app_version"] == "3.0.0"
    assert versions(repo.database_path)[-1][:2] == ("3.1.0", "3.0.0")
    assert repo.get_settings().truckload_standard_max_weight == 2222  # the data itself is untouched


def test_a_pre_3_1_database_without_version_records_is_backed_up(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT)")
        connection.execute("INSERT INTO settings VALUES ('marker', '1', 'then')")
    repo = Repository(path)
    repo.initialize()
    assert len(update_backups(repo)) == 1
    assert versions(path)[-1][1] is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value_json FROM settings WHERE key='marker'").fetchone()[0] == "1"


def test_a_database_changed_by_a_newer_incompatible_version_is_refused_untouched(tmp_path):
    repo = Repository(tmp_path / "newer.sqlite3")
    repo.initialize()
    with sqlite3.connect(repo.database_path) as connection:
        connection.execute("UPDATE app_meta SET value='9.0.0' WHERE key='last_app_version'")
        connection.execute("UPDATE app_meta SET value=? WHERE key='database_compatibility'",
                           (str(DATABASE_COMPATIBILITY + 1),))
    before = repo.database_path.read_bytes()
    with pytest.raises(DatabaseTooNewError, match="9.0.0"):
        repo.initialize()
    assert repo.database_path.read_bytes() == before and update_backups(repo) == []


def test_an_older_compatible_version_can_reopen_after_a_rollback(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "rollback.sqlite3")
    monkeypatch.setitem(RELEASE, "version", "3.2.0")
    repo.initialize()
    monkeypatch.setitem(RELEASE, "version", "3.1.0")
    repo.initialize()  # same compatibility level: allowed, and backed up first
    assert meta(repo.database_path)["last_app_version"] == "3.1.0"
    assert len(update_backups(repo)) == 1


def test_only_the_newest_update_backups_are_kept(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "many.sqlite3")
    repo.initialize()
    repo.backup_directory.mkdir(parents=True, exist_ok=True)
    for index in range(database.UPDATE_BACKUPS_KEPT + 3):
        old = repo.backup_directory / f"{UPDATE_BACKUP_PREFIX}old-{index:02d}.sqlite3"
        old.write_bytes(b"x")
        stamp = 1_000_000 + index
        import os
        os.utime(old, (stamp, stamp))
    unrelated = repo.backup_directory / "quote-helper-v2-before-freight-domain-1.sqlite3"
    unrelated.write_bytes(b"keep")
    monkeypatch.setitem(RELEASE, "version", "9.9.9")
    repo.initialize()
    kept = update_backups(repo)
    assert len(kept) == database.UPDATE_BACKUPS_KEPT
    assert any("9.9.9" in path.name for path in kept)
    assert not (repo.backup_directory / f"{UPDATE_BACKUP_PREFIX}old-00.sqlite3").exists()
    assert unrelated.exists()


def test_stored_records_written_by_a_newer_version_still_load():
    """Rollback safety: stored records ignore fields a newer version added; API inputs stay strict."""
    from app.freight_models import (
        Company,
        FreightShipment,
        FreightShipmentInput,
        Order,
        Quote,
        ShipmentExpense,
    )
    from app.models import ItemMaster
    from pydantic import ValidationError

    future = {"added_in_a_later_version": {"anything": 1}}
    Company.model_validate({"code": "HCI", "name": "Highland", "config": {"future_setting": 1}, **future})
    Order.model_validate({"order_number": "SO1", "company_id": "c", "lines": [
        {"original_sku": "B30", "quantity": 1, "future_line_field": True}], **future})
    Quote.model_validate({"company_id": "c", "kind": "packed", "cost": {"amount_cents": 1, "future": 2}, **future})
    FreightShipment.model_validate({"company_id": "c", **future})
    ShipmentExpense.model_validate({"amount_cents": 5, "shipment_id": "s", **future})
    ItemMaster.model_validate({"sku": "B30", "weight": 40, "flatpack_length": 30, "flatpack_width": 24,
                               "flatpack_height": 6, **future})
    with pytest.raises(ValidationError):
        FreightShipmentInput.model_validate({"typo_field": 1})
