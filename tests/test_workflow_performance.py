"""The needs-attention list, profit reports and Freight Data stay fast with a few years of history.

2,000 shipments (with orders, a priced combined quote, bills, extra revenue and some actuals) plus 1,000
open quotes are written directly, then each read is timed: the first call (rows read from SQLite) and a
repeat (rows reused while the tables are unchanged). Target on a workstation: about 100 ms for a first call
and 20-30 ms for a repeat. The asserted bounds only catch regressions such as a query per shipment; shared CI
runners (Windows especially) are several times slower and noisy. Run with ``-s`` to see the numbers.
"""

from __future__ import annotations

import gc
import time
from datetime import UTC, date, datetime, timedelta

import pytest
from app.freight_models import (
    ActualMeasurements,
    FreightShipment,
    Order,
    PalletMeasurement,
    Quote,
    QuoteSummary,
    ShipmentExpense,
    ShipmentRevenue,
)
from app.services.attention import attention
from app.services.profit_reports import freight_records, summary
from fixtures_freight import make_repo

SHIPMENTS = 2000
OPEN_QUOTES = 1000
ROWS = [{"pallets": 1, "length": 48, "width": 40, "height": 50, "weight": 450}] * 3


def build(repo) -> str:
    company = repo.default_company().id
    start = datetime(2024, 1, 1, tzinfo=UTC)
    reps, carriers = ["JD", "MK", "AL", "RT"], ["Estes", "Saia", "ODFL", "XPO", "R+L"]
    statuses = ["closed"] * 6 + ["delivered", "in_transit", "booked", "planning"]
    with repo.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for index in range(SHIPMENTS):
            created = start + timedelta(hours=9 * index)
            order = Order(company_id=company, order_number=f"SO{100000 + index}", status="ready",
                          customer_name=f"Customer {index % 150}", sales_rep=reps[index % 4],
                          customer_emails=[f"buyer{index}@example.com"])
            repo._write_order(connection, order)
            quote = Quote(company_id=company, kind="packed", order_ids=[order.id], role="combined",
                          customer_status="approved", created_at=created, responded_at=created,
                          summary=QuoteSummary(pallets=3, total_weight=1350, rows=ROWS))
            quote.cost.amount_cents = 40000 + index % 997
            quote.cost.customer_price_cents = 50000 + index % 991
            quote.cost.carrier = carriers[index % 5]
            status = statuses[index % len(statuses)]
            shipment = FreightShipment(
                company_id=company, order_ids=[order.id], status=status, carrier=carriers[index % 5],
                pickup_date=created.date(), delivery_date=created.date() + timedelta(days=3),
                expected_delivery_date=created.date() + timedelta(days=3), combined_quote_id=quote.id,
                customer_charge_cents=quote.cost.customer_price_cents,
                purpose="sister" if index % 25 == 0 else "standard",
                actual=ActualMeasurements(pallets=[PalletMeasurement(length=48, width=40, height=50, weight=470,
                                                                     quantity=3)]) if index % 3 == 0 else None)
            quote.freight_shipment_id = shipment.id
            repo._write_quote(connection, quote, action="imported", summary="perf")
            repo.write_freight_shipment(connection, shipment, action="imported", summary="perf")
            if status in ("closed", "delivered"):
                repo.write_expense(connection, ShipmentExpense(shipment_id=shipment.id, amount_cents=41000))
                if index % 4 == 0:
                    repo.write_expense(connection, ShipmentExpense(shipment_id=shipment.id, amount_cents=2500,
                                                                   kind="accessorial"))
            if index % 10 == 0:
                repo.write_revenue(connection, ShipmentRevenue(company_id=company, shipment_id=shipment.id,
                                                               amount_cents=2500))
        for index in range(OPEN_QUOTES):
            quote = Quote(company_id=company, kind="measured", customer_status="sent",
                          sent_at=datetime.now(UTC) - timedelta(days=index % 20), summary=QuoteSummary(rows=ROWS))
            repo._write_quote(connection, quote, action="imported", summary="perf")
    return company


def timed(function, *args, **kwargs):
    """(result, first call, best repeated call). Repeated calls reuse rows while the tables are unchanged."""

    gc.collect()  # time this call, not a collection of garbage left by the previous step
    started = time.perf_counter()
    result = function(*args, **kwargs)
    cold = time.perf_counter() - started
    warm = None
    for _ in range(3):
        started = time.perf_counter()
        function(*args, **kwargs)
        elapsed = time.perf_counter() - started
        warm = elapsed if warm is None else min(warm, elapsed)
    return result, cold, warm


def ms(seconds: float) -> str:
    return f"{seconds * 1000:.0f} ms"


@pytest.mark.performance
def test_attention_reports_and_records_are_fast(tmp_path):
    repo = make_repo(tmp_path, seed=False)
    company = build(repo)

    records, records_cold, records_warm = timed(freight_records, repo, company)
    repo.__dict__.pop("_workflow_rows_cache", None)  # each first call below reads the tables again
    needs, attention_cold, attention_warm = timed(attention, repo, company)
    repo.__dict__.pop("_workflow_rows_cache", None)
    report, month_cold, month_warm = timed(summary, repo, company_id=company, group="month")
    repo.__dict__.pop("_workflow_rows_cache", None)
    by_customer, customer_cold, customer_warm = timed(
        summary, repo, company_id=company, group="customer", exclude_sister=True, start=date(2024, 6, 1),
        end=date(2025, 6, 30))
    print(f"\nworkflow performance ({SHIPMENTS} shipments, {SHIPMENTS + OPEN_QUOTES} quotes), first call / repeat: "
          f"attention {ms(attention_cold)} / {ms(attention_warm)}, report by month {ms(month_cold)} / "
          f"{ms(month_warm)}, report by customer {ms(customer_cold)} / {ms(customer_warm)}, "
          f"freight records {ms(records_cold)} / {ms(records_warm)}")

    assert report["totals"]["shipments"] == SHIPMENTS
    assert len(records) == SHIPMENTS
    assert by_customer["rows"] and needs["counts"]["quote_unanswered"] > 0
    assert max(attention_cold, month_cold, customer_cold) < 2.0
    assert max(attention_warm, month_warm, customer_warm) < 0.5
    assert records_cold < 3.0

    # A change is seen on the next call.
    shipment = repo.list_freight_shipments(company_id=company, status="booked", limit=1)[0]
    shipment.status = "cancelled"
    with repo.connect() as connection:
        repo.write_freight_shipment(connection, shipment, action="updated", summary="perf")
    assert summary(repo, company_id=company)["totals"]["shipments"] == SHIPMENTS - 1
