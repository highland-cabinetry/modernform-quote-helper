from __future__ import annotations

import sqlite3

import pytest
from app import main
from app.database import AliasConflictError, Repository
from app.models import ItemMaster, PackingSettings, PackingSlipLine
from app.services.sku_matching import match_lines
from fastapi.testclient import TestClient

PREFIXES = PackingSettings().sku_prefixes_to_ignore


def item(sku: str) -> ItemMaster:
    return ItemMaster(sku=sku, description=f"{sku} cabinet", length=30, width=24, height=6, weight=50)


def test_confirmed_alias_matches_after_exact_and_before_fuzzy():
    master = item("PC249024BOX")
    master.id = 7
    line = PackingSlipLine(original_sku="SBS-PC249024B...", quantity=2)

    matches = match_lines(
        [line],
        [master],
        ignored_prefixes=PREFIXES,
        fuzzy_threshold=88,
        confirmed_aliases={"PC249024B": master},
    )

    assert matches[0].match_status.value == "confirmed_alias"
    assert matches[0].matched_item.sku == "PC249024BOX"
    assert matches[0].line.original_sku == "SBS-PC249024B..."
    assert matches[0].confidence == 100


def test_repository_alias_crud_normalizes_color_prefixes_and_rejects_collisions(tmp_path):
    repository = Repository(tmp_path / "aliases.sqlite3")
    repository.initialize()
    pantry, base = item("PC249024BOX"), item("B30")
    repository.upsert_items([pantry, base])

    saved = repository.save_sku_alias(
        "SBS-PC249024B...", pantry.id, ignored_prefixes=PREFIXES
    )

    assert saved.normalized_alias == "PC249024B"
    assert saved.target_sku == "PC249024BOX"
    assert repository.alias_targets()["PC249024B"].sku == "PC249024BOX"
    assert len(repository.list_sku_aliases(search="2490")) == 1
    with pytest.raises(AliasConflictError, match="already assigned"):
        repository.save_sku_alias("WHS-PC249024B...", base.id, ignored_prefixes=PREFIXES)
    with pytest.raises(AliasConflictError, match="item master"):
        repository.save_sku_alias("WHS-B30", base.id, ignored_prefixes=PREFIXES)
    assert repository.delete_sku_alias(saved.id) is True
    assert repository.list_sku_aliases() == []


def test_alias_api_creates_lists_and_deletes_mapping(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "api.sqlite3")
    repository.initialize()
    pantry = item("PC249024BOX")
    repository.upsert_items([pantry])
    monkeypatch.setattr(main, "repository", repository)

    with TestClient(main.app) as client:
        created = client.post(
            "/api/sku-aliases",
            json={"alias_sku": "SBS-PC249024B...", "target_item_id": pantry.id},
        )
        assert created.status_code == 200
        alias_id = created.json()["id"]
        assert client.get("/api/sku-aliases").json()[0]["target_sku"] == "PC249024BOX"
        assert client.delete(f"/api/sku-aliases/{alias_id}").json() == {"deleted": True}


def test_import_api_returns_accepted_and_ignored_row_report(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "import.sqlite3")
    repository.initialize()
    monkeypatch.setattr(main, "repository", repository)
    content = (
        b"Item,Quantity,Description\n"
        b"SBS,,Collection heading\n"
        b"SBS-B09,2,Base cabinet\n"
        b"Freight(Shaker),,Freight terms\n"
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/import/packing-slips",
            files={"files": ("order.csv", content, "text/csv")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted_count"] == 1
    assert payload["ignored_count"] == 2
    assert payload["file_summaries"] == [
        {"file": "order.csv", "accepted_count": 1, "ignored_count": 2}
    ]
    assert payload["lines"][0]["length"] is None
    assert [row["original_sku"] for row in payload["ignored_rows"]] == ["SBS", "Freight(Shaker)"]


def test_replacement_preserves_valid_aliases_and_removes_missing_targets(tmp_path):
    repository = Repository(tmp_path / "replace.sqlite3", tmp_path / "backups")
    repository.initialize()
    keep, remove = item("KEEP"), item("REMOVE")
    repository.upsert_items([keep, remove])
    repository.save_sku_alias("OLD-KEEP...", keep.id, ignored_prefixes=[])
    repository.save_sku_alias("OLD-REMOVE...", remove.id, ignored_prefixes=[])

    preview = repository.preview_item_master_replacement([item("KEEP"), item("NEW")])
    result = repository.replace_all_data([item("KEEP"), item("NEW")])

    assert preview["aliases_preserved"] == 1
    assert preview["aliases_removed"] == 1
    assert result["aliases_preserved"] == 1
    assert result["aliases_removed"] == 1
    assert [(alias.alias_sku, alias.target_sku) for alias in repository.list_sku_aliases()] == [
        ("OLD-KEEP...", "KEEP")
    ]
    with repository.connect() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(tmp_path / "backups" / result["backup_name"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sku_aliases").fetchone()[0] == 2


def test_replacement_rollback_keeps_aliases(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "rollback.sqlite3", tmp_path / "backups")
    repository.initialize()
    original = item("ORIGINAL")
    repository.upsert_items([original])
    repository.save_sku_alias("ORIG...", original.id, ignored_prefixes=[])

    def fail_insert(_connection, _items):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(repository, "_insert_items", fail_insert)
    with pytest.raises(RuntimeError, match="injected failure"):
        repository.replace_all_data([item("NEW")])

    assert [(alias.alias_sku, alias.target_sku) for alias in repository.list_sku_aliases()] == [
        ("ORIG...", "ORIGINAL")
    ]
