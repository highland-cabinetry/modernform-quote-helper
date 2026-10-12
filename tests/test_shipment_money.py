"""3.2 shipment money and measurements: actuals, comparison, customer charge source, extra revenue,
bills without a shipment, and the extended Freight Data records."""

from __future__ import annotations

import pytest
from app.freight_models import ActualMeasurements, PalletMeasurement
from app.services.comparison import actual_weight, margin_percent, profit, weight_over
from fixtures_freight import make_order, pack

FIELDS = ("name", "status", "carrier", "broker", "pro_number", "bol_number", "pickup_date", "delivery_date",
          "destination", "customer_charge_cents", "notes", "purpose", "expected_delivery_date", "charge_source",
          "rep_charge_cents", "backcharge_cents")


def payload(view: dict, **changes) -> dict:
    return {**{key: view[key] for key in FIELDS}, **changes}


@pytest.fixture
def quoted_shipment(api):
    """An approved quote (carrier $400, customer $500) whose shipment was created by the approval."""

    a = make_order(api, "SO82001", ("B30", 2))
    b = make_order(api, "SO82002", ("B18", 1))
    _, quote = pack(api, [a["id"], b["id"]])
    api.post(f"/api/quotes/{quote['id']}/options", json={"price_cents": 40000, "carrier": "Estes", "select": True})
    api.put(f"/api/quotes/{quote['id']}/customer-price", json={"cents": 50000})
    body = api.post(f"/api/quotes/{quote['id']}/approve", json={}).json()
    return body["shipment"], quote, (a, b)


# --------------------------------------------------------------------------- comparison helpers


def test_weight_over_is_more_than_five_percent_or_one_hundred_pounds():
    assert weight_over(1000, 1050) is False  # exactly 5 %, 50 lb
    assert weight_over(1000, 1051) is True  # > 5 %
    assert weight_over(4000, 4100) is False  # 2.5 %, exactly 100 lb
    assert weight_over(4000, 4101) is True  # > 100 lb
    assert weight_over(1000, 900) is False and weight_over(None, 5000) is False and weight_over(1000, None) is False


def test_profit_and_margin_rules():
    assert profit(50000, 41000, 40000) == (9000, False)
    assert profit(50000, None, 40000) == (10000, True)
    assert profit(50000, None, None) == (None, False)
    assert profit(None, 100, 100) == (None, False)
    assert margin_percent(9000, 50000) == 18.0
    assert margin_percent(-100, 0) is None and margin_percent(None, 100) is None
    measured = ActualMeasurements(pallets=[PalletMeasurement(length=48, width=40, height=50, weight=500, quantity=2)],
                                  billed_weight=1200)
    assert actual_weight(measured) == 1000
    assert actual_weight(ActualMeasurements(billed_weight=1200)) == 1200
    assert actual_weight(None) is None


# --------------------------------------------------------------------------- actuals and comparison


def test_actual_measurements_and_comparison_flags(api, quoted_shipment):
    shipment, quote, _ = quoted_shipment
    sid = shipment["id"]
    comparison = shipment["comparison"]
    quoted_weight = comparison["quoted"]["total_weight"]
    assert comparison["quoted"]["pallets"] == quote["summary"]["pallets"] and comparison["quoted"]["rows"]
    assert comparison["actual"] is None and comparison["flags"] == {
        "weight_over": False, "pallets_changed": False, "cost_over": False}
    assert comparison["cost"] == {"expected_cents": 40000, "actual_cents": None, "variance_cents": None,
                                  "variance_percent": None}
    assert comparison["revenue"] == {"customer_charge_cents": 50000, "extra_cents": 0, "total_cents": 50000,
                                     "charge_source": "quote"}
    assert (comparison["profit_cents"], comparison["profit_estimated"], comparison["margin_percent"]) == (
        10000, True, 20.0)

    heavy = quoted_weight + 150
    recorded = api.put(f"/api/freight-shipments/{sid}/actual", json={
        "pallets": [{"length": 48, "width": 40, "height": 60, "weight": heavy / 2, "quantity": 2}],
        "measured_on": "2026-10-08", "source": "warehouse", "billed_weight": 900, "billed_class": "85",
        "notes": "re-wrapped"})
    assert recorded.status_code == 200, recorded.text
    view = recorded.json()
    assert view["actual"]["billed_class"] == "85"
    actual = view["comparison"]["actual"]
    assert actual["pallets"] == 2 and actual["total_weight"] == pytest.approx(heavy, abs=0.1)
    assert actual["billed_weight"] == 900 and actual["rows"][0]["quantity"] == 2
    assert view["comparison"]["weight_variance_lb"] == pytest.approx(150, abs=0.1)
    flags = view["comparison"]["flags"]
    assert flags["weight_over"] is True
    assert flags["pallets_changed"] is (quote["summary"]["pallets"] != 2)
    assert view["history"][-1]["action"] == "actuals_recorded" and "2 pallet(s)" in view["history"][-1]["summary"]

    api.post(f"/api/freight-shipments/{sid}/expenses", json={"amount_cents": 38000, "kind": "linehaul"})
    view = api.post(f"/api/freight-shipments/{sid}/expenses",
                    json={"amount_cents": 4500, "kind": "reweigh", "category": "Reweigh"}).json()
    cost = view["comparison"]["cost"]
    assert cost == {"expected_cents": 40000, "actual_cents": 42500, "variance_cents": 2500, "variance_percent": 6.2}
    assert view["comparison"]["flags"]["cost_over"] is True
    assert (view["comparison"]["profit_cents"], view["comparison"]["profit_estimated"]) == (7500, False)
    assert view["comparison"]["margin_percent"] == 15.0
    assert view["expenses"][-1]["category"] == "Reweigh"

    cleared = api.delete(f"/api/freight-shipments/{sid}/actual").json()
    assert cleared["actual"] is None and cleared["comparison"]["actual"] is None
    assert cleared["history"][-1]["action"] == "actuals_cleared"
    # Billed weight alone is enough to compare.
    billed = api.put(f"/api/freight-shipments/{sid}/actual", json={"billed_weight": quoted_weight + 20}).json()
    assert billed["comparison"]["actual"]["weight"] == pytest.approx(quoted_weight + 20)
    assert billed["comparison"]["flags"]["pallets_changed"] is False
    assert api.put(f"/api/freight-shipments/{sid}/actual", json={"pallets": [{"length": 0, "width": 40,
                                                                              "height": 40, "weight": 1}]}
                   ).status_code == 422
    assert api.put(f"/api/freight-shipments/{sid}/actual", json={"bogus": 1}).status_code == 422
    assert api.put("/api/freight-shipments/nope/actual", json={}).status_code == 404


def test_actuals_are_locked_once_closed(api, quoted_shipment):
    shipment, _, _ = quoted_shipment
    sid = shipment["id"]
    assert api.put(f"/api/freight-shipments/{sid}", json=payload(shipment, status="delivered")).status_code == 200
    assert api.put(f"/api/freight-shipments/{sid}/actual", json={"billed_weight": 500}).status_code == 200
    assert api.put(f"/api/freight-shipments/{sid}", json=payload(shipment, status="closed")).status_code == 200
    assert api.put(f"/api/freight-shipments/{sid}/actual", json={"billed_weight": 600}).status_code == 409
    assert api.delete(f"/api/freight-shipments/{sid}/actual").status_code == 409


# --------------------------------------------------------------------------- customer charge source


def test_a_plan_built_after_approval_keeps_the_approved_quote_as_reference(api, quoted_shipment):
    from datetime import UTC, datetime, timedelta

    from app.services.attention import attention

    shipment, quote, (a, b) = quoted_shipment
    sid = shipment["id"]
    _, final_plan = pack(api, [a["id"], b["id"]], freight_shipment_id=sid)
    view = api.get(f"/api/freight-shipments/{sid}").json()
    assert view["combined_quote"]["id"] == final_plan["id"]  # the warehouse's final plan
    assert view["customer_charge_cents"] == 50000  # the new plan has no price: the charge is kept
    comparison = view["comparison"]
    assert comparison["quoted"]["quote_id"] == quote["id"]  # what the customer accepted
    assert comparison["cost"]["expected_cents"] == 40000 and comparison["combined_estimate_cents"] is None
    [record] = api.get("/api/freight-records").json()
    assert record["quote_number"] == quote["number"] and record["expected_cost_cents"] == 40000
    later = datetime.now(UTC) + timedelta(days=2)
    items = attention(api.repo, None, now=later)["items"]
    assert [(i["kind"], i["number"]) for i in items] == [("approved_not_booked", shipment["number"])]


def test_customer_charge_follows_its_source(api, quoted_shipment):
    shipment, quote, _ = quoted_shipment
    sid = shipment["id"]
    assert shipment["customer_charge_cents"] == 50000

    rep = api.put(f"/api/freight-shipments/{sid}", json=payload(shipment, charge_source="rep_assigned"))
    assert rep.status_code == 422 and "rep" in rep.json()["detail"]
    rep = api.put(f"/api/freight-shipments/{sid}",
                  json=payload(shipment, charge_source="rep_assigned", rep_charge_cents=45000)).json()
    assert rep["customer_charge_cents"] == 45000
    # The quote no longer drives the charge.
    api.put(f"/api/quotes/{quote['id']}/customer-price", json={"cents": 52000})
    assert api.get(f"/api/freight-shipments/{sid}").json()["customer_charge_cents"] == 45000

    back = api.put(f"/api/freight-shipments/{sid}", json=payload(rep, charge_source="backcharge"))
    assert back.status_code == 422 and "backcharge" in back.json()["detail"]
    back = api.put(f"/api/freight-shipments/{sid}",
                   json=payload(rep, charge_source="backcharge", backcharge_cents=30000)).json()
    assert back["customer_charge_cents"] == 30000 and back["comparison"]["revenue"]["charge_source"] == "backcharge"

    manual = api.put(f"/api/freight-shipments/{sid}",
                     json=payload(back, charge_source="manual", customer_charge_cents=12345)).json()
    assert manual["customer_charge_cents"] == 12345
    none = api.put(f"/api/freight-shipments/{sid}", json=payload(manual, charge_source="none")).json()
    assert none["customer_charge_cents"] == 0
    assert none["comparison"]["profit_cents"] == -40000  # no revenue: the expected cost is the loss

    from_quote = api.put(f"/api/freight-shipments/{sid}",
                         json=payload(none, charge_source="quote", customer_charge_cents=1)).json()
    assert from_quote["customer_charge_cents"] == 52000  # the combined quote's current price wins
    changes = from_quote["history"][-1]["details"]["changes"]
    assert changes["customer_charge_cents"] == [0, 52000] and changes["charge_source"] == ["none", "quote"]


def test_new_shipment_validates_its_charge_source(api):
    a = make_order(api, "SO82101", ("B30", 1))
    bad = api.post("/api/freight-shipments", json={"order_ids": [a["id"]], "charge_source": "backcharge"})
    assert bad.status_code == 422
    good = api.post("/api/freight-shipments", json={"order_ids": [a["id"]], "charge_source": "backcharge",
                                                    "backcharge_cents": 7500, "purpose": "warranty",
                                                    "expected_delivery_date": "2026-10-20"}).json()
    assert good["customer_charge_cents"] == 7500 and good["purpose"] == "warranty"
    assert good["expected_delivery_date"] == "2026-10-20"


def test_linking_a_priced_quote_sets_the_charge_only_for_quote_source(api):
    a = make_order(api, "SO82201", ("B30", 1))
    b = make_order(api, "SO82202", ("B30", 1))
    manual_ship = api.post("/api/freight-shipments", json={"order_ids": [a["id"]], "charge_source": "manual",
                                                           "customer_charge_cents": 9900}).json()
    quote_ship = api.post("/api/freight-shipments", json={"order_ids": [b["id"]], "customer_charge_cents": 100}).json()
    for shipment, order in ((manual_ship, a), (quote_ship, b)):
        _, quote = pack(api, [order["id"]])
        api.put(f"/api/quotes/{quote['id']}/customer-price", json={"cents": 25000})
        api.post(f"/api/freight-shipments/{shipment['id']}/combined-quote", json={"quote_id": quote["id"]})
    assert api.get(f"/api/freight-shipments/{manual_ship['id']}").json()["customer_charge_cents"] == 9900
    linked = api.get(f"/api/freight-shipments/{quote_ship['id']}").json()
    assert linked["customer_charge_cents"] == 25000
    assert linked["history"][-1]["details"]["changes"] == {"customer_charge_cents": [100, 25000]}
    # Through the 3.0 quote editor too.
    api.put(f"/api/quotes/{linked['combined_quote']['id']}", json={"cost": {"customer_price_cents": 26000}})
    assert api.get(f"/api/freight-shipments/{quote_ship['id']}").json()["customer_charge_cents"] == 26000


def test_closed_shipment_keeps_its_charge_when_the_quote_changes(api, quoted_shipment):
    shipment, quote, _ = quoted_shipment
    sid = shipment["id"]
    api.put(f"/api/freight-shipments/{sid}", json=payload(shipment, status="closed"))
    corrected = api.put(f"/api/quotes/{quote['id']}/customer-price", json={"cents": 61000})
    assert corrected.status_code == 200
    assert api.get(f"/api/freight-shipments/{sid}").json()["customer_charge_cents"] == 50000
    reopened = api.put(f"/api/freight-shipments/{sid}", params={"reason": "rebill"},
                       json=payload(shipment, status="delivered"))
    assert reopened.json()["customer_charge_cents"] == 61000


# --------------------------------------------------------------------------- extra revenue


def test_extra_revenue_add_list_and_void(api, quoted_shipment):
    shipment, _, _ = quoted_shipment
    sid = shipment["id"]
    view = api.post(f"/api/freight-shipments/{sid}/revenue", json={
        "amount_cents": 7500, "kind": "accessorial_rebill", "payer": "Acme", "reference": "INV-RB1",
        "received_on": "2026-10-09"}).json()
    assert view["revenue_total_cents"] == 7500 and view["revenue"][0]["payer"] == "Acme"
    assert view["comparison"]["revenue"]["total_cents"] == 57500
    assert view["history"][-1]["action"] == "revenue_recorded"
    listed = api.get(f"/api/freight-shipments/{sid}/revenue").json()
    assert [r["amount_cents"] for r in listed] == [7500]
    rid = listed[0]["id"]
    assert api.post(f"/api/freight-shipments/{sid}/revenue/{rid}/void", json={"reason": ""}).status_code == 422
    voided = api.post(f"/api/freight-shipments/{sid}/revenue/{rid}/void", json={"reason": "duplicate"}).json()
    assert voided["revenue_total_cents"] == 0 and voided["revenue"][0]["voided"] is True
    assert voided["history"][-1]["action"] == "revenue_voided"
    assert api.post(f"/api/freight-shipments/{sid}/revenue", json={"amount_cents": 0}).status_code == 422
    assert api.post(f"/api/freight-shipments/{sid}/revenue", json={"amount_cents": 5, "kind": "x"}).status_code == 422
    assert api.get("/api/freight-shipments/nope/revenue").status_code == 404
    assert api.post(f"/api/freight-shipments/nope/revenue/{rid}/void", json={"reason": "x"}).status_code == 404

    api.put(f"/api/freight-shipments/{sid}", json=payload(shipment, status="closed"))
    assert api.post(f"/api/freight-shipments/{sid}/revenue", json={"amount_cents": 5}).status_code == 409


def test_unassigned_revenue_can_be_assigned_later(api, quoted_shipment):
    shipment, _, _ = quoted_shipment
    claim = api.post("/api/revenue", json={"amount_cents": 12000, "kind": "claim_recovery", "payer": "Estes"})
    assert claim.status_code == 200, claim.text
    claim = claim.json()
    assert claim["shipment_id"] is None
    assert [r["id"] for r in api.get("/api/revenue").json()] == [claim["id"]]
    view = api.post(f"/api/revenue/{claim['id']}/assign", json={"shipment_id": shipment["id"]}).json()
    assert view["revenue_total_cents"] == 12000 and view["history"][-1]["action"] == "revenue_assigned"
    assert api.get("/api/revenue").json() == []
    assert api.post(f"/api/revenue/{claim['id']}/assign", json={"shipment_id": shipment["id"]}).status_code == 409
    direct = api.post("/api/revenue", json={"amount_cents": 500, "shipment_id": shipment["id"]}).json()
    assert direct["shipment_id"] == shipment["id"]
    other = api.post("/api/revenue", json={"amount_cents": 100}).json()
    assert api.post(f"/api/revenue/{other['id']}/void", json={"reason": "typo"}).json()["voided"] is True
    assert api.post(f"/api/revenue/{other['id']}/assign", json={"shipment_id": shipment["id"]}).status_code == 409
    assert [r["id"] for r in api.get("/api/revenue", params={"include_voided": True}).json()] == [other["id"]]
    assert api.post("/api/revenue/nope/void", json={"reason": "x"}).status_code == 404


# --------------------------------------------------------------------------- bills without a shipment


def test_unassigned_expense_create_assign_and_void(api, quoted_shipment):
    shipment, _, _ = quoted_shipment
    sid = shipment["id"]
    api.post(f"/api/freight-shipments/{sid}/expenses", json={"amount_cents": 40000})
    api.post(f"/api/freight-shipments/{sid}/allocation", json={"method": "equal"})
    bill = api.post("/api/unassigned-expenses", json={"amount_cents": 8800, "kind": "storage", "vendor": "3PL",
                                                     "invoice_reference": "W-77", "category": "Storage"})
    assert bill.status_code == 200, bill.text
    bill = bill.json()
    assert bill["company_id"] and bill["voided"] is False
    assert [e["id"] for e in api.get("/api/unassigned-expenses").json()] == [bill["id"]]
    assert api.post("/api/unassigned-expenses", json={"amount_cents": -5}).status_code == 422
    assert api.post("/api/unassigned-expenses", json={"amount_cents": -5, "kind": "adjustment"}).status_code == 200

    moved = api.post(f"/api/unassigned-expenses/{bill['id']}/assign", json={"shipment_id": sid})
    assert moved.status_code == 200, moved.text
    view = moved.json()
    expense = next(e for e in view["expenses"] if e["id"] == bill["id"])
    assert (expense["amount_cents"], expense["kind"], expense["vendor"], expense["category"]) == (
        8800, "storage", "3PL", "Storage")
    assert expense["created_at"] == bill["created_at"]
    assert view["expense_total_cents"] == 48800 and view["allocation"] is None
    actions = [e["action"] for e in view["history"]]
    assert actions[-2:] == ["allocation_cleared", "expense_assigned"]
    assert bill["id"] not in [e["id"] for e in api.get("/api/unassigned-expenses").json()]
    assert api.post(f"/api/unassigned-expenses/{bill['id']}/assign", json={"shipment_id": sid}).status_code == 404
    trail = api.get("/api/audit", params={"entity_type": "unassigned_expense", "entity_id": bill["id"]}).json()
    assert [e["action"] for e in trail] == ["recorded", "assigned"]

    second = api.post("/api/unassigned-expenses", json={"amount_cents": 100}).json()
    assert api.post(f"/api/unassigned-expenses/{second['id']}/void", json={"reason": " "}).status_code == 409
    voided = api.post(f"/api/unassigned-expenses/{second['id']}/void", json={"reason": "not ours"}).json()
    assert voided["voided"] is True and voided["void_reason"] == "not ours"
    assert api.post(f"/api/unassigned-expenses/{second['id']}/assign", json={"shipment_id": sid}).status_code == 409
    listed = api.get("/api/unassigned-expenses", params={"include_voided": True}).json()
    assert second["id"] in [e["id"] for e in listed]
    assert second["id"] not in [e["id"] for e in api.get("/api/unassigned-expenses").json()]
    assert api.post("/api/unassigned-expenses/nope/void", json={"reason": "x"}).status_code == 404

    third = api.post("/api/unassigned-expenses", json={"amount_cents": 100}).json()
    assert api.post(f"/api/unassigned-expenses/{third['id']}/assign", json={"shipment_id": "nope"}).status_code == 404
    api.put(f"/api/freight-shipments/{sid}", json=payload(shipment, status="closed"))
    assert api.post(f"/api/unassigned-expenses/{third['id']}/assign", json={"shipment_id": sid}).status_code == 409


def test_bills_and_revenue_stay_within_their_company(api, quoted_shipment):
    from fixtures_freight import acme_company

    shipment, _, _ = quoted_shipment
    acme = acme_company(api.repo)
    bill = api.post("/api/unassigned-expenses", json={"amount_cents": 100, "company_id": acme.code}).json()
    assert bill["company_id"] == acme.id
    assert api.post(f"/api/unassigned-expenses/{bill['id']}/assign",
                    json={"shipment_id": shipment["id"]}).status_code == 409
    claim = api.post("/api/revenue", json={"amount_cents": 100, "company_id": acme.id}).json()
    assert api.post(f"/api/revenue/{claim['id']}/assign", json={"shipment_id": shipment["id"]}).status_code == 409
    assert api.post("/api/revenue", json={"amount_cents": 100, "company_id": acme.id,
                                          "shipment_id": shipment["id"]}).status_code == 409
    assert [e["id"] for e in api.get("/api/unassigned-expenses", params={"company_id": "ACME"}).json()] == [bill["id"]]
    assert api.get("/api/unassigned-expenses", params={"company_id": "HCI"}).json() == []


# --------------------------------------------------------------------------- freight records


def test_freight_records_include_revenue_cost_margin_and_weights(api, quoted_shipment):
    shipment, quote, _ = quoted_shipment
    sid = shipment["id"]
    api.post(f"/api/freight-shipments/{sid}/expenses", json={"amount_cents": 41000})
    api.post(f"/api/freight-shipments/{sid}/revenue", json={"amount_cents": 2000})
    api.put(f"/api/freight-shipments/{sid}/actual", json={"billed_weight": 2000})
    [record] = api.get("/api/freight-records").json()
    assert (record["estimate_cents"], record["actual_cents"], record["variance_cents"]) == (40000, 41000, 1000)
    assert (record["customer_charge_cents"], record["extra_revenue_cents"], record["revenue_cents"]) == (
        50000, 2000, 52000)
    assert (record["cost_cents"], record["net_cents"], record["net_estimated"]) == (41000, 11000, False)
    assert record["margin_percent"] == pytest.approx(21.2)
    assert record["purpose"] == "standard" and record["charge_source"] == "quote"
    assert record["quote_number"] == quote["number"] and record["quote_status"] == "approved"
    assert record["weight"] == quote["summary"]["total_weight"] and record["actual_weight"] == 2000
    assert record["weight_over"] is True and record["orders"] == ["SO82001", "SO82002"]
    rows = api.get("/api/freight-records/export.csv").content.decode("utf-8-sig").splitlines()
    assert rows[0].startswith("number,name,status,purpose") and "net" in rows[0].split(",")
    assert ",520.00,410.00,110.00,21.2," in rows[1]
    assert api.get("/api/freight-records", params={"company_id": "HCI"}).json()[0]["id"] == sid
