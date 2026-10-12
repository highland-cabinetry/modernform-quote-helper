"""Shipment expenses and their allocation across orders, reconciled to the cent."""

from __future__ import annotations

import random

import pytest
from app.services.freight import split_cents
from fixtures_freight import catalog_item, make_order, pack

# --------------------------------------------------------------------------- split_cents


def amounts(total: int, basis: dict[str, float]) -> list[int]:
    return [line.amount_cents for line in split_cents(total, basis)]


def test_three_way_split_of_one_hundred_dollars_is_exact_and_deterministic():
    lines = split_cents(10000, {"a": 1, "b": 1, "c": 1})
    assert [line.amount_cents for line in lines] == [3334, 3333, 3333]
    assert [line.rounding_adjustment_cents for line in lines] == [1, 0, 0]
    assert [line.share for line in lines] == [pytest.approx(1 / 3, abs=1e-6)] * 3
    assert amounts(10000, {"c": 1, "a": 1, "b": 1}) == [3334, 3333, 3333]  # earlier order gets the tie


def test_largest_remainder_goes_to_the_largest_fraction():
    # raw: 33.3, 66.6, 0.1 cents of 100 -> floors 33, 66, 0; one leftover cent to the 0.6 remainder.
    assert amounts(100, {"a": 333, "b": 666, "c": 1}) == [33, 67, 0]
    assert amounts(1, {"a": 1, "b": 1}) == [1, 0]
    assert amounts(5, {"a": 0, "b": 2}) == [0, 5]


def test_negative_credit_is_split_by_magnitude():
    lines = split_cents(-10000, {"a": 1, "b": 1, "c": 1})
    assert [line.amount_cents for line in lines] == [-3334, -3333, -3333]
    assert [line.rounding_adjustment_cents for line in lines] == [-1, 0, 0]
    assert sum(line.amount_cents for line in lines) == -10000


@pytest.mark.parametrize("basis", [{}, {"a": 0, "b": 0}, {"a": 2, "b": -1}])
def test_invalid_basis_is_rejected(basis):
    with pytest.raises(ValueError, match="basis"):
        split_cents(10000, basis)


def test_random_totals_and_bases_always_reconcile_within_one_cent():
    rng = random.Random(20261009)
    for _ in range(3000):
        size = rng.randint(1, 12)
        basis = {f"o{i}": rng.choice([0, 0.5, 1, rng.uniform(0, 5000), rng.randint(1, 40)]) for i in range(size)}
        if sum(basis.values()) <= 0:
            basis["o0"] = 1.0
        total = rng.choice([rng.randint(-5_000_000, 5_000_000), rng.randint(-50, 50), 1, -1])
        lines = split_cents(total, basis)
        assert sum(line.amount_cents for line in lines) == total
        weight_total = sum(basis.values())
        for line in lines:
            raw = total * basis[line.order_id] / weight_total
            assert abs(line.amount_cents - raw) < 1
            assert line.amount_cents == 0 or (line.amount_cents > 0) == (total > 0)
        assert [line.order_id for line in lines] == list(basis)


# --------------------------------------------------------------------------- allocation through the API


@pytest.fixture
def shipment(api):
    api.repo.catalog_upsert("default", [catalog_item("W3030", weight=25, height=3)])
    a = make_order(api, "SO90001", ("B30", 2))  # 80 lb
    b = make_order(api, "SO90002", ("W3030", 1), ("B18", 1))  # 65 lb
    c = make_order(api, "SO90003", ("DB24", 1))  # 40 lb
    created = api.post("/api/freight-shipments", json={"order_ids": [a["id"], b["id"], c["id"]]})
    assert created.status_code == 200
    return created.json(), (a, b, c)


def add_expense(api, shipment_id: str, cents: int, **extra) -> dict:
    response = api.post(f"/api/freight-shipments/{shipment_id}/expenses", json={"amount_cents": cents, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def allocate(api, shipment_id: str, method: str, **extra):
    return api.post(f"/api/freight-shipments/{shipment_id}/allocation", json={"method": method, **extra})


def allocation_cents(view: dict) -> dict[str, int]:
    return {line["order_id"]: line["amount_cents"] for line in view["allocation"]["lines"]}


def test_expenses_belong_to_the_shipment_and_validate(api, shipment):
    view, (a, _, _) = shipment
    sid = view["id"]
    assert allocate(api, sid, "equal").status_code == 422  # nothing to allocate yet
    for bad in ({"amount_cents": 0}, {"amount_cents": -500}, {"amount_cents": -500, "kind": "fuel"}):
        assert api.post(f"/api/freight-shipments/{sid}/expenses", json=bad).status_code == 422
    add_expense(api, sid, 85000, vendor="Estes", invoice_reference="PRO-1")
    after = add_expense(api, sid, 4999, kind="fuel")
    after = add_expense(api, sid, -1000, kind="adjustment", notes="carrier credit")
    assert after["expense_total_cents"] == 88999
    assert len(after["expenses"]) == 3 and {e["shipment_id"] for e in after["expenses"]} == {sid}
    assert [e["action"] for e in after["history"]].count("expense_recorded") == 3
    # The order sees the shipment's total, not a per-order copy of the expense.
    trace = api.get(f"/api/orders/{a['id']}").json()
    assert trace["shipment"]["expense_total_cents"] == 88999 and trace["shipment"]["allocated_cents"] is None


def test_equal_allocation_reconciles_to_the_cent(api, shipment):
    view, orders = shipment
    add_expense(api, view["id"], 100001)
    result = allocate(api, view["id"], "equal").json()
    cents = allocation_cents(result)
    assert list(cents.values()) == [33334, 33334, 33333]
    assert sum(cents.values()) == result["expense_total_cents"] == result["allocation"]["total_cents"]
    assert result["allocation_stale"] is False
    trace = api.get(f"/api/orders/{orders[2]['id']}").json()
    assert trace["shipment"]["allocated_cents"] == 33333 and trace["shipment"]["allocation_method"] == "equal"


def test_weight_and_pallet_allocation_use_the_combined_plan(api, shipment):
    view, (a, b, c) = shipment
    _, combined = pack(api, [a["id"], b["id"], c["id"]], freight_shipment_id=view["id"])
    add_expense(api, view["id"], 92500)
    by_weight = allocate(api, view["id"], "weight").json()
    weights = combined["summary"]["order_weights"]
    assert weights == {a["id"]: 80.0, b["id"]: 65.0, c["id"]: 40.0}
    assert allocation_cents(by_weight) == {a["id"]: 40000, b["id"]: 32500, c["id"]: 20000}
    assert by_weight["allocation"]["basis_source"].startswith(f"Combined plan {combined['number']}")

    by_pallets = allocate(api, view["id"], "pallets").json()
    shares = combined["summary"]["order_pallet_shares"]
    assert [line["basis"] for line in by_pallets["allocation"]["lines"]] == [
        pytest.approx(shares[a["id"]]), pytest.approx(shares[b["id"]]), pytest.approx(shares[c["id"]])]
    assert sum(allocation_cents(by_pallets).values()) == 92500


def test_weight_allocation_falls_back_to_individual_quotes(api, shipment):
    view, (a, b, c) = shipment
    add_expense(api, view["id"], 10000)
    pack(api, [a["id"]])
    pack(api, [b["id"]])
    missing = allocate(api, view["id"], "weight")
    assert missing.status_code == 422 and "SO90003" in missing.json()["detail"]
    pack(api, [c["id"]])
    result = allocate(api, view["id"], "weight").json()
    assert result["allocation"]["basis_source"].startswith("Individual quotes")
    assert [line["basis"] for line in result["allocation"]["lines"]] == [80.0, 65.0, 40.0]
    assert sum(allocation_cents(result).values()) == 10000
    pallets = allocate(api, view["id"], "pallets").json()
    assert [line["basis"] for line in pallets["allocation"]["lines"]] == [1, 1, 1]


def test_manual_allocation_must_cover_every_order_and_reconcile(api, shipment):
    view, (a, b, c) = shipment
    add_expense(api, view["id"], 30000)
    short = allocate(api, view["id"], "manual", manual_cents={a["id"]: 10000, b["id"]: 10000, c["id"]: 9999})
    assert short.status_code == 422 and "off by 0.01" in short.json()["detail"]
    partial = allocate(api, view["id"], "manual", manual_cents={a["id"]: 30000})
    assert partial.status_code == 422
    good = allocate(api, view["id"], "manual", manual_cents={a["id"]: 15000, b["id"]: 15000, c["id"]: 0}).json()
    assert allocation_cents(good) == {a["id"]: 15000, b["id"]: 15000, c["id"]: 0}
    assert good["allocation"]["basis_source"] == "Entered by hand"
    assert good["history"][-1]["action"] == "allocated"


def test_voided_expense_changes_the_total_and_clears_the_allocation(api, shipment):
    view, _ = shipment
    sid = view["id"]
    add_expense(api, sid, 60000)
    extra = add_expense(api, sid, 3000, kind="accessorial")["expenses"][-1]
    allocate(api, sid, "equal")
    assert api.post(f"/api/freight-shipments/{sid}/expenses/{extra['id']}/void", json={"reason": ""}).status_code == 422
    voided = api.post(f"/api/freight-shipments/{sid}/expenses/{extra['id']}/void", json={"reason": "billed twice"})
    after = voided.json()
    assert after["expense_total_cents"] == 60000
    # A split of the old total is never shown as current: it is cleared and must be recalculated.
    assert after["allocation"] is None and after["allocation_stale"] is False
    assert "allocation_cleared" in [e["action"] for e in after["history"]]
    assert next(e for e in after["expenses"] if e["id"] == extra["id"])["void_reason"] == "billed twice"
    recalculated = allocate(api, sid, "equal").json()
    assert recalculated["allocation_stale"] is False
    assert sum(allocation_cents(recalculated).values()) == 60000


def test_changing_orders_clears_the_allocation(api, shipment):
    view, (a, _, _) = shipment
    sid = view["id"]
    add_expense(api, sid, 45000)
    allocate(api, sid, "equal")
    extra = make_order(api, "SO90004", ("B18", 1))
    changed = api.post(f"/api/freight-shipments/{sid}/orders", json={"add": [extra["id"]], "remove": [a["id"]]}).json()
    assert changed["allocation"] is None and changed["allocation_stale"] is False
    assert [e["action"] for e in changed["history"]][-2:] == ["orders_changed", "allocation_cleared"]
    assert changed["expense_total_cents"] == 45000  # the money stays with the shipment
    assert api.get(f"/api/orders/{a['id']}").json()["shipment"] is None
    assert api.post(f"/api/freight-shipments/{sid}/orders", json={"remove": changed["order_ids"]}).status_code == 409


def test_freight_records_compare_estimate_and_actual(api, shipment):
    view, (a, b, c) = shipment
    _, combined = pack(api, [a["id"], b["id"], c["id"]], freight_shipment_id=view["id"])
    api.put(f"/api/quotes/{combined['id']}", json={"cost": {"amount_cents": 90000, "carrier": "Estes"}})
    add_expense(api, view["id"], 100001)
    allocate(api, view["id"], "weight")
    [record] = api.get("/api/freight-records").json()
    assert record["number"] == view["number"]
    assert (record["estimate_cents"], record["actual_cents"], record["variance_cents"]) == (90000, 100001, 10001)
    assert record["allocated"] is True and record["orders"] == ["SO90001", "SO90002", "SO90003"]
    assert record["pallets"] == combined["summary"]["pallets"]
    exported = api.get("/api/freight-records/export.csv").content.decode("utf-8-sig").splitlines()
    assert exported[0].startswith("number,name,status")
    assert ",900.00," in exported[1] and ",1000.01," in exported[1] and ",100.01," in exported[1]
