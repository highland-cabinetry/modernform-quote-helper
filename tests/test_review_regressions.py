"""Regressions for the issues found in the independent 3.0 review."""

from __future__ import annotations

import io
import zipfile

import pytest
from app.freight_models import TextLayout
from app.services.adapters.base import references_in_text
from app.services.order_import import expand_uploads
from fixtures_freight import HighlandDoc, catalog_item, highland_pdf, make_order, pack, upload
from test_quotes_shipments import manual_payload, shipment_payload


@pytest.fixture
def orders(api):
    api.repo.catalog_upsert("default", [catalog_item("W3030", weight=25, height=3)], reason="test weights")
    a = make_order(api, "SO80001", ("B30", 2))
    b = make_order(api, "SO80002", ("W3030", 1), ("B18", 1))
    c = make_order(api, "SO80003", ("DB24", 1))
    return a, b, c


def test_two_invoices_for_one_sales_order_are_two_orders(api):
    first = upload(api, ("inv1.pdf", highland_pdf(HighlandDoc(invoice="68001", sales_order="SO77001",
                                                              items=[("2", "WHS-B30", "Base", "2")]))))
    second = upload(api, ("inv2.pdf", highland_pdf(HighlandDoc(invoice="68002", sales_order="SO77001",
                                                               items=[("1", "WHS-B18", "Base", "1")]))))
    assert first["summary"]["created"] == 1
    assert second["summary"]["created"] == 1 and second["summary"]["conflicts"] == 0
    numbers = {order["order_number"]: order for order in api.get("/api/orders").json()}
    assert {"INV68001", "INV68002"} <= set(numbers)
    assert any("Shares reference" in issue for issue in numbers["INV68002"]["issues"])
    first_order = api.get(f"/api/orders/{numbers['INV68001']['id']}").json()["order"]
    assert [(line["original_sku"], line["quantity"]) for line in first_order["lines"]] == [("WHS-B30", 2)]


def test_a_shared_reference_still_fills_a_reference_only_placeholder(api):
    placeholder = api.post("/api/orders", json={"order_number": "SO77002", "lines": []}).json()
    result = upload(api, ("inv.pdf", highland_pdf(HighlandDoc(invoice="68003", sales_order="SO77002"))))
    assert result["summary"]["updated"] == 1 and result["summary"]["created"] == 0
    filled = api.get(f"/api/orders/{placeholder['id']}").json()["order"]
    assert filled["lines"] and filled["status"] != "reference_only"


def test_resaving_a_superseded_measured_quote_makes_it_current_again(api, orders):
    a, _, _ = orders
    measured = api.post("/api/manual-quotes", json=manual_payload([a["id"]])).json()
    first = measured["freight_quote"]
    _, packed = pack(api, [a["id"]])
    assert api.get(f"/api/quotes/{first['id']}").json()["status"] == "superseded"
    revised = api.put(f"/api/manual-quotes/{measured['id']}", json=manual_payload([a["id"]])).json()
    assert revised["freight_quote"]["status"] == "final"
    assert api.get(f"/api/quotes/{packed['id']}").json()["status"] == "superseded"
    current = [q for q in api.get("/api/quotes", params={"order_id": a["id"]}).json() if q["status"] == "final"]
    assert [q["id"] for q in current] == [first["id"]]


def test_rejected_combined_link_writes_nothing(api, orders):
    a, b, c = orders
    shipment = api.post("/api/freight-shipments", json={"order_ids": [a["id"], b["id"]]}).json()
    plan, _ = pack(api, [c["id"]])
    before = len(api.get("/api/quotes").json())
    response = api.post("/api/quotes", json={"kind": "packed", "packing_result_id": plan["id"],
                                             "order_ids": [c["id"]], "freight_shipment_id": shipment["id"]})
    assert response.status_code == 409
    after = api.get("/api/quotes").json()
    assert len(after) == before
    recorded = api.get("/api/quotes/for-source", params={"packing_result_id": plan["id"]}).json()
    assert [e["action"] for e in api.get(f"/api/quotes/{recorded['id']}").json()["history"]] == ["created"]


def test_closed_shipments_combined_quote_cannot_change(api, orders):
    a, b, c = orders
    shipment = api.post("/api/freight-shipments", json={"order_ids": [a["id"], b["id"]]}).json()
    _, combined = pack(api, [a["id"], b["id"]], freight_shipment_id=shipment["id"])
    view = api.get(f"/api/freight-shipments/{shipment['id']}").json()
    assert view["combined_quote"]["id"] == combined["id"]
    closed = api.put(f"/api/freight-shipments/{shipment['id']}", json=shipment_payload(view, status="closed"))
    assert closed.status_code == 200
    assert api.put(f"/api/quotes/{combined['id']}", json={"order_ids": [a["id"], c["id"]]}).status_code == 409
    assert api.put(f"/api/quotes/{combined['id']}", json={"status": "void"}).status_code == 409
    assert api.put(f"/api/quotes/{combined['id']}", json={"notes": "carrier confirmed"}).status_code == 200


def test_void_combined_quote_is_ignored_and_separate_estimates_count_each_quote_once(api, orders):
    a, b, _ = orders
    _, together = pack(api, [a["id"], b["id"]])
    api.put(f"/api/quotes/{together['id']}", json={"cost": {"amount_cents": 75000}})
    shipment = api.post("/api/freight-shipments", json={"order_ids": [a["id"], b["id"]]}).json()
    # The existing quote for exactly these orders becomes the combined quote.
    assert shipment["combined_quote"]["id"] == together["id"]
    assert api.put(f"/api/quotes/{together['id']}", json={"status": "void"}).status_code == 200
    view = api.get(f"/api/freight-shipments/{shipment['id']}").json()
    assert view["combined_quote"] is None
    _, single_a = pack(api, [a["id"]])
    _, single_b = pack(api, [b["id"]])
    api.put(f"/api/quotes/{single_a['id']}", json={"cost": {"amount_cents": 40000}})
    api.put(f"/api/quotes/{single_b['id']}", json={"cost": {"amount_cents": 35000}})
    view = api.get(f"/api/freight-shipments/{shipment['id']}").json()
    assert view["comparison"]["separate_estimates_cents"] == 75000
    record = next(r for r in api.get("/api/freight-records").json() if r["id"] == shipment["id"])
    assert record["separate_estimates_cents"] == 75000


def test_cancelled_orders_cannot_join_a_shipment_and_reopen_restores_reference_only(api, orders):
    a, _, _ = orders
    shipment = api.post("/api/freight-shipments", json={"order_ids": [a["id"]]}).json()
    cancelled = api.post("/api/orders", json={"order_number": "SO80099", "lines": []}).json()
    status = api.post(f"/api/orders/{cancelled['id']}/status", json={"status": "cancelled", "reason": "cancelled"})
    assert status.status_code == 200, status.text
    added = api.post(f"/api/freight-shipments/{shipment['id']}/orders", json={"add": [cancelled["id"]]})
    assert added.status_code == 409
    placeholder = api.post("/api/orders", json={"order_number": "SO80098", "lines": []}).json()
    view = api.post(f"/api/freight-shipments/{shipment['id']}/orders", json={"add": [placeholder["id"]]}).json()
    closed = api.put(f"/api/freight-shipments/{shipment['id']}", json=shipment_payload(view, status="closed"))
    assert closed.status_code == 200, closed.text
    assert api.get(f"/api/orders/{placeholder['id']}").json()["order"]["status"] == "shipped"
    view = api.get(f"/api/freight-shipments/{shipment['id']}").json()
    reopened = api.put(f"/api/freight-shipments/{shipment['id']}", params={"reason": "wrong truck"},
                       json=shipment_payload(view, status="delivered"))
    assert reopened.status_code == 200, reopened.text
    assert api.get(f"/api/orders/{placeholder['id']}").json()["order"]["status"] == "reference_only"
    assert api.get(f"/api/orders/{a['id']}").json()["order"]["status"] == "ready"


def test_encrypted_zip_member_is_reported_not_fatal():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("ok.csv", "SKU,Qty\nB30,1\n")
        archive.writestr("locked.csv", "SKU,Qty\nB18,1\n")
    data = bytearray(stream.getvalue())
    # Set the "encrypted" flag on the second member's local and central headers.
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = data.find(signature, data.find(signature) + 1)
        data[position + offset] |= 0x1
    expanded, rejected = expand_uploads([("bundle.zip", bytes(data))])
    assert [name for name, _ in expanded] == ["bundle/ok.csv"]
    assert rejected and "encrypted" in rejected[0]["messages"][0]


@pytest.mark.parametrize(("text", "expected"), [
    ("SO77767 SO77769 ASMBLD", ["SO77767", "SO77769"]),
    ("68323 BO 66878", ["INV68323", "INV66878"]),
    ("Jones Santa Fe NM 87501", []),
    ("Santa Fe, New Mexico 87501", []),
    ("Smith 2024-10-05", []),
    ("Lee 3 pallets 1200 lbs", []),
    ("PO 55512 68612", ["INV68612"]),
])
def test_references_in_names(text, expected):
    assert references_in_text(text, "INV") == expected


def test_text_layouts_reject_nested_repetition():
    with pytest.raises(ValueError, match="repeats a group"):
        TextLayout(name="slow", detect_pattern="X", order_pattern=r"(?P<order>\d+)",
                   line_pattern=r"^(?P<quantity>(\d+)+) (?P<sku>\S+)")
