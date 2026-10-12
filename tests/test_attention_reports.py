"""3.2 needs-attention list and profit reports."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from app.freight_models import ActualMeasurements, FreightShipment, PalletMeasurement, Quote, ShipmentExpense
from app.services.attention import attention
from fixtures_freight import acme_company, make_order, pack

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)
TODAY = NOW.date()


def write_shipment(repo, shipment: FreightShipment, expenses: list[int] = ()) -> FreightShipment:
    with repo.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repo.write_freight_shipment(connection, shipment, action="created", summary="test")
        for cents in expenses:
            repo.write_expense(connection, ShipmentExpense(shipment_id=shipment.id, amount_cents=cents))
    return shipment


def kinds(result: dict) -> list[tuple[str, str]]:
    return [(item["kind"], item["number"]) for item in result["items"]]


@pytest.fixture
def company_id(api):
    return api.repo.default_company().id


def test_quote_attention(api, company_id):
    a = make_order(api, "SO84001", ("B30", 1))
    repo = api.repo
    _, recent = pack(api, [a["id"]])
    _, old = pack(api, [a["id"]], shipment_name="other")
    stored = {q.id: q for q in repo.list_quotes()}
    # Sent 4 days ago (warn) and 20 days ago (danger); a sent-today quote is not reported.
    for quote_id, days in ((recent["id"], 4), (old["id"], 20)):
        quote = stored[quote_id]
        quote.status = "final"
        quote.customer_status = "sent"
        quote.sent_at = NOW - timedelta(days=days)
        repo.save_quote(quote, action="sent")
    fresh = Quote(company_id=company_id, kind="external", order_ids=[a["id"]], customer_status="sent", sent_at=NOW)
    expiring = Quote(company_id=company_id, kind="external", order_ids=[a["id"]], created_at=NOW - timedelta(days=1))
    expiring.cost.expires_on = TODAY + timedelta(days=2)
    expiring.cost.amount_cents = 30000
    later = Quote(company_id=company_id, kind="external", order_ids=[a["id"]])
    later.cost.expires_on = TODAY + timedelta(days=3)
    gone = Quote(company_id=company_id, kind="external", order_ids=[a["id"]], status="superseded",
                 customer_status="sent", sent_at=NOW - timedelta(days=30))
    for quote in (fresh, expiring, later, gone):
        repo.save_quote(quote)
    result = attention(repo, company_id, now=NOW)
    assert kinds(result) == [("quote_unanswered", old["number"]), ("quote_unanswered", recent["number"]),
                             ("quote_expiring", expiring.number)]
    danger, warn, expiry = result["items"]
    assert (danger["severity"], danger["age_days"], danger["entity_type"]) == ("danger", 20, "quote")
    assert "expired" in danger["detail"] and "SO84001" in danger["detail"]
    assert (warn["severity"], warn["age_days"]) == ("warn", 4)
    assert expiry["title"].endswith("in 2 days") and "$300.00" in expiry["detail"] and expiry["age_days"] == 1
    assert result["counts"]["quote_unanswered"] == 2 and result["total"] == 3


def test_shipment_attention(api, company_id):
    repo = api.repo
    orders = [make_order(api, f"SO8410{i}", ("B30", 1)) for i in range(9)]

    def shipment(index: int, status: str, **fields) -> FreightShipment:
        return FreightShipment(company_id=company_id, order_ids=[orders[index]["id"]], status=status, **fields)

    approved = Quote(company_id=company_id, kind="external", order_ids=[orders[0]["id"]], customer_status="approved",
                     responded_at=NOW - timedelta(days=2))
    approved.cost.amount_cents = 10000
    repo.save_quote(approved)
    not_booked = write_shipment(repo, shipment(0, "planning", combined_quote_id=approved.id))
    pickup = write_shipment(repo, shipment(1, "booked", pickup_date=TODAY + timedelta(days=1), carrier="Estes"))
    write_shipment(repo, shipment(2, "booked", pickup_date=TODAY + timedelta(days=3)))
    late = write_shipment(repo, shipment(3, "in_transit", expected_delivery_date=TODAY - timedelta(days=4),
                                         pro_number="P1"))
    no_bill = write_shipment(repo, shipment(4, "delivered", delivery_date=TODAY - timedelta(days=3)))
    quoted = Quote(company_id=company_id, kind="external", order_ids=[orders[5]["id"]])
    quoted.cost.amount_cents = 10000
    repo.save_quote(quoted)
    measured = ActualMeasurements(pallets=[PalletMeasurement(length=48, width=40, height=40, weight=300)])
    over = write_shipment(repo, shipment(5, "closed", combined_quote_id=quoted.id, actual=measured,
                                         delivery_date=TODAY - timedelta(days=1)), expenses=[10600])
    write_shipment(repo, shipment(6, "closed", delivery_date=TODAY - timedelta(days=200)))  # too old to nag
    write_shipment(repo, shipment(7, "delivered", external_source="freight_manager"))  # imported history
    write_shipment(repo, shipment(8, "cancelled"))
    api.post("/api/unassigned-expenses", json={"amount_cents": 4200})

    result = attention(repo, company_id, now=NOW)
    found = set(kinds(result))
    assert found == {
        ("approved_not_booked", not_booked.number), ("pickup_today", pickup.number),
        ("overdue_delivery", late.number), ("missing_cost", no_bill.number), ("missing_actuals", no_bill.number),
        ("cost_over_quote", over.number), ("unassigned_expense", ""),
    }
    by_kind = {item["kind"]: item for item in result["items"]}
    assert by_kind["overdue_delivery"]["severity"] == "danger" and by_kind["overdue_delivery"]["age_days"] == 4
    assert "PRO P1" in by_kind["overdue_delivery"]["detail"]
    assert by_kind["pickup_today"]["title"].endswith("tomorrow") and by_kind["pickup_today"]["severity"] == "info"
    assert by_kind["approved_not_booked"]["age_days"] == 2
    assert "+6.0%" in by_kind["cost_over_quote"]["detail"]
    assert "$42.00" in by_kind["unassigned_expense"]["detail"]
    assert [item["severity"] for item in result["items"]] == sorted(
        (item["severity"] for item in result["items"]), key=["danger", "warn", "info"].index)

    # The API uses the real clock; the dashboard carries the counts.
    listed = api.get("/api/attention", params={"company_id": "HCI"}).json()
    assert listed["counts"]["unassigned_expense"] == 1 and "items" in listed
    assert api.get("/api/dashboard").json()["attention"]["counts"]["unassigned_expense"] == 1
    assert api.get("/api/attention", params={"company_id": "nope"}).status_code == 404


def test_cost_within_five_percent_is_not_reported(api, company_id):
    repo = api.repo
    order = make_order(api, "SO84201", ("B30", 1))
    quote = Quote(company_id=company_id, kind="external", order_ids=[order["id"]])
    quote.cost.amount_cents = 10000
    repo.save_quote(quote)
    measured = ActualMeasurements(billed_weight=500)
    write_shipment(repo, FreightShipment(company_id=company_id, order_ids=[order["id"]], status="delivered",
                                         combined_quote_id=quote.id, actual=measured), expenses=[10500])
    assert attention(repo, company_id, now=NOW)["items"] == []


# --------------------------------------------------------------------------- reports


@pytest.fixture
def report_data(api, company_id):
    """Three shipments over two months, two reps and carriers, one sister shipment, one cancelled."""

    repo = api.repo
    rows = [
        # order, rep, customer, pickup, carrier, purpose, charge, expenses, extra revenue
        ("SO85001", "JD", "Acme", date(2026, 9, 5), "Estes", "standard", 50000, [40000], 2000),
        ("SO85002", "JD", "Acme", date(2026, 9, 20), "Saia", "back_order", 20000, [25000], 0),
        ("SO85003", "MK", "Desert Homes", date(2026, 10, 2), "Estes", "standard", 30000, [], 0),
        ("SO85004", "MK", "Sister Co", date(2026, 10, 3), "Estes", "sister", 0, [15000], 0),
        ("SO85005", "MK", "Acme", date(2026, 10, 4), "Estes", "standard", 99999, [1], 0),
    ]
    shipments = []
    for number, rep, customer, pickup, carrier, purpose, charge, expenses, extra in rows:
        created = api.post("/api/orders", json={"order_number": number, "sales_rep": rep,
                                                "customer_name": customer}).json()
        shipment = FreightShipment(company_id=company_id, order_ids=[created["id"]], status="delivered",
                                   pickup_date=pickup, carrier=carrier, purpose=purpose, customer_charge_cents=charge,
                                   charge_source="manual")
        write_shipment(repo, shipment, expenses)
        if extra:
            api.post(f"/api/freight-shipments/{shipment.id}/revenue", json={"amount_cents": extra})
        shipments.append((shipment, created))
    cancelled = shipments[-1][0]
    cancelled.status = "cancelled"
    write_shipment(repo, cancelled)
    # Quotes: two sent in September (one approved), one declined in October, one never sent, one void.
    # (18:00 UTC is the same calendar day on any US clock; reports group by the local day.)
    order_ids = [created["id"] for _, created in shipments]
    for created_at, status, customer_status, order_index in (
            (datetime(2026, 9, 2, 18, tzinfo=UTC), "final", "approved", 0),
            (datetime(2026, 9, 3, 18, tzinfo=UTC), "final", "sent", 1),
            (datetime(2026, 10, 1, 18, tzinfo=UTC), "final", "declined", 2),
            (datetime(2026, 10, 1, 18, tzinfo=UTC), "final", "not_sent", 2),
            (datetime(2026, 10, 1, 18, tzinfo=UTC), "void", "approved", 2),
            (datetime(2026, 10, 1, 18, tzinfo=UTC), "superseded", "sent", 2)):
        quote = Quote(company_id=company_id, kind="external", order_ids=[order_ids[order_index]], status=status,
                      customer_status=customer_status, created_at=created_at)
        quote.cost.carrier = "Estes"
        repo.save_quote(quote)
    api.post("/api/unassigned-expenses", json={"amount_cents": 3000, "incurred_on": "2026-10-05"})
    api.post("/api/revenue", json={"amount_cents": 1000, "received_on": "2026-08-01"})
    return shipments


def test_report_by_month(api, report_data):
    report = api.get("/api/reports/summary").json()
    assert report["group"] == "month" and [row["key"] for row in report["rows"]] == ["2026-09", "2026-10"]
    september, october = report["rows"]
    assert (september["shipments"], september["revenue_cents"], september["cost_cents"]) == (2, 72000, 65000)
    assert (september["customer_charge_cents"], september["extra_revenue_cents"]) == (70000, 2000)
    assert (september["net_cents"], september["margin_percent"], september["loss_shipments"]) == (7000, 9.7, 1)
    assert (september["quotes"], september["quotes_sent"], september["quotes_approved"]) == (2, 2, 1)
    assert september["conversion_percent"] == 50.0
    assert (october["shipments"], october["revenue_cents"], october["cost_cents"]) == (2, 30000, 15000)
    assert october["shipments_without_cost"] == 1 and october["loss_shipments"] == 1
    assert (october["quotes"], october["quotes_sent"], october["quotes_declined"]) == (2, 1, 1)
    assert october["conversion_percent"] == 0.0
    totals = report["totals"]
    assert (totals["shipments"], totals["revenue_cents"], totals["cost_cents"], totals["net_cents"]) == (
        4, 102000, 80000, 22000)
    assert (totals["unassigned_expense_cents"], totals["unassigned_revenue_cents"]) == (3000, 1000)
    assert totals["net_with_unassigned_cents"] == 20000


def test_report_filters_and_other_groups(api, report_data):
    no_sister = api.get("/api/reports/summary", params={"exclude_sister": True}).json()
    assert no_sister["totals"]["shipments"] == 3 and no_sister["totals"]["cost_cents"] == 65000
    october = api.get("/api/reports/summary", params={"from": "2026-10-01", "to": "2026-10-31"}).json()
    assert october["totals"]["shipments"] == 2 and october["totals"]["unassigned_revenue_cents"] == 0
    assert october["totals"]["unassigned_expense_cents"] == 3000
    by_rep = api.get("/api/reports/summary", params={"group": "rep"}).json()
    assert {row["key"]: row["shipments"] for row in by_rep["rows"]} == {"JD": 2, "MK": 2}
    by_carrier = api.get("/api/reports/summary", params={"group": "carrier"}).json()
    assert {row["key"]: (row["shipments"], row["quotes"]) for row in by_carrier["rows"]} == {
        "Estes": (3, 4), "Saia": (1, 0)}
    purposes = api.get("/api/reports/summary", params={"group": "purpose"}).json()["rows"]
    by_purpose = {row["key"]: row for row in purposes}
    assert set(by_purpose) == {"standard", "back_order", "sister"}
    assert by_purpose["back_order"]["net_cents"] == -5000
    by_customer = api.get("/api/reports/summary", params={"group": "customer"}).json()
    assert [row["key"] for row in by_customer["rows"]] == ["Acme", "Desert Homes", "Sister Co"]
    assert api.get("/api/reports/summary", params={"group": "zip"}).status_code == 422
    assert api.get("/api/reports/summary", params={"from": "2026-10-05", "to": "2026-10-01"}).status_code == 422
    assert api.get("/api/reports/summary", params={"from": "October"}).status_code == 422


def test_report_csv(api, report_data):
    response = api.get("/api/reports/summary.csv", params={"group": "rep"})
    assert response.status_code == 200 and "freight-profit-by-rep.csv" in response.headers["content-disposition"]
    lines = response.content.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("rep,shipments,revenue,customer_charges,extra_revenue,cost,net,margin_percent")
    assert lines[1].startswith("JD,2,720.00,700.00,20.00,650.00,70.00,9.7,1,0,2,2,1,0,50.0")
    assert lines[3].startswith("total,4,1020.00")
    assert lines[-1].startswith("net incl. unassigned")


def test_report_scoped_to_a_company(api, report_data):
    acme = acme_company(api.repo)
    assert api.get("/api/reports/summary", params={"company_id": acme.id}).json()["rows"] == []
    assert api.get("/api/reports/summary", params={"company_id": "HCI"}).json()["totals"]["shipments"] == 4


def test_imported_quotes_count_and_old_unanswered_ones_count_as_declined(api, company_id):
    """Freight Manager quotes are legacy records but real quotes: they are reported, and one still
    'sent' 30+ days later counts as declined, like the workbook's own dashboard."""
    from datetime import timedelta

    from app.services.profit_reports import summary

    orders = [api.post("/api/orders", json={"order_number": f"SO8600{index}"}).json() for index in range(3)]
    long_ago = datetime.now(UTC) - timedelta(days=40)
    recent = datetime.now(UTC) - timedelta(days=2)
    for order, created, customer_status, source in ((orders[0], long_ago, "sent", "freight_manager"),
                                                    (orders[1], recent, "sent", "freight_manager"),
                                                    (orders[2], long_ago, "approved", "")):
        quote = Quote(company_id=company_id, kind="external", order_ids=[order["id"]], legacy=True,
                      customer_status=customer_status, created_at=created, external_source=source)
        api.repo.save_quote(quote)
    totals = summary(api.repo, company_id=company_id)["totals"]
    # The 2.x history backfill (legacy without a source) stays out; both imported quotes count.
    assert (totals["quotes"], totals["quotes_sent"], totals["quotes_declined"]) == (2, 2, 1)


def test_imported_in_progress_shipments_are_not_flagged_as_unbooked(api, company_id):
    repo = api.repo
    for source in ("freight_manager", ""):
        order = make_order(api, f"SO8700{len(source)}", ("B30", 1))
        quote = Quote(company_id=company_id, kind="external", order_ids=[order["id"]], customer_status="approved",
                      responded_at=NOW - timedelta(days=10), external_source=source)
        repo.save_quote(quote)
        write_shipment(repo, FreightShipment(company_id=company_id, order_ids=[order["id"]], status="planning",
                                             combined_quote_id=quote.id, external_source=source,
                                             created_at=NOW - timedelta(days=10)))
    kinds = [item["kind"] for item in attention(repo, company_id, now=NOW)["items"]]
    assert kinds.count("approved_not_booked") == 1  # only the one entered in the app
