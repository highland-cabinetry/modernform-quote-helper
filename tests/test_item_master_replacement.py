from __future__ import annotations

import sqlite3
from io import BytesIO

import pytest
from app import main
from app.database import Repository
from app.models import ItemMaster, PackingSettings
from fastapi.testclient import TestClient
from openpyxl import Workbook


def item(sku: str) -> ItemMaster:
    return ItemMaster(sku=sku, length=10, width=8, height=4, weight=12)


def workbook_bytes(*, include_invalid: bool = False) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Item Master"
    sheet.append(["sku", "length", "width", "height", "weight", "description"])
    sheet.append(["PROD-1", 10, 8, 4, 12, "Production item"])
    if include_invalid:
        sheet.append(["BROKEN", "", "", "", "", "Incomplete item"])
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def xlsx_file(content: bytes) -> dict:
    return {
        "file": ("master.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    }


def seed_resettable_data(repository: Repository) -> None:
    repository.upsert_items([item("SAMPLE-1"), item("SAMPLE-2")])
    repository.save_settings(PackingSettings(max_pallets=7))
    with repository.connect() as connection:
        connection.execute(
            """
            INSERT INTO shipments(id, shipment_name, request_json, result_json, created_at)
            VALUES ('old', 'Old shipment', '{}', '{}', '2026-08-05T00:00:00Z')
            """
        )


def test_repository_replacement_backs_up_and_resets_everything(tmp_path):
    repository = Repository(tmp_path / "live.sqlite3", tmp_path / "backups")
    repository.initialize()
    seed_resettable_data(repository)

    result = repository.replace_all_data([item("PROD-1"), item("PROD-2")])

    assert result["inserted"] == 2
    assert result["items_removed"] == 2
    assert result["shipments_deleted"] == 1
    assert result["settings_reset"] == 1
    assert repository.database_counts() == {"active_items": 2, "all_items": 2, "shipments": 0, "settings": 0}
    assert [master.sku for master in repository.list_items()] == ["PROD-1", "PROD-2"]
    assert repository.get_settings().max_pallets == PackingSettings().max_pallets

    backup_path = tmp_path / "backups" / result["backup_name"]
    assert backup_path.exists()
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM item_master").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM shipments").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 1


def test_item_kind_migration_is_backed_up_and_preserves_shipments_and_settings(tmp_path):
    repository = Repository(tmp_path / "live.sqlite3", tmp_path / "backups")
    repository.initialize()
    accessory = ItemMaster(sku="TF3X96", length=96, width=3, height=0.75, weight=4, category="Accessory")
    repository.upsert_items([accessory])
    repository.save_settings(PackingSettings(max_pallets=9))
    with repository.connect() as connection:
        row = connection.execute("SELECT id, payload_json FROM item_master").fetchone()
        import json
        payload = json.loads(row["payload_json"])
        for field in ("item_kind", "bundle_approved"):
            payload.pop(field, None)
        payload["bundle_max_units"] = 1
        payload["packing_role"] = "standard"
        connection.execute("UPDATE item_master SET payload_json = ? WHERE id = ?", (json.dumps(payload), row["id"]))
        connection.execute(
            "INSERT INTO shipments(id, shipment_name, request_json, result_json, created_at) "
            "VALUES ('keep', 'Keep', '{}', '{}', '2026-08-26T00:00:00Z')"
        )

    result = repository.migrate_item_handling_metadata()

    migrated = repository.list_items()[0]
    assert migrated.item_kind.value == "accessory"
    assert migrated.bundle_approved is True
    assert migrated.bundle_max_units == 5
    assert migrated.packing_role.value == "top_accessory"
    assert repository.database_counts()["shipments"] == 1
    assert repository.get_settings().max_pallets == 9
    with sqlite3.connect(result["backup"]) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_replacement_rolls_back_if_insertion_fails(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "live.sqlite3", tmp_path / "backups")
    repository.initialize()
    seed_resettable_data(repository)
    original_insert = repository._insert_items

    def fail_after_first(connection, items):
        original_insert(connection, items[:1])
        raise RuntimeError("injected insert failure")

    monkeypatch.setattr(repository, "_insert_items", fail_after_first)
    with pytest.raises(RuntimeError, match="injected insert failure"):
        repository.replace_all_data([item("PROD-1"), item("PROD-2")])

    assert repository.database_counts() == {"active_items": 2, "all_items": 2, "shipments": 1, "settings": 1}
    assert [master.sku for master in repository.list_items()] == ["SAMPLE-1", "SAMPLE-2"]


def test_backup_retention_keeps_newest_ten(tmp_path):
    repository = Repository(tmp_path / "live.sqlite3", tmp_path / "backups")
    repository.backup_directory.mkdir(parents=True)
    for index in range(12):
        name = f"quote-helper-v2-before-master-replace-20260805T0000{index:02d}.sqlite3"
        (repository.backup_directory / name).write_bytes(b"backup")

    assert repository._prune_backups(10) == []
    remaining = sorted(repository.backup_directory.glob("*.sqlite3"))
    assert len(remaining) == 10
    assert remaining[0].name.endswith("02.sqlite3")
    assert remaining[-1].name.endswith("11.sqlite3")


def test_api_preview_and_confirmation_guard(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "api.sqlite3", tmp_path / "backups")
    repository.initialize()
    seed_resettable_data(repository)
    monkeypatch.setattr(main, "repository", repository)
    content = workbook_bytes()

    with TestClient(main.app) as client:
        preview = client.post(
            "/api/item-master/replace/preview",
            files=xlsx_file(content),
        )
        assert preview.status_code == 200
        assert preview.json()["incoming_rows"] == 1
        assert preview.json()["current_items_removed"] == 2
        assert preview.json()["shipments_deleted"] == 1
        assert preview.json()["can_replace"] is True

        rejected = client.post(
            "/api/item-master/replace",
            data={"confirmation": "replace"},
            files=xlsx_file(content),
        )
        assert rejected.status_code == 400
        assert repository.database_counts()["all_items"] == 2

        replaced = client.post(
            "/api/item-master/replace",
            data={"confirmation": "REPLACE"},
            files=xlsx_file(content),
        )
        assert replaced.status_code == 200
        assert replaced.json()["inserted"] == 1
        assert replaced.json()["backup_name"].endswith(".sqlite3")
        assert repository.database_counts() == {"active_items": 1, "all_items": 1, "shipments": 0, "settings": 0}


def test_api_blocks_workbook_with_skipped_rows(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "api.sqlite3", tmp_path / "backups")
    repository.initialize()
    repository.upsert_items([item("ORIGINAL")])
    monkeypatch.setattr(main, "repository", repository)
    content = workbook_bytes(include_invalid=True)

    with TestClient(main.app) as client:
        preview = client.post(
            "/api/item-master/replace/preview",
            files=xlsx_file(content),
        )
        assert preview.status_code == 200
        assert preview.json()["skipped_count"] == 1
        assert preview.json()["can_replace"] is False

        response = client.post(
            "/api/item-master/replace",
            data={"confirmation": "REPLACE"},
            files=xlsx_file(content),
        )
        assert response.status_code == 422
        assert [master.sku for master in repository.list_items()] == ["ORIGINAL"]


def test_empty_startup_does_not_seed_sample_items(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "empty.sqlite3", tmp_path / "backups")
    monkeypatch.setattr(main, "repository", repository)
    with TestClient(main.app) as client:
        assert client.get("/api/health").json()["item_master_count"] == 0
    assert repository.item_count() == 0
