"""Product catalog: single-item edits, import preview/apply, export round-trips, validation, scoping."""

from __future__ import annotations

import csv
import io

import pytest
from app.services.catalog import catalog_imports
from fixtures_freight import acme_company, catalog_item, csv_bytes, seed_catalog, upload


def item_payload(sku: str, **changes) -> dict:
    return {**catalog_item(sku).model_dump(mode="json", exclude={"id"}), **changes}


def save_item(api, catalog: str, item: dict, reason: str | None = None):
    body = {"item": item, **({"reason": reason} if reason else {})}
    return api.post(f"/api/catalog/{catalog}/items", json=body)


def items(api, catalog: str, **params) -> dict[str, dict]:
    return {row["sku"]: row for row in api.get(f"/api/catalog/{catalog}/items", params=params).json()}


def preview(api, catalog: str, filename: str, content: bytes) -> dict:
    response = api.post(f"/api/catalog/{catalog}/import/preview", files={"file": (filename, content)})
    assert response.status_code == 200, response.text
    return response.json()


def exported_rows(api, catalog: str) -> list[list[str]]:
    content = api.get(f"/api/catalog/{catalog}/export/csv").content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(content)))


def to_csv(rows: list[list[str]]) -> bytes:
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    return stream.getvalue().encode()


# --------------------------------------------------------------------------- single items


def test_single_item_create_update_and_history(api):
    created = save_item(api, "default", item_payload("PANTRY96", weight=120, description="Pantry"), "new product")
    assert created.status_code == 200, created.text
    item = created.json()
    assert item["id"] and any(issue["code"] == "NO_ASSEMBLED_DIMENSIONS" for issue in item["issues"])

    updated = save_item(api, "default", {**item_payload("PANTRY96", weight=125.5, description="Pantry"),
                                         "id": item["id"]}, "re-weighed in warehouse")
    assert updated.status_code == 200 and updated.json()["weight"] == 125.5
    # Saving the same values again changes nothing and writes no audit entry.
    save_item(api, "default", {**item_payload("PANTRY96", weight=125.5, description="Pantry"), "id": item["id"]})

    history = api.get(f"/api/catalog/default/items/{item['id']}/history").json()
    assert [entry["action"] for entry in history] == ["created", "updated"]
    assert "new product" in history[0]["summary"]
    assert history[1]["details"]["changes"] == {"weight": [120.0, 125.5]}
    assert "re-weighed in warehouse" in history[1]["summary"]
    assert items(api, "default")["PANTRY96"]["weight"] == 125.5

    assert api.delete(f"/api/catalog/default/items/{item['id']}").json() == {"deactivated": True}
    assert "PANTRY96" not in items(api, "default")
    assert items(api, "default", include_inactive=True)["PANTRY96"]["active"] is False
    assert api.get(f"/api/catalog/default/items/{item['id']}/history").json()[-1]["action"] == "deactivated"
    # Saving it again from the catalog screen reactivates it, and the history says so.
    save_item(api, "default", {**item_payload("PANTRY96", weight=125.5, description="Pantry"), "id": item["id"]})
    assert items(api, "default")["PANTRY96"]["active"] is True
    last = api.get(f"/api/catalog/default/items/{item['id']}/history").json()[-1]
    assert (last["action"], last["details"]["changes"]) == ("updated", {"active": [False, True]})


def test_sku_rename_is_rejected(api):
    b30 = items(api, "default")["B30"]
    renamed = save_item(api, "default", {**item_payload("B30-NEW"), "id": b30["id"]})
    assert renamed.status_code == 409 and "Changing a SKU is not supported" in renamed.json()["detail"]
    onto_existing = save_item(api, "default", {**item_payload("W3030"), "id": b30["id"]})
    assert onto_existing.status_code == 409 and "already exists" in onto_existing.json()["detail"]
    # Case-only differences are the same SKU.
    assert save_item(api, "default", {**item_payload("b30", weight=41), "id": b30["id"]}).status_code == 200
    assert save_item(api, "default", {**item_payload("ZZZ"), "id": 99999}).status_code == 404
    assert {sku.upper(): row for sku, row in items(api, "default").items()}["B30"]["weight"] == 41


# --------------------------------------------------------------------------- import preview and apply


@pytest.fixture
def catalog_file(api):
    """An export of the seeded catalog, edited the way a user would in Excel."""

    seed_catalog(api.repo, [catalog_item("MFR1", manufacturer_sku="M-100")])
    rows = exported_rows(api, "default")
    header = rows[0]
    column = {name: index for index, name in enumerate(header)}
    by_sku = {row[column["sku"]]: row for row in rows[1:]}
    by_sku["B30"][column["weight"]] = "45"  # changed
    del by_sku["DB24"]  # missing from the file (reported, not deleted)

    def new_row(sku: str, **values) -> list[str]:
        row = list(by_sku["B18"])
        row[column["sku"]] = sku
        row[column["manufacturer_sku"]] = ""
        for key, value in values.items():
            row[column[key]] = value
        return row

    extra = [
        new_row("NEW1", description="Brand new"),
        new_row("DUP", description="first"),
        new_row("DUP", description="second"),
        new_row("NEW2", manufacturer_sku="m-100"),  # manufacturer SKU already owned by MFR1
        new_row("BROKEN", weight=""),  # weight is required
    ]
    return to_csv([header, *by_sku.values(), *extra])


def test_import_preview_classifies_every_row(api, catalog_file):
    result = preview(api, "default", "catalog.csv", catalog_file)
    assert result["counts"] == {"new": 1, "changed": 1, "unchanged": 5, "invalid": 1, "conflict": 3}
    rows = {(row["sku"], row["status"]): row for row in result["rows"]}
    assert rows[("B30", "changed")]["changes"] == {"weight": [40.0, 45.0]}
    assert ("NEW1", "new") in rows
    assert [row["message"] for row in result["rows"] if row["sku"] == "DUP"] == [
        "SKU appears more than once in this file."] * 2
    assert "already belongs to MFR1" in rows[("NEW2", "conflict")]["message"]
    assert rows[("BROKEN", "invalid")]["message"].startswith("catalog.csv row")
    assert result["not_in_file"] == ["DB24"]
    assert result["can_apply"] is True
    # A preview changes nothing.
    assert items(api, "default")["B30"]["weight"] == 40 and "NEW1" not in items(api, "default")


def test_apply_writes_accepted_rows_once_and_the_preview_is_single_use(api, catalog_file):
    result = preview(api, "default", "catalog.csv", catalog_file)
    applied = api.post("/api/catalog/default/import/apply", json={"preview_id": result["preview_id"]})
    assert applied.status_code == 200
    assert applied.json() == {"inserted": 1, "updated": 1, "filename": "catalog.csv"}
    catalog = items(api, "default")
    assert catalog["B30"]["weight"] == 45 and "NEW1" in catalog
    assert not {"DUP", "NEW2", "BROKEN"} & set(catalog)
    assert "DB24" in catalog  # rows missing from the file are never deleted
    again = api.post("/api/catalog/default/import/apply", json={"preview_id": result["preview_id"]})
    assert again.status_code == 404 and "expired or was already applied" in again.json()["detail"]
    history = api.get(f"/api/catalog/default/items/{catalog['B30']['id']}/history").json()
    assert "import catalog.csv" in history[-1]["summary"]


def test_apply_is_all_or_nothing(api, catalog_file, monkeypatch):
    result = preview(api, "default", "catalog.csv", catalog_file)
    calls = []
    original = api.repo.audit

    def failing_audit(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        return original(*args, **kwargs)

    monkeypatch.setattr(api.repo, "audit", failing_audit)
    with pytest.raises(RuntimeError, match="disk full"):
        catalog_imports.apply(api.repo, result["preview_id"])
    monkeypatch.setattr(api.repo, "audit", original)  # never monkeypatch.undo(): it would unbind the test database
    catalog = items(api, "default")
    assert catalog["B30"]["weight"] == 40 and "NEW1" not in catalog


@pytest.mark.parametrize("export_format", ["xlsx", "csv"])
def test_export_round_trips_through_preview_with_no_changes(api, export_format):
    seed_catalog(api.repo, [
        catalog_item("CSB42", assembled=(45, 45, 31.25), manufacturer_sku="M-CSB42", item_kind="cabinet",
                     must_stay_upright=True, max_stack_layers=2, freight_class="85", nmfc="79300", color="#336699",
                     description='Corner sink base, 42"'),
        catalog_item("FILLER3", item_kind="accessory", bundle_approved=True, bundle_max_units=5,
                     packing_role="top_accessory", allowed_rotations=["LWH", "WLH", "LHW"], weight=2.375,
                     overhang_approved=True, max_overhang_per_side=1.5, rules_reviewed=False),
    ])
    exported = api.get(f"/api/catalog/default/export/{export_format}")
    assert exported.status_code == 200
    assert f'catalog-default.{export_format}' in exported.headers["content-disposition"]
    result = preview(api, "default", f"export.{export_format}", exported.content)
    total = len(items(api, "default"))
    differences = [row for row in result["rows"] if row["status"] != "unchanged"]
    assert result["counts"] == {"new": 0, "changed": 0, "unchanged": total, "invalid": 0, "conflict": 0}, differences
    assert result["not_in_file"] == [] and result["can_apply"] is False


# --------------------------------------------------------------------------- validation report


def test_validation_report_flags_missing_sizes_and_ambiguous_skus(api):
    seed_catalog(api.repo, [
        catalog_item("MB-B30"),  # normalizes to B30 with the MB prefix ignored
        catalog_item("TALL", length=130, weight=450, assembled=(130, 24, 90)),
        catalog_item("GOOD", assembled=(30, 24, 34.5)),
    ])
    report = api.get("/api/catalog/default/report").json()
    by_sku = {row["sku"]: {issue["code"]: issue for issue in row["issues"]} for row in report["rows"]}
    assert by_sku["B30"]["AMBIGUOUS_SKU"]["severity"] == "error"
    assert by_sku["MB-B30"]["AMBIGUOUS_SKU"]["severity"] == "error"
    assert "NO_ASSEMBLED_DIMENSIONS" in by_sku["B30"]
    assert {"OVERSIZE", "HEAVY"} <= set(by_sku["TALL"])
    assert "GOOD" not in by_sku
    assert report["counts"]["AMBIGUOUS_SKU"] == 2
    assert report["items"] == len(items(api, "default"))


# --------------------------------------------------------------------------- company-scoped catalogs


def test_company_catalogs_are_isolated(api):
    acme = acme_company(api.repo, catalog="acme")
    assert save_item(api, "acme", item_payload("AC-BASE30")).status_code == 200
    assert save_item(api, "acme", item_payload("B30", weight=99)).status_code == 200  # same SKU, other catalog
    assert set(items(api, "acme")) == {"AC-BASE30", "B30"}
    assert "AC-BASE30" not in items(api, "default")
    assert items(api, "default")["B30"]["weight"] == 40 and items(api, "acme")["B30"]["weight"] == 99
    catalogs = {row["name"]: row for row in api.get("/api/catalogs").json()}
    assert catalogs["acme"] == {"name": "acme", "item_count": 2, "companies": ["ACME"]}
    assert catalogs["default"]["companies"] == ["HCI"]
    assert [row[0] for row in exported_rows(api, "acme")[1:]] == ["AC-BASE30", "B30"]

    # Aliases are per catalog and drive matching for that company only.
    target = items(api, "acme")["AC-BASE30"]
    alias = api.post("/api/catalog/acme/aliases", json={"alias_sku": "OLD-BASE", "target_item_id": target["id"]})
    assert alias.status_code == 200, alias.text
    assert api.post("/api/catalog/acme/aliases", json={"alias_sku": "AC-BASE30", "target_item_id": target["id"]}
                    ).status_code == 409
    assert api.get("/api/catalog/default/aliases").json() == []
    slip = csv_bytes([["SKU", "Qty"], ["OLD-BASE", "1"], ["W3030", "1"]])
    [acme_order] = upload(api, ("SO7001.csv", slip), company_id=acme.id)["orders"]
    [highland_order] = upload(api, ("SO7001.csv", slip))["orders"]
    assert acme_order["company_id"] == acme.id and highland_order["company_id"] != acme.id
    assert acme_order["issues"] == ["1 SKU(s) not in the catalog: W3030"]
    assert highland_order["issues"] == ["1 SKU(s) not in the catalog: OLD-BASE"]

    # Deactivating removes the item (and its aliases) from the active catalog.
    assert api.delete(f"/api/catalog/acme/items/{target['id']}").status_code == 200
    assert "AC-BASE30" not in items(api, "acme")
    assert api.get("/api/catalog/acme/aliases").json() == []


def test_company_catalog_import_preview_compares_only_that_catalog(api):
    acme_company(api.repo, catalog="acme")
    rows = exported_rows(api, "default")
    result = preview(api, "acme", "from-highland.csv", to_csv(rows))
    assert result["counts"]["new"] == len(rows) - 1 and result["counts"]["unchanged"] == 0
    applied = api.post("/api/catalog/acme/import/apply", json={"preview_id": result["preview_id"]}).json()
    assert applied["inserted"] == len(rows) - 1
    assert set(items(api, "acme")) == set(items(api, "default"))
    # The default catalog was not touched by the Acme import.
    trail = api.get("/api/audit", params={"entity_type": "catalog_item", "entity_id": "default:B30"}).json()
    assert [entry["action"] for entry in trail] == ["created"]
    acme_trail = api.get("/api/audit", params={"entity_type": "catalog_item", "entity_id": "acme:B30"}).json()
    assert [entry["action"] for entry in acme_trail] == ["created"]


def test_csv_quoting_does_not_depend_on_the_sniffers_guess(monkeypatch):
    # csv.Sniffer's doublequote guess varies with the sampled rows (it misread 42"" on Windows CI);
    # only its delimiter is used.
    import csv as csv_module

    from app.services import imports

    class Guess(csv_module.excel):
        delimiter = ";"
        doublequote = False
        escapechar = "\\"

    monkeypatch.setattr(csv_module.Sniffer, "sniff", lambda self, sample, delimiters=None: Guess)
    grid = imports._csv_grid('﻿sku;description\r\nCSB42;"Corner sink base; 42"""\r\n'.encode())
    assert grid == [["sku", "description"], ["CSB42", 'Corner sink base; 42"']]
