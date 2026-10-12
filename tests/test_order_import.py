"""Order import through the API: batches, de-duplication, conflicts, review flags and corrections."""

from __future__ import annotations

import hashlib

from fixtures_freight import HighlandDoc, csv_bytes, highland_pdf, upload, xlsx_bytes


def pdf(invoice: str, *items, **changes) -> bytes:
    return highland_pdf(HighlandDoc(invoice=invoice, items=list(items) or [("2", "B30", "Base", "2")], **changes))


def stored_order(api, order_id: str) -> dict:
    response = api.get(f"/api/orders/{order_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_batch_of_mixed_formats_in_one_request(api):
    combined = highland_pdf(HighlandDoc(invoice="66101", items=[("1", "B30", "Base", "1")]),
                            HighlandDoc(invoice="66102", kind="Packing Slip", items=[("2", "W3030", "Wall", "2")]))
    result = upload(
        api,
        ("66100.pdf", pdf("66100")),
        ("combined.pdf", combined),
        ("SO77010.csv", csv_bytes([["SKU", "Description", "Qty"], ["B18", "Base 18", "3"]])),
        ("orders.xlsx", xlsx_bytes([["SO Number", "Item", "Qty"], ["SO77011", "DB24", 1], ["SO77012", "SB36", 2]])),
    )
    assert result["summary"]["created"] == 6
    assert result["summary"]["failed"] == 0
    adapters = {row["file"]: (row["adapter"], len(row["order_ids"])) for row in result["files"]}
    assert adapters == {"66100.pdf": ("highland_pdf", 1), "combined.pdf": ("highland_pdf", 2),
                        "SO77010.csv": ("table", 1), "orders.xlsx": ("table", 2)}
    numbers = {o["order_number"]: o for o in result["orders"]}
    assert set(numbers) == {"INV66100", "INV66101", "INV66102", "SO77010", "SO77011", "SO77012"}
    assert all(o["status"] == "ready" for o in numbers.values())
    listed = api.get("/api/orders").json()
    assert {o["order_number"] for o in listed} == set(numbers)
    assert all(o["quote_ids"] == [] and o["shipment"] is None for o in listed)


def test_same_file_again_is_a_duplicate_and_creates_nothing(api):
    content = pdf("66200")
    first = upload(api, ("66200.pdf", content))
    [order_id] = first["created_order_ids"]
    again = upload(api, ("renamed copy.pdf", content))
    [row] = again["files"]
    assert row["status"] == "duplicate"
    assert row["order_ids"] == [order_id]
    assert "Already imported" in row["messages"][0]
    assert again["summary"]["created"] == 0
    assert again["existing"] == [{"order_id": order_id, "order_number": "INV66200", "file": "renamed copy.pdf",
                                  "reason": "same file"}]
    assert len(api.get("/api/orders").json()) == 1


def test_same_file_twice_in_one_batch_creates_one_order(api):
    content = pdf("66210")
    result = upload(api, ("a.pdf", content), ("b.pdf", content))
    assert result["summary"]["created"] == 1
    assert len(api.get("/api/orders").json()) == 1


def test_same_order_in_a_different_file_with_same_lines_is_reported_existing(api):
    first = upload(api, ("invoice.pdf", pdf("66300", po="PO-1")))
    [order_id] = first["created_order_ids"]
    # The packing slip for the same invoice: different bytes, identical lines.
    second = upload(api, ("slip.pdf", pdf("66300", po="PO-1", kind="Packing Slip")))
    assert second["summary"] == {**second["summary"], "created": 0, "existing": 1, "conflicts": 0}
    assert second["existing"][0] == {"order_id": order_id, "order_number": "INV66300", "file": "slip.pdf",
                                     "reason": "same lines"}
    [row] = second["files"]
    assert row["status"] == "imported" and row["order_ids"] == [order_id]
    assert "already imported with the same lines" in row["messages"][0]
    assert stored_order(api, order_id)["order"]["revision"] == 1


def test_same_order_with_different_lines_is_a_conflict_and_never_overwrites(api):
    first = upload(api, ("invoice.pdf", pdf("66400", ("2", "B30", "Base", "2"))))
    [order_id] = first["created_order_ids"]
    before = stored_order(api, order_id)["order"]

    second = upload(api, ("revised.pdf", pdf("66400", ("3", "B30", "Base", "3"), ("1", "W3030", "Wall", "1"))))
    assert second["summary"]["conflicts"] == 1 and second["summary"]["created"] == 0
    [conflict] = second["conflicts"]
    assert conflict["order_id"] == order_id and conflict["file"] == "revised.pdf"
    assert sorted(conflict["differences"]) == ["B30 (flatpack): 2 → 3", "W3030 (flatpack): 0 → 1"]
    assert [(line["original_sku"], line["quantity"]) for line in conflict["lines"]] == [("B30", 3), ("W3030", 1)]
    assert "not changed" in second["files"][0]["messages"][0]

    after = stored_order(api, order_id)["order"]
    assert after["lines"] == before["lines"]
    assert after["revision"] == before["revision"] == 1


def test_conflicting_files_inside_one_batch_keep_the_first(api):
    result = upload(api, ("a.pdf", pdf("66450", ("1", "B30", "Base", "1"))),
                    ("b.pdf", pdf("66450", ("5", "B30", "Base", "5"))))
    assert result["summary"]["created"] == 1 and result["summary"]["conflicts"] == 1
    [order] = result["orders"]
    assert [(line["original_sku"], line["quantity"]) for line in order["lines"]] == [("B30", 1)]


def test_reference_only_order_is_filled_when_its_slip_arrives(api):
    created = api.post("/api/orders", json={"order_number": "INV68606", "notes": "typed by freight manager"})
    assert created.status_code == 200, created.text
    reference = created.json()
    assert reference["status"] == "reference_only" and reference["lines"] == []

    result = upload(api, ("68606.pdf", pdf("68606", ("1", "B30", "Base", "1"), ("2", "W3030", "Wall", "2"))))
    assert result["updated_order_ids"] == [reference["id"]]
    assert result["created_order_ids"] == []
    trace = stored_order(api, reference["id"])
    order = trace["order"]
    assert order["status"] == "ready"
    assert order["revision"] == 2
    # What the freight manager typed is kept; the document's header fills the rest.
    assert order["notes"] == "typed by freight manager"
    assert order["po_number"] == "PO-4471" and order["ship_to"]["postal_code"] == "87501"
    assert [(line["original_sku"], line["quantity"]) for line in order["lines"]] == [("B30", 1), ("W3030", 2)]
    assert order["source_file_id"] == trace["source_file"]["id"]
    assert result["files"][0]["order_ids"] == [reference["id"]]
    assert [entry["action"] for entry in trace["history"]] == ["created", "lines_imported"]
    assert [rev["revision"] for rev in trace["revisions"]] == [1, 2]


def test_measured_quote_name_creates_a_reference_only_order_that_import_completes(api):
    payload = {
        "shipment_name": "68700 BO",
        "pallets": [{"length": 48, "width": 40, "height": 50, "weight": 600, "quantity": 1}],
        "shipping_info": {"destination": {"postal_code": "87501"}},
    }
    manual = api.post("/api/manual-quotes", json=payload).json()
    quote = manual["freight_quote"]
    [order_ref] = quote["orders"]
    assert order_ref["order_number"] == "INV68700"
    result = upload(api, ("68700.pdf", pdf("68700")))
    assert result["updated_order_ids"] == [order_ref["id"]]
    assert stored_order(api, order_ref["id"])["order"]["status"] == "ready"


def test_unmatched_and_fuzzy_skus_are_flagged_for_review(api):
    result = upload(api, ("SO77100.csv", csv_bytes([
        ["SKU", "Description", "Qty"],
        ["WHS-B30", "Base with ignored prefix", "1"],
        ["W30300", "Typo of W3030", "1"],
        ["ZZ-NOPE", "Not in the catalog", "2"],
    ])))
    [order] = result["orders"]
    assert order["status"] == "needs_review"
    assert "1 SKU(s) not in the catalog: ZZ-NOPE" in order["issues"]
    assert "1 SKU(s) only roughly match the catalog: W30300" in order["issues"]
    assert not any("WHS-B30" in issue for issue in order["issues"])
    # The authored SKUs are stored unchanged.
    assert [line["original_sku"] for line in order["lines"]] == ["WHS-B30", "W30300", "ZZ-NOPE"]


def test_original_bytes_are_stored_and_downloadable(api, freight_repo):
    content = pdf("66500")
    result = upload(api, ("66500 invoice.pdf", content))
    [order_id] = result["created_order_ids"]
    trace = stored_order(api, order_id)
    source = trace["source_file"]
    assert source["sha256"] == hashlib.sha256(content).hexdigest()
    assert source["filename"] == "66500 invoice.pdf" and source["status"] == "imported"
    assert "stored_path" not in source
    download = api.get(f"/api/source-files/{source['id']}")
    assert download.status_code == 200
    assert download.content == content
    assert "66500%20invoice.pdf" in download.headers["content-disposition"]
    stored = freight_repo.get_source_file(source["id"])
    assert stored.stored_path.startswith(str(freight_repo.database_path.parent / "source_files"))
    assert api.get("/api/source-files/not-a-real-id").status_code == 404


def test_rejected_files_are_not_stored(api, freight_repo):
    upload(api, ("notes.txt", b"just some words\nno columns here"))
    assert not (freight_repo.database_path.parent / "source_files").exists()


def test_correction_creates_a_revision_and_an_audit_entry(api):
    result = upload(api, ("66600.pdf", pdf("66600", ("1", "B30", "Base", "1"))))
    [order] = result["orders"]
    corrected = api.put(f"/api/orders/{order['id']}", json={
        "order_number": "INV66600",
        "po_number": "PO-FIXED",
        "lines": [{"original_sku": "B30", "quantity": 2, "description": "Base"},
                  {"original_sku": "W3030", "quantity": 1}],
        "reason": "Customer added a wall cabinet",
    })
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["revision"] == 2
    trace = stored_order(api, order["id"])
    assert trace["order"]["po_number"] == "PO-FIXED"
    assert [rev["revision"] for rev in trace["revisions"]] == [1, 2]
    correction = trace["history"][-1]
    assert correction["action"] == "corrected"
    assert correction["summary"] == "Customer added a wall cabinet"
    assert [line["quantity"] for line in correction["details"]["before"]["lines"]] == [1]
    assert [line["quantity"] for line in correction["details"]["after"]["lines"]] == [2, 1]
    assert api.get("/api/audit", params={"entity_type": "order", "entity_id": order["id"]}).json()[-1]["action"] == \
        "corrected"


def test_correcting_a_reference_only_order_with_lines_makes_it_ready(api):
    reference = api.post("/api/orders", json={"order_number": "SO77300"}).json()
    corrected = api.put(f"/api/orders/{reference['id']}",
                        json={"order_number": "SO77300", "lines": [{"original_sku": "B30", "quantity": 1}]}).json()
    assert corrected["status"] == "ready"
    assert not any(issue.startswith("Reference only") for issue in corrected["issues"])


def test_order_numbers_and_references_are_unique_per_company(api):
    first = api.post("/api/orders", json={"order_number": "SO77400", "references": ["INV66999"]})
    assert first.status_code == 200
    assert api.post("/api/orders", json={"order_number": "so77400"}).status_code == 409
    # A typed reference that already belongs to another order is a duplicate too.
    assert api.post("/api/orders", json={"order_number": "INV66999"}).status_code == 409
    second = api.post("/api/orders", json={"order_number": "SO77401"}).json()
    renamed = api.put(f"/api/orders/{second['id']}", json={"order_number": "SO77400"})
    assert renamed.status_code == 409
    assert stored_order(api, second["id"])["order"]["order_number"] == "SO77401"
    # An import for the referenced invoice resolves to the existing (reference-only) order and fills it.
    result = upload(api, ("66999.pdf", pdf("66999")))
    assert result["created_order_ids"] == []
    assert result["updated_order_ids"] == [first.json()["id"]]
    assert stored_order(api, first.json()["id"])["order"]["order_number"] == "SO77400"


def test_search_finds_orders_typed_with_dashes_or_spaces(api):
    created = api.post("/api/orders", json={"order_number": "SO-77767", "lines": []}).json()
    assert created["order_number"] == "SO77767"
    for typed in ("SO-77767", "so 77767", "77767", "SO77767"):
        assert [order["id"] for order in api.get("/api/orders", params={"search": typed}).json()] == [created["id"]]
