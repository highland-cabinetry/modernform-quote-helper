"""Needs-attention list (WORKFLOW §6): one SQL pass per table, then plain Python rules.

Quotes and shipments imported from the Freight Manager workbook (``external_source`` set) and shipments delivered more
than ``RECENT_DAYS`` ago are not reported for missing bills or measurements, so years of history do not
bury today's work.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from .comparison import approved_quotes_by_shipment, reference_quote_row
from .freight import money

UNANSWERED_WARN_DAYS = 3
UNANSWERED_DANGER_DAYS = 14
EXPIRING_DAYS = 2
NOT_BOOKED_DAYS = 1
COST_OVER_PERCENT = 5.0
RECENT_DAYS = 90
SEVERITY_ORDER = {"danger": 0, "warn": 1, "info": 2}
KINDS = ("quote_unanswered", "quote_expiring", "approved_not_booked", "pickup_today", "overdue_delivery",
         "missing_cost", "cost_over_quote", "missing_actuals", "unassigned_expense")


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _item(kind: str, severity: str, title: str, detail: str, entity_type: str, entity_id: str, number: str,
          age_days: int | None) -> dict[str, Any]:
    return {"kind": kind, "severity": severity, "title": title, "detail": detail, "entity_type": entity_type,
            "entity_id": entity_id, "number": number, "age_days": age_days}


def attention(repository, company_id: str | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    today = now.date()
    quotes = repository.workflow_quote_rows(company_id, exclude_statuses=("void",))
    shipments = repository.workflow_shipment_rows(company_id)
    orders = repository.workflow_order_numbers(company_id)
    expenses = repository.expense_sums_by_shipment(company_id)
    quote_by_id = {q["id"]: q for q in quotes}
    approved = approved_quotes_by_shipment(quotes)
    items: list[dict[str, Any]] = []

    def order_label(order_ids) -> str:
        return ", ".join(orders[oid] for oid in (order_ids or []) if oid in orders)

    for quote in quotes:
        if quote["external_source"] or quote["status"] == "superseded":
            continue  # imported history and replaced quotes: no follow-up reminders
        label = order_label(quote["order_ids"]) or quote["number"]
        if quote["customer_status"] == "sent":
            sent = _datetime(quote["sent_at"])
            age = (now - sent).days if sent else None
            if age is not None and age >= UNANSWERED_WARN_DAYS:
                danger = age >= UNANSWERED_DANGER_DAYS
                items.append(_item(
                    "quote_unanswered", "danger" if danger else "warn", f"{quote['number']} unanswered for {age} days",
                    f"{label}: sent {age} days ago with no answer"
                    + ("; consider marking it expired" if danger else "; follow up with the customer"),
                    "quote", quote["id"], quote["number"], age))
        if quote["customer_status"] in ("not_sent", "sent"):
            expires = _date(quote["cost.expires_on"])
            if expires is not None and 0 <= (expires - today).days <= EXPIRING_DAYS:
                left = (expires - today).days
                created = _datetime(quote["created_at"])
                items.append(_item(
                    "quote_expiring", "warn", f"{quote['number']} price expires "
                    + ("today" if left == 0 else "tomorrow" if left == 1 else f"in {left} days"),
                    f"{label}: {quote['cost.carrier'] or 'carrier'} price {money(quote['cost.amount_cents'])} "
                    f"valid until {expires.isoformat()}", "quote", quote["id"], quote["number"],
                    (now - created).days if created else None))

    recent = today - timedelta(days=RECENT_DAYS)
    for shipment in shipments:
        status = shipment["status"]
        if status == "cancelled":
            continue
        sid, number = shipment["id"], shipment["number"]
        label = order_label(shipment["order_ids"]) or shipment["name"] or number
        reference = reference_quote_row(shipment, quote_by_id.get(shipment["combined_quote_id"] or ""), approved)
        if status == "planning" and reference and reference["customer_status"] == "approved" \
                and not shipment["external_source"]:
            approved_at = _datetime(reference["responded_at"]) or _datetime(shipment["created_at"])
            age = (now - approved_at).days if approved_at else 0
            if age >= NOT_BOOKED_DAYS:
                items.append(_item("approved_not_booked", "warn", f"{number} approved {age} day(s) ago, not booked",
                                   f"{label}: quote {reference['number']} was approved; book the carrier",
                                   "shipment", sid, number, age))
        pickup = _date(shipment["pickup_date"])
        if status == "booked" and pickup is not None and 0 <= (pickup - today).days <= 1:
            items.append(_item("pickup_today", "info",
                               f"{number} picks up {'today' if pickup == today else 'tomorrow'}",
                               f"{label} with {shipment['carrier'] or 'carrier not set'}", "shipment", sid, number, 0))
        expected = _date(shipment["expected_delivery_date"])
        if status == "in_transit" and expected is not None and expected < today:
            late = (today - expected).days
            items.append(_item("overdue_delivery", "danger" if late > 2 else "warn",
                               f"{number} is {late} day(s) past its expected delivery",
                               f"{label}: expected {expected.isoformat()}"
                               + (f", PRO {shipment['pro_number']}" if shipment["pro_number"] else ""),
                               "shipment", sid, number, late))
        if status not in ("delivered", "closed") or shipment["external_source"]:
            continue
        finished = _date(shipment["delivery_date"]) or _date(shipment["updated_at"])
        if finished is not None and finished < recent:
            continue
        age = (today - finished).days if finished else None
        cost, count = expenses.get(sid, (0, 0))
        if count == 0:
            items.append(_item("missing_cost", "warn", f"{number} has no carrier bill",
                               f"{label} was {status}; record the carrier's invoice", "shipment", sid, number, age))
        expected_cost = reference["cost.amount_cents"] if reference else None
        if count and expected_cost and cost > expected_cost * (1 + COST_OVER_PERCENT / 100):
            over = cost - expected_cost
            items.append(_item("cost_over_quote", "warn", f"{number} cost {money(over)} more than quoted",
                               f"{label}: billed {money(cost)} vs quoted {money(expected_cost)} "
                               f"(+{over / expected_cost * 100:.1f}%)", "shipment", sid, number, age))
        actual = shipment["actual"] or {}
        if not (actual.get("pallets") or actual.get("billed_weight")):
            items.append(_item("missing_actuals", "info", f"{number} has no actual measurements",
                               f"{label}: record what shipped to compare with the quote", "shipment", sid, number,
                               age))

    unassigned = repository.unassigned_money_rows(company_id)["expenses"]
    if unassigned:
        total = sum(row["amount_cents"] for row in unassigned)
        oldest = min(_datetime(row["created_at"]) for row in unassigned)
        items.append(_item("unassigned_expense", "warn", f"{len(unassigned)} bill(s) without a shipment",
                           f"{money(total)} in carrier bills are not assigned to a shipment", "unassigned_expense",
                           "", "", (now - oldest).days if oldest else None))

    items.sort(key=lambda item: (SEVERITY_ORDER[item["severity"]], -(item["age_days"] or 0), item["number"]))
    counts = {kind: 0 for kind in KINDS}
    for item in items:
        counts[item["kind"]] += 1
    return {"items": items, "counts": counts, "total": len(items), "generated_at": now.isoformat()}
