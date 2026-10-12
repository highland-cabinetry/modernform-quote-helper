"""Quotes recorded from pallet plans and measured quotes; shipments grouping orders; locking."""

from __future__ import annotations

import time

import pytest
from app.freight_models import Quote
from app.freight_repository import ConflictError
from app.models import PackingRequest, PackingSlipLine
from app.services.freight import legacy_backfill
from app.services.packing_jobs import PackingJobs
from app.services.shipments import build_shipment
from fixtures_freight import catalog_item, make_order, make_repo, pack, seed_catalog


def shipment_payload(view: dict, **changes) -> dict:
    fields = ("name", "status", "carrier", "broker", "pro_number", "bol_number", "pickup_date", "delivery_date",
              "destination", "customer_charge_cents", "notes")
    return {**{key: view[key] for key in fields}, **changes}


def get_quote(api, quote_id: str) -> dict:
    return api.get(f"/api/quotes/{quote_id}").json()


@pytest.fixture
def orders(api):
    api.repo.catalog_upsert("default", [catalog_item("W3030", weight=25, height=3)], reason="test weights")
    a = make_order(api, "SO80001", ("B30", 2))  # 80 lb
    b = make_order(api, "SO80002", ("W3030", 1), ("B18", 1))  # 65 lb
    c = make_order(api, "SO80003", ("DB24", 1))  # 40 lb
    return a, b, c


# --------------------------------------------------------------------------- quoting orders together


def test_orders_quoted_together_record_one_quote_with_per_order_weights(api, orders):
    a, b, _ = orders
    selection = api.post("/api/orders/packing-lines", json={"order_ids": [a["id"], b["id"]]}).json()
    assert {line["source_ref"].split(" · ")[0] for line in selection["lines"]} == {"SO80001", "SO80002"}
    assert selection["suggested_name"] == "SO80001 + SO80002"
    assert selection["shipping"]["destination"]["postal_code"] == "87501"

    plan, quote = pack(api, [a["id"], b["id"]])
    assert plan["freight_ready"]
    assert quote["kind"] == "packed" and quote["status"] == "final" and quote["role"] == "individual"
    assert quote["order_ids"] == [a["id"], b["id"]]
    assert [o["order_number"] for o in quote["orders"]] == ["SO80001", "SO80002"]
    summary = quote["summary"]
    assert summary["order_weights"] == {a["id"]: 80.0, b["id"]: 65.0}
    assert sum(summary["order_weights"].values()) == pytest.approx(summary["item_weight"]) == 145.0
    assert sum(summary["order_pallet_shares"].values()) == pytest.approx(summary["pallets"], abs=1e-3)
    assert summary["total_units"] == 4 and summary["freight_ready"] is True
    assert summary["total_weight"] > summary["item_weight"]  # pallet tare is included in the freight weight

    listed = {o["order_number"]: o for o in api.get("/api/orders").json()}
    assert listed["SO80001"]["quote_numbers"] == [quote["number"]] == listed["SO80002"]["quote_numbers"]
    assert listed["SO80003"]["quote_numbers"] == []
    assert [q["number"] for q in api.get("/api/quotes", params={"order_id": b["id"]}).json()] == [quote["number"]]
    assert [entry["action"] for entry in get_quote(api, quote["id"])["history"]] == ["created"]


def test_packing_lines_warn_about_mixed_destinations_and_review_state(api):
    a = make_order(api, "SO80101", ("B30", 1), postal_code="87501")
    b = make_order(api, "SO80102", ("B30", 1), postal_code="79763")
    ref = api.post("/api/orders", json={"order_number": "SO80103"}).json()
    selection = api.post("/api/orders/packing-lines", json={"order_ids": [a["id"], b["id"], ref["id"]]}).json()
    assert any("different ZIP codes" in warning for warning in selection["warnings"])
    assert any("SO80103 has no imported lines" in warning for warning in selection["warnings"])
    assert selection["shipping"]["destination"] == {}


def test_accepting_a_packing_job_records_the_quote_once(api, orders):
    a, b, _ = orders
    selected = api.post("/api/orders/packing-lines", json={"order_ids": [a["id"], b["id"]]}).json()
    request = PackingRequest(shipment_name=selected["suggested_name"], lines=selected["lines"],
                             settings=selected["settings"], company_id=selected["company_id"],
                             order_ids=[a["id"], b["id"]])
    manager = PackingJobs()
    try:
        job = manager.start(request, api.repo)
        deadline = time.monotonic() + 30
        while job["state"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.1)
            job = manager.get(job["id"])
        assert job["state"] == "complete", job.get("message")
        saved = manager.accept(job["id"], request, api.repo)
        assert manager.accept(job["id"], request, api.repo).id == saved.id
    finally:
        manager.shutdown()
    quotes = api.get("/api/quotes").json()
    assert len(quotes) == 1
    [quote] = quotes
    assert quote["packing_result_id"] == saved.id and quote["order_ids"] == [a["id"], b["id"]]
    assert quote["summary"]["order_weights"] == {a["id"]: 80.0, b["id"]: 65.0}


def test_an_order_cannot_be_in_one_quote_twice(api, orders, freight_repo):
    a, b, _ = orders
    assert api.post("/api/orders/packing-lines", json={"order_ids": [a["id"], a["id"]]}).status_code == 409
    plan, quote = pack(api, [a["id"]])
    duplicate = api.post("/api/quotes", json={"kind": "packed", "packing_result_id": plan["id"],
                                              "order_ids": [a["id"], a["id"]]})
    assert duplicate.status_code == 422
    assert api.put(f"/api/quotes/{quote['id']}", json={"order_ids": [a["id"], b["id"], a["id"]]}).status_code == 409
    with pytest.raises(ConflictError):
        freight_repo.save_quote(Quote(company_id=quote["company_id"], kind="packed", order_ids=[a["id"], a["id"]]))
    # A plan whose request repeats an order still records the order once.
    selected = api.post("/api/orders/packing-lines", json={"order_ids": [b["id"]]}).json()
    repeated = api.post("/api/shipments/pack", json={"lines": selected["lines"], "settings": selected["settings"],
                                                      "order_ids": [b["id"], b["id"]]}).json()
    recorded = api.get("/api/quotes/for-source", params={"packing_result_id": repeated["id"]}).json()
    assert recorded["order_ids"] == [b["id"]]


def test_quoting_the_same_orders_again_supersedes_the_earlier_quote(api, orders):
    a, b, _ = orders
    _, first = pack(api, [a["id"], b["id"]])
    _, second = pack(api, [b["id"], a["id"]])
    _, alone = pack(api, [a["id"]])
    assert get_quote(api, first["id"])["status"] == "superseded"
    assert get_quote(api, second["id"])["status"] == "final"
    assert get_quote(api, alone["id"])["status"] == "final"  # a different set of orders is a different quote
    history = [entry["action"] for entry in get_quote(api, first["id"])["history"]]
    assert history == ["created", "superseded"]


def test_quote_update_records_cost_and_status_and_void_is_final(api, orders):
    a, _, _ = orders
    _, quote = pack(api, [a["id"]])
    updated = api.put(f"/api/quotes/{quote['id']}", json={
        "cost": {"amount_cents": 41250, "carrier": "ODFL", "customer_price_cents": 52000}, "notes": "phone quote"})
    assert updated.status_code == 200
    assert updated.json()["cost"]["amount_cents"] == 41250
    assert api.put(f"/api/quotes/{quote['id']}", json={"status": "void"}).json()["status"] == "void"
    assert api.put(f"/api/quotes/{quote['id']}", json={"status": "final"}).status_code == 409
    assert [e["action"] for e in get_quote(api, quote["id"])["history"]] == ["created", "updated", "updated"]
    # Void quotes no longer show against the order.
    assert next(o for o in api.get("/api/orders").json() if o["id"] == a["id"])["quote_ids"] == []


# --------------------------------------------------------------------------- shipments


def test_orders_quoted_separately_then_shipped_together(api, orders):
    a, b, _ = orders
    _, quote_a = pack(api, [a["id"]])
    _, quote_b = pack(api, [b["id"]])

    created = api.post("/api/freight-shipments", json={"order_ids": [a["id"], b["id"]], "carrier": "Estes"})
    assert created.status_code == 200, created.text
    shipment = created.json()
    assert shipment["number"] == "S-000001" and shipment["name"] == "SO80001 + SO80002"
    assert shipment["destination"]["postal_code"] == "87501"
    assert shipment["combined_quote"] is None  # nobody quoted these two together
    assert [q["number"] for q in shipment["individual_quotes"][a["id"]]] == [quote_a["number"]]

    # A combined plan built for the shipment becomes its combined quote; the originals are untouched.
    _, combined = pack(api, [a["id"], b["id"]], freight_shipment_id=shipment["id"])
    assert combined["role"] == "combined" and combined["freight_shipment_id"] == shipment["id"]
    view = api.get(f"/api/freight-shipments/{shipment['id']}").json()
    assert view["combined_quote"]["id"] == combined["id"]
    for original in (quote_a, quote_b):
        kept = get_quote(api, original["id"])
        assert (kept["status"], kept["role"], kept["summary"]) == ("final", "individual", original["summary"])

    # A newer combined plan replaces the combined quote; the previous one is kept as superseded.
    _, newer = pack(api, [a["id"], b["id"]], freight_shipment_id=shipment["id"])
    view = api.get(f"/api/freight-shipments/{shipment['id']}").json()
    assert view["combined_quote"]["id"] == newer["id"]
    assert get_quote(api, combined["id"])["status"] == "superseded"
    assert get_quote(api, quote_a["id"])["status"] == "final"
    actions = [entry["action"] for entry in view["history"]]
    assert actions == ["created", "combined_quote", "combined_quote"]


def test_existing_combined_quote_is_linked_when_the_shipment_is_created(api, orders):
    a, b, _ = orders
    _, together = pack(api, [a["id"], b["id"]])
    shipment = api.post("/api/freight-shipments", json={"order_ids": [b["id"], a["id"]]}).json()
    assert shipment["combined_quote"]["id"] == together["id"]
    assert get_quote(api, together["id"])["role"] == "combined"
    # A quote covering an order outside the shipment cannot be its combined quote.
    c_only = make_order(api, "SO80009", ("B30", 1))
    _, other = pack(api, [a["id"], c_only["id"]])
    linked = api.post(f"/api/freight-shipments/{shipment['id']}/combined-quote", json={"quote_id": other["id"]})
    assert linked.status_code == 409


def test_an_order_can_be_in_only_one_active_shipment(api, orders):
    a, b, c = orders
    first = api.post("/api/freight-shipments", json={"order_ids": [a["id"]]}).json()
    clash = api.post("/api/freight-shipments", json={"order_ids": [b["id"], a["id"]]})
    assert clash.status_code == 409 and "SO80001 (S-000001)" in clash.json()["detail"]
    second = api.post("/api/freight-shipments", json={"order_ids": [b["id"]]}).json()
    assert api.post(f"/api/freight-shipments/{second['id']}/orders", json={"add": [a["id"]]}).status_code == 409
    assert api.post("/api/freight-shipments", json={"order_ids": [c["id"], c["id"]]}).status_code == 422
    assert {o["order_number"] for o in api.get("/api/orders", params={"unshipped": True}).json()} == {"SO80003"}

    # Cancelling the first shipment frees the order; the cancelled one cannot come back while it is taken.
    cancelled = api.put(f"/api/freight-shipments/{first['id']}", json=shipment_payload(first, status="cancelled"))
    assert cancelled.status_code == 200
    moved = api.post(f"/api/freight-shipments/{second['id']}/orders", json={"add": [a["id"]]})
    assert moved.status_code == 200 and set(moved.json()["order_ids"]) == {a["id"], b["id"]}
    revived = api.put(f"/api/freight-shipments/{first['id']}", params={"reason": "mistake"},
                      json=shipment_payload(first, status="planning"))
    assert revived.status_code == 409
    # An order in an active shipment cannot be cancelled.
    status = api.post(f"/api/orders/{a['id']}/status", json={"status": "cancelled", "reason": "customer"})
    assert status.status_code == 409


def test_closed_shipment_is_locked_until_reopened_with_a_reason(api, orders):
    a, b, c = orders
    _, quote = pack(api, [a["id"], b["id"]])
    shipment = api.post("/api/freight-shipments", json={"order_ids": [a["id"], b["id"]]}).json()
    sid = shipment["id"]
    assert api.post(f"/api/freight-shipments/{sid}/expenses", json={"amount_cents": 50000}).status_code == 200
    expense_id = api.get(f"/api/freight-shipments/{sid}").json()["expenses"][0]["id"]

    closed = api.put(f"/api/freight-shipments/{sid}", json=shipment_payload(shipment, status="closed"))
    assert closed.status_code == 200 and closed.json()["locked"] is True
    assert {o["status"] for o in closed.json()["orders"]} == {"shipped"}

    assert api.post(f"/api/freight-shipments/{sid}/expenses", json={"amount_cents": 100}).status_code == 409
    assert api.post(f"/api/freight-shipments/{sid}/expenses/{expense_id}/void",
                    json={"reason": "duplicate"}).status_code == 409
    assert api.post(f"/api/freight-shipments/{sid}/orders", json={"add": [c["id"]]}).status_code == 409
    assert api.post(f"/api/freight-shipments/{sid}/orders", json={"remove": [b["id"]]}).status_code == 409
    assert api.put(f"/api/freight-shipments/{sid}",
                   json=shipment_payload(shipment, status="closed", carrier="Other")).status_code == 409
    _, other = pack(api, [a["id"], b["id"]])
    assert api.post(f"/api/freight-shipments/{sid}/combined-quote", json={"quote_id": other["id"]}).status_code == 409
    assert api.get(f"/api/freight-shipments/{sid}").json()["combined_quote"]["id"] == quote["id"]
    # Shipped orders keep their lines.
    locked_lines = api.put(f"/api/orders/{a['id']}", json={"order_number": "SO80001",
                                                           "lines": [{"original_sku": "B30", "quantity": 9}]})
    assert locked_lines.status_code == 409
    # ...and their status cannot be changed by hand to get around the lock.
    unlock = api.post(f"/api/orders/{a['id']}/status", json={"status": "ready", "reason": "edit lines"})
    assert unlock.status_code == 409
    assert api.post("/api/orders/no-such-order/status", json={"status": "ready", "reason": "x"}).status_code == 404
    # Notes may still be added to a closed shipment.
    noted = api.put(f"/api/freight-shipments/{sid}",
                    json=shipment_payload(shipment, status="closed", notes="POD filed"))
    assert noted.status_code == 200

    no_reason = api.put(f"/api/freight-shipments/{sid}", json=shipment_payload(shipment, status="delivered"))
    assert no_reason.status_code == 409
    reopened = api.put(f"/api/freight-shipments/{sid}", params={"reason": "carrier rebilled"},
                       json=shipment_payload(shipment, status="delivered"))
    assert reopened.status_code == 200 and reopened.json()["locked"] is False
    assert {o["status"] for o in reopened.json()["orders"]} == {"ready"}
    reopen_entry = reopened.json()["history"][-1]
    assert reopen_entry["action"] == "reopened" and "carrier rebilled" in reopen_entry["summary"]
    assert api.post(f"/api/freight-shipments/{sid}/expenses", json={"amount_cents": 2500, "kind": "accessorial"}
                    ).status_code == 200


def test_plan_built_for_a_closed_shipment_is_quoted_without_relinking(api, orders):
    a, _, _ = orders
    shipment = api.post("/api/freight-shipments", json={"order_ids": [a["id"]]}).json()
    api.put(f"/api/freight-shipments/{shipment['id']}", json=shipment_payload(shipment, status="closed"))
    plan, quote = pack(api, [a["id"]], freight_shipment_id=shipment["id"])
    assert quote["freight_shipment_id"] is None and quote["role"] == "individual"
    assert api.get(f"/api/freight-shipments/{shipment['id']}").json()["combined_quote"] is None
    # Asking explicitly to link it is refused without touching the recorded quote.
    explicit = api.post("/api/quotes", json={"kind": "packed", "packing_result_id": plan["id"],
                                             "order_ids": [a["id"]], "freight_shipment_id": shipment["id"]})
    assert explicit.status_code == 409
    assert get_quote(api, quote["id"])["history"][-1]["action"] == "created"
    assert len(api.get("/api/quotes").json()) == 1


# --------------------------------------------------------------------------- measured quotes


def manual_payload(order_ids: list[str], **changes) -> dict:
    return {
        "shipment_name": "Measured SO80001",
        "pallets": [{"length": 48, "width": 40, "height": 52, "weight": 410.5, "quantity": 2},
                    {"length": 96, "width": 40, "height": 40, "weight": 300, "quantity": 1}],
        "shipping_info": {"destination": {"postal_code": "87501"}},
        "order_ids": order_ids,
        **changes,
    }


def test_measured_quote_save_records_and_revises_one_quote(api, orders):
    a, b, _ = orders
    saved = api.post("/api/manual-quotes", json=manual_payload([a["id"]]))
    assert saved.status_code == 200, saved.text
    manual = saved.json()
    quote = manual["freight_quote"]
    assert quote["kind"] == "measured" and quote["manual_quote_id"] == manual["id"]
    assert quote["order_ids"] == [a["id"]]
    assert quote["summary"]["pallets"] == 3 and quote["summary"]["long_pallets"] == 1
    assert quote["summary"]["total_weight"] == pytest.approx(1121.0)  # gross weights, no tare added

    revised = api.put(f"/api/manual-quotes/{manual['id']}",
                      json=manual_payload([a["id"], b["id"]], pallets=[{"length": 48, "width": 40, "height": 60,
                                                                         "weight": 700, "quantity": 1}])).json()
    again = revised["freight_quote"]
    assert again["id"] == quote["id"] and again["number"] == quote["number"]
    assert again["order_ids"] == [a["id"], b["id"]]
    assert again["summary"]["pallets"] == 1 and again["summary"]["total_weight"] == 700
    assert [e["action"] for e in get_quote(api, quote["id"])["history"]] == ["created", "revised"]
    assert len(api.get("/api/quotes", params={"kind": "measured"}).json()) == 1


def test_measured_quote_for_a_shipment_becomes_its_combined_quote(api, orders):
    a, b, _ = orders
    shipment = api.post("/api/freight-shipments", json={"order_ids": [a["id"], b["id"]]}).json()
    manual = api.post("/api/manual-quotes", json=manual_payload([a["id"], b["id"]],
                                                                freight_shipment_id=shipment["id"])).json()
    view = api.get(f"/api/freight-shipments/{shipment['id']}").json()
    assert view["combined_quote"]["id"] == manual["freight_quote"]["id"]
    assert view["combined_quote"]["role"] == "combined"


# --------------------------------------------------------------------------- legacy history backfill


def test_legacy_backfill_records_history_once(tmp_path):
    repo = make_repo(tmp_path)
    seed_catalog(repo)

    def legacy_plan(name: str, *, references: list[str] | None = None, purpose: str = "production"):
        request = PackingRequest(shipment_name=name, save_result=False,
                                 lines=[PackingSlipLine(source_ref="old.pdf", original_sku="B30", quantity=1)])
        result = build_shipment(request, repo)
        result.purpose = purpose
        if references is not None:
            from app.models import ShippingQuoteInfo

            result.shipping_info = ShippingQuoteInfo(customer_references=references)
        repo.save_shipment(request.model_dump(mode="json"), result)  # 2.x storage: no quote recorded
        return result

    older = legacy_plan("SO77154 + 68606")
    newer = legacy_plan("SO77154 68606 revised")
    referenced = legacy_plan("whatever name", references=["SO77999"])
    legacy_plan("SO70000", purpose="prototype")
    from app.workflow_models import ManualQuoteInput

    manual = repo.save_manual_quote(ManualQuoteInput(
        shipment_name="66557 measured", pallets=[{"length": 48, "width": 40, "height": 50, "weight": 500}],
        shipping_info={}))

    assert legacy_backfill(repo) == {"created": 4}
    assert legacy_backfill(repo) == {"created": 0}

    quotes = {q.packing_result_id or q.manual_quote_id: q for q in repo.list_quotes()}
    assert len(quotes) == 4 and all(q.legacy for q in quotes.values())
    numbers = {oid: number for oid, number in repo.order_numbers(
        oid for q in quotes.values() for oid in q.order_ids).items()}
    assert sorted(numbers[o] for o in quotes[older.id].order_ids) == ["INV68606", "SO77154"]
    assert quotes[older.id].status == "superseded" and quotes[newer.id].status == "final"
    assert set(quotes[older.id].order_ids) == set(quotes[newer.id].order_ids)
    assert [numbers[o] for o in quotes[referenced.id].order_ids] == ["SO77999"]
    assert [numbers[o] for o in quotes[manual.id].order_ids] == ["INV66557"]
    assert quotes[manual.id].kind == "measured"
    # Quote numbers follow history (oldest first) and orders are reference-only placeholders.
    assert quotes[older.id].number < quotes[newer.id].number
    assert {o["status"] for o in repo.list_orders()} == {"reference_only"}
    assert len(repo.list_orders()) == 4


def test_shipment_update_keeps_nested_fields_typed(api, orders):
    from app.freight_models import FreightShipmentInput
    from app.models import AddressInfo
    from app.services.freight import update_shipment

    a, _, _ = orders
    created = api.post("/api/freight-shipments", json={"order_ids": [a["id"]]}).json()
    updated = update_shipment(api.repo, created["id"], FreightShipmentInput(
        name="Renamed", carrier="Estes", destination={"postal_code": "87505", "city": "Santa Fe"}))
    assert isinstance(updated.destination, AddressInfo) and updated.destination.postal_code == "87505"
    history = api.get(f"/api/freight-shipments/{created['id']}").json()["history"]
    assert history[-1]["action"] == "updated"
    assert set(history[-1]["details"]["changes"]) == {"name", "carrier", "destination"}
