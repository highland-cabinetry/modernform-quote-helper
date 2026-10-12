"""Durability and migration safety of the freight domain on the shared SQLite database."""

from __future__ import annotations

import json
import sqlite3

import pytest
from app import main
from app.database import Repository
from app.freight_repository import FREIGHT_SCHEMA_VERSION
from app.models import ItemMaster, PackingRequest, PackingSettings, PackingSlipLine
from app.services.freight import legacy_backfill
from app.services.shipments import build_shipment
from app.workflow_models import ManualQuoteInput
from fastapi.testclient import TestClient
from fixtures_freight import HighlandDoc, catalog_item, highland_pdf, make_order, make_repo, pack, upload

LEGACY_TABLES = ("item_master", "shipments", "sku_aliases", "settings", "manual_quotes", "manual_quote_revisions")
NEW_TABLES = ("companies", "company_settings", "catalog_items", "catalog_aliases", "import_batches", "source_files",
              "orders", "order_references", "order_revisions", "quotes", "quote_orders", "freight_shipments",
              "shipment_orders", "shipment_expenses", "expense_allocations", "audit_log", "freight_counters")


def client_for(repo, monkeypatch) -> TestClient:
    monkeypatch.setattr(main, "repository", repo)
    return TestClient(main.app)


def table_dump(path, tables=LEGACY_TABLES) -> dict:
    connection = sqlite3.connect(path)
    try:
        return {
            table: {
                "sql": connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0],
                "rows": connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall(),
            }
            for table in tables
        }
    finally:
        connection.close()


def backups(repo, label: str) -> list:
    return sorted(repo.backup_directory.glob(f"quote-helper-v2-{label}-*.sqlite3"))


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """A database as the released 2.10.0 build leaves it: item master, plans, aliases, settings, measured quotes."""

    original = Repository.initialize_freight
    monkeypatch.setattr(Repository, "initialize_freight", lambda self: {"migrated": False})
    legacy = Repository(tmp_path / "legacy.sqlite3", tmp_path / "backups")
    legacy.initialize()
    monkeypatch.setattr(Repository, "initialize_freight", original)

    legacy.upsert_items([catalog_item("B30"), catalog_item("W3030", weight=25)])
    legacy.save_settings(PackingSettings(fuzzy_match_threshold=91))
    legacy.save_sku_alias("OLD-B30", legacy.list_items()[0].id, ignored_prefixes=[])
    donor = make_repo(tmp_path / "donor")
    request = PackingRequest(shipment_name="SO77154 + 68606", save_result=False,
                             lines=[PackingSlipLine(source_ref="old.pdf", original_sku="B30", quantity=2)])
    result = build_shipment(request, donor)
    legacy_request = request.model_dump(mode="json", exclude={"company_id", "order_ids", "freight_shipment_id"})
    legacy.save_shipment(legacy_request, result)
    legacy.save_manual_quote(ManualQuoteInput(
        shipment_name="66557", pallets=[{"length": 48, "width": 40, "height": 50, "weight": 500}], shipping_info={}))
    with sqlite3.connect(legacy.database_path) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not names & set(NEW_TABLES)
    return legacy, result


# --------------------------------------------------------------------------- restart durability


def test_orders_quotes_and_shipments_survive_an_app_restart(tmp_path, monkeypatch):
    path = tmp_path / "restart.sqlite3"
    first_repo = make_repo(tmp_path, "restart.sqlite3")
    with client_for(first_repo, monkeypatch) as api:
        imported = upload(api, ("66800.pdf", highland_pdf(HighlandDoc(invoice="66800",
                                                                       items=[("2", "B30", "Base", "2")]))))
        [order_id] = imported["created_order_ids"]
        other = make_order(api, "SO88001", ("W3030", 1))
        _, quote = pack(api, [order_id, other["id"]])
        shipment = api.post("/api/freight-shipments", json={"order_ids": [order_id, other["id"]]}).json()
        api.post(f"/api/freight-shipments/{shipment['id']}/expenses", json={"amount_cents": 77777})
        api.post(f"/api/freight-shipments/{shipment['id']}/allocation", json={"method": "weight"})
        before = {
            "orders": api.get("/api/orders").json(),
            "quote": api.get(f"/api/quotes/{quote['id']}").json(),
            "shipment": api.get(f"/api/freight-shipments/{shipment['id']}").json(),
            "trace": api.get(f"/api/orders/{order_id}").json(),
        }

    second_repo = Repository(path, tmp_path / "backups")  # a new process: nothing shared in memory
    with client_for(second_repo, monkeypatch) as api:
        assert api.get("/api/orders").json() == before["orders"]
        assert api.get(f"/api/quotes/{quote['id']}").json() == before["quote"]
        assert api.get(f"/api/freight-shipments/{shipment['id']}").json() == before["shipment"]
        assert api.get(f"/api/orders/{order_id}").json() == before["trace"]
        assert api.get(f"/api/source-files/{before['trace']['source_file']['id']}").status_code == 200
        # Numbering continues after a restart instead of reusing numbers.
        _, later = pack(api, [other["id"]])
        assert later["number"] == "Q-000002"
        assert api.post("/api/freight-shipments", json={"order_ids": [make_order(api, "SO88002", ("B18", 1))["id"]]}
                        ).json()["number"] == "S-000002"
        assert len(api.get("/api/quotes").json()) == 2  # the startup backfill did not duplicate history


# --------------------------------------------------------------------------- migration


def test_migration_writes_one_verified_backup_and_is_idempotent(tmp_path):
    repo = make_repo(tmp_path, seed=False)
    [backup] = backups(repo, "before-freight-domain")
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        backup_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "item_master" in backup_tables and "companies" not in backup_tables  # taken before the migration
    with repo.connect() as connection:
        rows = connection.execute("SELECT version, backup_path FROM freight_schema").fetchall()
    assert [tuple(row) for row in rows] == [(FREIGHT_SCHEMA_VERSION, str(backup))]

    assert repo.initialize_freight() == {"migrated": False, "version": FREIGHT_SCHEMA_VERSION}
    Repository(repo.database_path, repo.backup_directory).initialize()
    assert backups(repo, "before-freight-domain") == [backup]
    assert len(backups(repo, "before-workflows")) == 1
    assert [c.code for c in repo.list_companies()] == ["HCI"]


def test_migration_leaves_legacy_tables_untouched_and_backfills_history(legacy_db):
    legacy, result = legacy_db
    before = table_dump(legacy.database_path)
    migrated = Repository(legacy.database_path, legacy.backup_directory)
    migrated.initialize()
    assert table_dump(legacy.database_path) == before

    [backup] = backups(migrated, "before-freight-domain")
    assert table_dump(backup) == before
    # The default company inherits the 2.x quote origin and settings stay where 2.x reads them.
    assert migrated.default_company().config.rule_set == "highland"
    assert migrated.company_settings(migrated.default_company()).fuzzy_match_threshold == 91

    assert legacy_backfill(migrated) == {"created": 2}
    assert legacy_backfill(migrated) == {"created": 0}
    packed = migrated.quote_for_source(packing_result_id=result.id)
    assert packed.legacy and sorted(migrated.order_numbers(packed.order_ids).values()) == ["INV68606", "SO77154"]
    assert packed.summary.item_weight == 80 and packed.summary.pallets == len(result.pallets)
    assert table_dump(legacy.database_path) == before  # backfill writes only new tables


def test_new_tables_never_reference_legacy_tables(freight_repo):
    with freight_repo.connect() as connection:
        for table in NEW_TABLES:
            targets = {row["table"] for row in connection.execute(f"PRAGMA foreign_key_list({table})")}
            assert not targets & {"shipments", "item_master", "settings", "sku_aliases"}, table


def test_migrated_database_still_works_with_the_legacy_repository_methods(legacy_db, monkeypatch):
    legacy, result = legacy_db
    repo = Repository(legacy.database_path, legacy.backup_directory)
    repo.initialize()
    # New code writing through the catalog keeps 2.x-compatible item payloads (no unknown fields).
    repo.catalog_upsert("default", [catalog_item("B18"), catalog_item("B30", weight=41)])
    repo.catalog_deactivate("default", repo.catalog_items()[0].id)
    with sqlite3.connect(repo.database_path) as connection:
        for (payload,) in connection.execute("SELECT payload_json FROM item_master"):
            assert set(json.loads(payload)) <= set(ItemMaster.model_fields)
            ItemMaster(**json.loads(payload))

    assert {item.sku for item in repo.list_items()} == {"B30", "W3030"}  # B18 was deactivated
    assert {item.sku for item in repo.list_items(active_only=False)} == {"B18", "B30", "W3030"}
    assert repo.list_items(search="W3030")[0].weight == 25
    assert [row["id"] for row in repo.list_shipments()] == [result.id]
    assert repo.get_shipment(result.id).summary.total_items == 2
    assert repo.get_settings().fuzzy_match_threshold == 91
    assert [alias.alias_sku for alias in repo.list_sku_aliases()] == ["OLD-B30"]
    assert repo.database_counts()["shipments"] == 1


def test_replace_all_data_keeps_orders_quotes_and_shipments(legacy_db, monkeypatch):
    legacy, _ = legacy_db
    repo = Repository(legacy.database_path, legacy.backup_directory)
    with client_for(repo, monkeypatch) as api:  # startup migrates and backfills
        backfilled = api.get("/api/quotes").json()
        assert len(backfilled) == 2
        order = make_order(api, "SO88100", ("B30", 1), ("W3030", 1))
        _, quote = pack(api, [order["id"]])
        shipment = api.post("/api/freight-shipments", json={"order_ids": [order["id"]]}).json()
        api.post(f"/api/freight-shipments/{shipment['id']}/expenses", json={"amount_cents": 12345})
        assert api.get(f"/api/quotes/{quote['id']}").json()["source_available"] is True

        replaced = repo.replace_all_data([catalog_item("B30", weight=50)])
        assert replaced["shipments_deleted"] == 2  # the legacy plan and the new one

        view = api.get(f"/api/quotes/{quote['id']}").json()
        assert view["source_available"] is False
        assert view["summary"] == quote["summary"]  # the quote's own snapshot survives
        assert view["order_ids"] == [order["id"]]
        assert len(api.get("/api/quotes").json()) == 3
        kept = api.get(f"/api/freight-shipments/{shipment['id']}").json()
        assert kept["expense_total_cents"] == 12345 and kept["combined_quote"]["id"] == quote["id"]
        assert api.post(f"/api/freight-shipments/{shipment['id']}/allocation", json={"method": "weight"}
                        ).status_code == 200
        assert {o["order_number"] for o in api.get("/api/orders").json()} >= {"SO88100", "SO77154", "INV68606"}
        assert [i["sku"] for i in api.get("/api/catalog/default/items").json()] == ["B30"]
