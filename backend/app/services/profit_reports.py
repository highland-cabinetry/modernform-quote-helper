"""Freight profit reports (WORKFLOW §7) and the Freight Data records.

Fast by construction: one query per table (only the needed fields, see the repository's 3.2 helpers) and
plain Python aggregation, so a few thousand shipments report in well under 100 ms.

* Revenue = the shipment's customer charge + non-void extra revenue.
* Cost = non-void shipment expenses. Net = revenue − cost. Margin % = net / revenue.
* Cancelled shipments are excluded. ``exclude_sister`` drops purpose ``sister``.
* A shipment's date is its pickup date, else its delivery date, else the day it was created.
* Quote counts use the quote's creation date; void quotes and superseded quotes nobody answered are ignored.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from .comparison import approved_quotes_by_shipment, margin_percent, profit, reference_quote_row, weight_over

GROUPS = ("month", "rep", "carrier", "purpose", "customer")
NONE_LABEL = "(none)"


def local_day(value: str | None) -> str:
    """Calendar day of a stored timestamp on this PC's clock (UTC timestamps would put evening work on
    the next day); plain dates are returned as they are."""
    if not value:
        return ""
    if len(value) <= 10:
        return value
    try:
        return datetime.fromisoformat(value).astimezone().date().isoformat()
    except ValueError:
        return value[:10]


def _shipment_date(row: dict) -> str:
    return row.get("pickup_date") or row.get("delivery_date") or local_day(row["created_at"])


# The Freight Manager workbook never recorded declines: its dashboard counted a quote still "Quoted"
# after 30 days as declined. Imported quotes are reported the same way.
IMPORTED_DECLINE_DAYS = 30


def _declined(quote: dict, today: date) -> bool:
    if quote["customer_status"] == "declined":
        return True
    if quote.get("external_source") and quote["customer_status"] == "sent":
        created = local_day(quote["created_at"])
        return bool(created) and (today - date.fromisoformat(created)).days >= IMPORTED_DECLINE_DAYS
    return False


def _in_range(day: str, start: date | None, end: date | None) -> bool:
    return (start is None or day >= start.isoformat()) and (end is None or day <= end.isoformat())


def _first(values) -> str:
    return next((value for value in values if value), "")


def _quote_sent(quote: dict) -> bool:
    return bool(quote["sent_at"]) or quote["customer_status"] in ("sent", "approved", "declined", "expired")


def _empty_row(key: str) -> dict[str, Any]:
    return {"key": key, "shipments": 0, "customer_charge_cents": 0, "extra_revenue_cents": 0, "revenue_cents": 0,
            "cost_cents": 0, "net_cents": 0, "margin_percent": None, "loss_shipments": 0,
            "shipments_without_cost": 0, "quotes": 0, "quotes_sent": 0, "quotes_approved": 0, "quotes_declined": 0,
            "conversion_percent": None}


def _finish(row: dict[str, Any]) -> dict[str, Any]:
    row["revenue_cents"] = row["customer_charge_cents"] + row["extra_revenue_cents"]
    row["net_cents"] = row["revenue_cents"] - row["cost_cents"]
    row["margin_percent"] = margin_percent(row["net_cents"], row["revenue_cents"])
    row["conversion_percent"] = (round(row["quotes_approved"] / row["quotes_sent"] * 100, 1)
                                 if row["quotes_sent"] else None)
    return row


def summary(repository, *, company_id: str | None = None, start: date | None = None, end: date | None = None,
            group: str = "month", exclude_sister: bool = False) -> dict[str, Any]:
    if group not in GROUPS:
        raise ValueError(f"Group by one of: {', '.join(GROUPS)}")
    if start and end and start > end:
        raise ValueError("The start date is after the end date")
    today = date.today()
    shipments = repository.workflow_shipment_rows(company_id)
    quotes = repository.workflow_quote_rows(company_id, exclude_statuses=("void",))
    orders = repository.workflow_order_fields(company_id) if group in ("rep", "customer") else {}
    expenses = repository.expense_sums_by_shipment(company_id)
    revenue = repository.revenue_sums_by_shipment(company_id)
    purpose_by_shipment = {s["id"]: s["purpose"] or "standard" for s in shipments}

    def order_value(order_ids, field: str) -> str:
        return _first(orders[oid][field] for oid in (order_ids or []) if oid in orders)

    def key_for(kind: str, row: dict, day: str) -> str:
        if group == "month":
            return day[:7]
        if group == "rep":
            return order_value(row["order_ids"], "sales_rep")
        if group == "customer":
            return order_value(row["order_ids"], "customer_name")
        if group == "carrier":
            return row["carrier"] if kind == "shipment" else row["cost.carrier"]
        return (row["purpose"] or "standard") if kind == "shipment" else purpose_by_shipment.get(
            row["freight_shipment_id"] or "", "standard")

    rows: dict[str, dict[str, Any]] = {}
    totals = _empty_row("total")
    for shipment in shipments:
        if shipment["status"] == "cancelled" or (exclude_sister and shipment["purpose"] == "sister"):
            continue
        day = _shipment_date(shipment)
        if not _in_range(day, start, end):
            continue
        key = key_for("shipment", shipment, day) or NONE_LABEL
        cost, count = expenses.get(shipment["id"], (0, 0))
        charge = shipment["customer_charge_cents"] or 0
        extra = revenue.get(shipment["id"], 0)
        for target in (rows.setdefault(key, _empty_row(key)), totals):
            target["shipments"] += 1
            target["customer_charge_cents"] += charge
            target["extra_revenue_cents"] += extra
            target["cost_cents"] += cost
            target["loss_shipments"] += int(charge + extra - cost < 0)
            target["shipments_without_cost"] += int(count == 0)
    for quote in quotes:
        if quote["legacy"] and not quote.get("external_source"):  # 2.x history backfill, not real quotes
            continue
        answered = quote["customer_status"] in ("approved", "declined", "expired")
        if quote["status"] == "superseded" and not answered:
            continue
        if exclude_sister and purpose_by_shipment.get(quote["freight_shipment_id"] or "") == "sister":
            continue
        day = local_day(quote["created_at"])
        if not _in_range(day, start, end):
            continue
        key = key_for("quote", quote, day) or NONE_LABEL
        for target in (rows.setdefault(key, _empty_row(key)), totals):
            target["quotes"] += 1
            target["quotes_sent"] += int(_quote_sent(quote))
            target["quotes_approved"] += int(quote["customer_status"] == "approved")
            target["quotes_declined"] += int(_declined(quote, today))
    result = [_finish(row) for row in rows.values()]
    if group == "month":
        result.sort(key=lambda row: row["key"])
    else:
        result.sort(key=lambda row: (-row["revenue_cents"], -row["shipments"], row["key"]))
    _finish(totals)
    unassigned = repository.unassigned_money_rows(company_id)
    unassigned_cost = sum(r["amount_cents"] for r in unassigned["expenses"]
                          if _in_range(r["date"] or local_day(r["created_at"]), start, end))
    unassigned_revenue = sum(r["amount_cents"] for r in unassigned["revenue"]
                             if _in_range(r["date"] or local_day(r["created_at"]), start, end))
    totals.update({
        "unassigned_expense_cents": unassigned_cost,
        "unassigned_revenue_cents": unassigned_revenue,
        "net_with_unassigned_cents": totals["net_cents"] + unassigned_revenue - unassigned_cost,
    })
    return {"group": group, "from": start.isoformat() if start else None, "to": end.isoformat() if end else None,
            "exclude_sister": exclude_sister, "rows": result, "totals": totals}


CSV_COLUMNS = ("shipments", "revenue", "customer_charges", "extra_revenue", "cost", "net", "margin_percent",
               "loss_shipments", "shipments_without_cost", "quotes", "quotes_sent", "quotes_approved",
               "quotes_declined", "conversion_percent")


def _dollars(cents: int | None) -> str:
    return "" if cents is None else f"{cents / 100:.2f}"


def summary_csv(report: dict[str, Any]) -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow([report["group"], *CSV_COLUMNS])
    for row in [*report["rows"], report["totals"]]:
        writer.writerow([
            row["key"], row["shipments"], _dollars(row["revenue_cents"]), _dollars(row["customer_charge_cents"]),
            _dollars(row["extra_revenue_cents"]), _dollars(row["cost_cents"]), _dollars(row["net_cents"]),
            "" if row["margin_percent"] is None else row["margin_percent"], row["loss_shipments"],
            row["shipments_without_cost"], row["quotes"], row["quotes_sent"], row["quotes_approved"],
            row["quotes_declined"], "" if row["conversion_percent"] is None else row["conversion_percent"],
        ])
    totals = report["totals"]
    writer.writerow(["unassigned bills", "", "", "", "", _dollars(totals["unassigned_expense_cents"])])
    writer.writerow(["unassigned revenue", "", _dollars(totals["unassigned_revenue_cents"])])
    writer.writerow(["net incl. unassigned", "", "", "", "", "", _dollars(totals["net_with_unassigned_cents"])])
    return stream.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------- freight records (Freight Data)


def _separate_estimates(related: list[dict], shipment: dict) -> tuple[int | None, bool]:
    """Same rule as services.freight.separate_estimates, on report rows."""

    orders = set(shipment["order_ids"] or [])
    chosen: list[dict] = []
    covered: set[str] = set()
    for quote in sorted(related, key=lambda q: q["created_at"], reverse=True):
        quote_orders = set(quote["order_ids"] or [])
        if (quote["status"] != "final" or quote["role"] != "individual" or quote["id"] == shipment["combined_quote_id"]
                or quote["cost.amount_cents"] is None or not quote_orders or not quote_orders <= orders
                or quote_orders & covered):
            continue
        chosen.append(quote)
        covered |= quote_orders
    if not chosen:
        return None, False
    return sum(q["cost.amount_cents"] for q in chosen), covered == orders


def freight_records(repository, company_id: str | None = None) -> list[dict[str, Any]]:
    """One row per shipment: estimate vs actual, revenue, cost, net, margin, expected vs actual weight."""

    shipments = sorted(repository.workflow_shipment_rows(company_id), key=lambda s: s["created_at"], reverse=True)
    quotes = repository.workflow_quote_rows(company_id)
    orders = repository.workflow_order_fields(company_id)
    expenses = repository.expense_sums_by_shipment(company_id)
    revenue = repository.revenue_sums_by_shipment(company_id)
    with repository.connect() as connection:
        allocated = {row[0] for row in connection.execute("SELECT shipment_id FROM expense_allocations")}
    quote_by_id = {q["id"]: q for q in quotes}
    approved = approved_quotes_by_shipment(quotes)
    by_order: dict[str, list[dict]] = defaultdict(list)
    for quote in quotes:
        for order_id in quote["order_ids"] or []:
            by_order[order_id].append(quote)
    records = []
    for shipment in shipments:
        order_ids = shipment["order_ids"] or []
        combined = quote_by_id.get(shipment["combined_quote_id"] or "")
        if combined and combined["status"] == "void":
            combined = None
        related = list({q["id"]: q for oid in order_ids for q in by_order.get(oid, [])}.values())
        separate_total, separate_complete = _separate_estimates(related, shipment)
        estimate = combined["cost.amount_cents"] if combined else None
        reference = reference_quote_row(shipment, combined, approved)
        expected_cost = reference["cost.amount_cents"] if reference else None
        actual, count = expenses.get(shipment["id"], (None, 0))
        extra = revenue.get(shipment["id"], 0)
        charge = shipment["customer_charge_cents"]
        revenue_total = (charge or 0) + extra if (charge is not None or extra) else None
        net, estimated = profit(revenue_total, actual, expected_cost)
        actual_info = shipment["actual"] or {}
        pallets = actual_info.get("pallets") or []
        measured = (round(sum(p["weight"] * p.get("quantity", 1) for p in pallets), 1) if pallets
                    else actual_info.get("billed_weight"))
        expected_weight = reference["summary.total_weight"] if reference else None
        records.append({
            "id": shipment["id"],
            "number": shipment["number"],
            "name": shipment["name"],
            "status": shipment["status"],
            "purpose": shipment["purpose"] or "standard",
            "carrier": shipment["carrier"] or "",
            "broker": shipment["broker"] or "",
            "pickup_date": shipment["pickup_date"],
            "delivery_date": shipment["delivery_date"],
            "expected_delivery_date": shipment["expected_delivery_date"],
            "orders": [orders[oid]["order_number"] if oid in orders else oid for oid in order_ids],
            "customer": _first(orders[oid]["customer_name"] for oid in order_ids if oid in orders),
            "rep": _first(orders[oid]["sales_rep"] for oid in order_ids if oid in orders),
            "quote_number": reference["number"] if reference else None,
            "quote_status": reference["customer_status"] if reference else None,
            "expected_cost_cents": expected_cost,
            "pallets": reference["summary.pallets"] if reference else None,
            "weight": expected_weight,
            "actual_pallets": sum(p.get("quantity", 1) for p in pallets) if pallets else None,
            "actual_weight": measured,
            "weight_over": weight_over(expected_weight, measured),
            "estimate_cents": estimate,
            "separate_estimates_cents": separate_total if separate_complete else None,
            "actual_cents": actual,
            "expense_count": count,
            "allocated": shipment["id"] in allocated,
            "variance_cents": (actual - estimate) if actual is not None and estimate is not None else None,
            "charge_source": shipment["charge_source"] or "quote",
            "customer_charge_cents": charge,
            "extra_revenue_cents": extra,
            "revenue_cents": revenue_total,
            "cost_cents": actual,
            "net_cents": net,
            "net_estimated": estimated,
            "margin_percent": margin_percent(net, revenue_total),
            "created_at": shipment["created_at"],
        })
    return records


RECORD_COLUMNS = ("number", "name", "status", "purpose", "carrier", "broker", "pickup_date", "delivery_date",
                  "orders", "customer", "rep", "quote_number", "pallets", "weight", "actual_pallets", "actual_weight",
                  "estimate", "separate_estimates", "actual", "variance", "charge_source", "customer_charge",
                  "extra_revenue", "revenue", "cost", "net", "margin_percent", "allocated", "created_at")


def freight_records_csv(rows: list[dict[str, Any]]) -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(RECORD_COLUMNS)
    for row in rows:
        writer.writerow([
            row["number"], row["name"], row["status"], row["purpose"], row["carrier"], row["broker"],
            row["pickup_date"], row["delivery_date"], " ".join(row["orders"]), row["customer"], row["rep"],
            row["quote_number"], row["pallets"], row["weight"], row["actual_pallets"], row["actual_weight"],
            _dollars(row["estimate_cents"]), _dollars(row["separate_estimates_cents"]), _dollars(row["actual_cents"]),
            _dollars(row["variance_cents"]), row["charge_source"], _dollars(row["customer_charge_cents"]),
            _dollars(row["extra_revenue_cents"]), _dollars(row["revenue_cents"]), _dollars(row["cost_cents"]),
            _dollars(row["net_cents"]), "" if row["margin_percent"] is None else row["margin_percent"],
            row["allocated"], row["created_at"],
        ])
    return stream.getvalue().encode("utf-8-sig")
