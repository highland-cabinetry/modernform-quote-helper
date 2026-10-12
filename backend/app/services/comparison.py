"""Quoted versus actual for one shipment (WORKFLOW §4): weight, pallets, cost, revenue and profit.

Pure functions over plain values so the shipment page, freight records and reports agree.
"""

from __future__ import annotations

from typing import Any

from ..freight_models import ActualMeasurements, FreightShipment, Quote

WEIGHT_OVER_PERCENT = 5.0
WEIGHT_OVER_POUNDS = 100.0


def reference_quote(shipment: FreightShipment, combined: Quote | None, quotes) -> Quote | None:
    """The quote the shipment is measured against: the customer-approved quote of this shipment (newest answer),
    else its combined quote. A plan built after approval may replace the combined quote, but what the customer
    accepted stays the reference for quoted weight and expected cost."""

    approved = [q for q in [*([combined] if combined else []), *quotes]
                if q.customer_status == "approved" and q.status != "void" and q.freight_shipment_id == shipment.id]
    if approved:
        return max(approved, key=lambda q: (q.responded_at or q.created_at, q.number))
    return combined


def reference_quote_row(shipment: dict, combined: dict | None, approved_by_shipment: dict[str, dict]) -> dict | None:
    """``reference_quote`` on report rows; ``approved_by_shipment`` maps shipment id -> its newest approved quote."""

    return approved_by_shipment.get(shipment["id"]) or combined


def approved_quotes_by_shipment(quotes: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for quote in quotes:
        shipment_id = quote["freight_shipment_id"]
        if not shipment_id or quote["customer_status"] != "approved" or quote["status"] == "void":
            continue
        current = result.get(shipment_id)
        key = (quote["responded_at"] or quote["created_at"], quote["number"])
        if current is None or key > (current["responded_at"] or current["created_at"], current["number"]):
            result[shipment_id] = quote
    return result


def actual_weight(actual: ActualMeasurements | None) -> float | None:
    """Measured gross weight when pallets were measured, otherwise the carrier's billed weight."""

    if actual is None:
        return None
    if actual.pallets:
        return actual.total_weight
    return actual.billed_weight


def has_actuals(actual: ActualMeasurements | None) -> bool:
    return bool(actual and (actual.pallets or actual.billed_weight))


def weight_over(quoted: float | None, actual: float | None) -> bool:
    """Actual heavier than quoted by more than 5 % or by more than 100 lb."""

    if not quoted or actual is None:
        return False
    over = actual - quoted
    return over > 0 and (over > quoted * WEIGHT_OVER_PERCENT / 100 or over > WEIGHT_OVER_POUNDS)


def margin_percent(profit_cents: int | None, revenue_cents: int | None) -> float | None:
    if profit_cents is None or not revenue_cents or revenue_cents <= 0:
        return None
    return round(profit_cents / revenue_cents * 100, 1)


def profit(revenue_cents: int | None, actual_cost_cents: int | None,
           expected_cost_cents: int | None) -> tuple[int | None, bool]:
    """(profit, estimated): revenue − actual cost, or − expected cost (estimated) before any bill."""

    if revenue_cents is None:
        return None, False
    if actual_cost_cents is not None:
        return revenue_cents - actual_cost_cents, False
    if expected_cost_cents is not None:
        return revenue_cents - expected_cost_cents, True
    return None, False


def shipment_comparison(shipment: FreightShipment, combined: Quote | None, *, actual_cost_cents: int | None,
                        extra_revenue_cents: int) -> dict[str, Any]:
    quoted = None
    if combined is not None:
        quoted = {"quote_id": combined.id, "number": combined.number, "pallets": combined.summary.pallets,
                  "total_weight": combined.summary.total_weight, "rows": combined.summary.rows}
    actual = shipment.actual
    actual_block = None
    if actual is not None:
        actual_block = {
            "pallets": actual.pallet_count if actual.pallets else None,
            "total_weight": actual.total_weight if actual.pallets else None,
            "billed_weight": actual.billed_weight,
            "billed_class": actual.billed_class,
            "weight": actual_weight(actual),
            "rows": [pallet.model_dump(mode="json") for pallet in actual.pallets],
            "source": actual.source,
            "measured_on": actual.measured_on.isoformat() if actual.measured_on else None,
            "notes": actual.notes,
        }
    expected = combined.cost.amount_cents if combined is not None else None
    variance = actual_cost_cents - expected if actual_cost_cents is not None and expected is not None else None
    charge = shipment.customer_charge_cents
    revenue_known = charge is not None or extra_revenue_cents > 0
    revenue_total = (charge or 0) + extra_revenue_cents if revenue_known else None
    profit_cents, estimated = profit(revenue_total, actual_cost_cents, expected)
    quoted_weight = quoted["total_weight"] if quoted else None
    measured = actual_weight(actual)
    weight_variance = round(measured - quoted_weight, 1) if measured is not None and quoted_weight else None
    return {
        "quoted": quoted,
        "actual": actual_block,
        "weight_variance_lb": weight_variance,
        "weight_variance_percent": (round(weight_variance / quoted_weight * 100, 1)
                                    if weight_variance is not None else None),
        "cost": {
            "expected_cents": expected,
            "actual_cents": actual_cost_cents,
            "variance_cents": variance,
            "variance_percent": round(variance / expected * 100, 1) if variance is not None and expected else None,
        },
        "revenue": {"customer_charge_cents": charge, "extra_cents": extra_revenue_cents,
                    "total_cents": revenue_total, "charge_source": shipment.charge_source},
        "profit_cents": profit_cents,
        "profit_estimated": estimated,
        "margin_percent": margin_percent(profit_cents, revenue_total),
        "flags": {
            "weight_over": weight_over(quoted_weight, measured),
            "pallets_changed": bool(quoted and actual is not None and actual.pallets
                                    and actual.pallet_count != quoted["pallets"]),
            "cost_over": variance is not None and variance > 0,
        },
    }
