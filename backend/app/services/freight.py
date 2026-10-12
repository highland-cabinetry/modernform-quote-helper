"""Quotes, shipments, shared freight expenses and their allocation.

Orders, quotes, shipments and expenses are separate records:

* a quote is recorded automatically whenever a pallet plan is accepted or a measured
  quote is saved, and keeps a snapshot of its numbers;
* a shipment groups orders that physically travel together, whether or not they were
  quoted together; an order can be in only one active shipment;
* expenses belong to the shipment, never to an order, so a shared cost is never
  duplicated per order;
* an allocation is an analytical split of the shipment's expense total across its
  orders that always reconciles to the cent.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from ..freight_models import (
    Allocation,
    AllocationLine,
    AllocationRequest,
    Company,
    DeliveryTerms,
    FreightShipment,
    FreightShipmentCreate,
    FreightShipmentInput,
    Order,
    Quote,
    QuoteCost,
    QuoteCreate,
    QuoteSummary,
    QuoteUpdate,
    ShipmentExpense,
    ShipmentExpenseInput,
)
from ..freight_repository import ConflictError, NotFoundError
from ..models import PackingRequest, PackingResult
from .comparison import reference_quote, shipment_comparison
from .order_import import ensure_orders_for_references, references_from_name

log = logging.getLogger("quote_helper.freight")


# ---------------------------------------------------------------------------- quote summaries


def order_of_source(source_ref: str) -> str:
    return str(source_ref or "").split(" · ", 1)[0].strip().upper()


def summarize_packed(result: PackingResult, orders: list[Order]) -> QuoteSummary:
    by_number: dict[str, str] = {}
    for order in orders:
        for reference in [order.order_number, *order.references]:
            by_number.setdefault(reference.upper(), order.id)
    weights: dict[str, float] = defaultdict(float)
    shares: dict[str, float] = defaultdict(float)
    for pallet in result.pallets:
        pallet_weights: dict[str, float] = defaultdict(float)
        for item in pallet.items:
            order_id = by_number.get(order_of_source(item.source_refs[0] if item.source_refs else ""))
            if order_id:
                pallet_weights[order_id] += item.weight
        total = sum(item.weight for item in pallet.items) or 0
        for order_id, weight in pallet_weights.items():
            weights[order_id] += weight
            if total:
                shares[order_id] += weight / total
    for item in result.unpacked:
        order_id = by_number.get(order_of_source(item.source_refs[0] if item.source_refs else ""))
        if order_id:
            weights[order_id] += item.weight
    standard = sum(p.pallet_type == "standard" for p in result.pallets)
    return QuoteSummary(
        pallets=len(result.pallets),
        standard_pallets=standard,
        long_pallets=len(result.pallets) - standard,
        total_weight=round(result.summary.total_freight_weight, 1),
        item_weight=round(result.summary.total_item_weight, 1),
        total_units=result.summary.total_items,
        freight_ready=bool(result.freight_ready),
        optimizer=result.optimizer,
        rows=result.quote_rows,
        order_weights={k: round(v, 2) for k, v in weights.items()},
        order_pallet_shares={k: round(v, 4) for k, v in shares.items()},
    )


def summarize_manual(quote) -> QuoteSummary:
    from .broker_handoff import quote_rows

    pallets = sum(p.quantity for p in quote.pallets)
    long = sum(p.quantity for p in quote.pallets if max(p.length, p.width) > 60)
    return QuoteSummary(
        pallets=pallets,
        standard_pallets=pallets - long,
        long_pallets=long,
        total_weight=round(sum(p.weight * p.quantity for p in quote.pallets), 1),
        freight_ready=True,
        optimizer="measured",
        rows=quote_rows(quote),
    )


# ---------------------------------------------------------------------------- recording quotes


def _resolve_orders(repository, company: Company, order_ids: list[str], references: list[str], *,
                    reason: str, history: bool = False) -> list[Order]:
    orders = repository.get_orders(order_ids) if order_ids else []
    for order in orders:
        if order.company_id != company.id:
            raise ConflictError(f"Order {order.order_number} belongs to a different company")
    known = {o.id for o in orders}
    if references:
        for order in ensure_orders_for_references(repository, company, references, reason=reason, history=history):
            if order.id not in known:
                orders.append(order)
                known.add(order.id)
    return orders


def terms_from_shipping(info, keep_notes_from: DeliveryTerms | None = None) -> DeliveryTerms:
    """Delivery terms as quoted, from a plan's or measured quote's shipping info (notes typed on the quote stay)."""

    terms = DeliveryTerms.from_shipping(info)
    if keep_notes_from is not None:
        terms.notes = keep_notes_from.notes
    return terms


def refresh_terms_from_source(repository, info, *, packing_result_id: str | None = None,
                              manual_quote_id: str | None = None) -> Quote | None:
    """The shipping info of a saved plan changed: its quote's terms follow until the quote is approved."""

    quote = repository.quote_for_source(packing_result_id=packing_result_id, manual_quote_id=manual_quote_id)
    if quote is None or info is None or quote.customer_status == "approved":
        return quote
    terms = terms_from_shipping(info, keep_notes_from=quote.terms)
    if terms != quote.terms:
        quote.terms = terms
        repository.save_quote(quote, action="terms_updated",
                              summary=f"{quote.number}: delivery terms {', '.join(terms.labels())} to "
                                      f"{terms.postal_code or 'no ZIP'} (from its source)")
    return quote


def _supersede_previous(repository, quote: Quote) -> None:
    """Earlier final quotes for exactly the same orders become 'superseded' (kept, not deleted)."""

    if not quote.order_ids:
        return
    for previous in repository.list_quotes(order_id=quote.order_ids[0], status="final"):
        if previous.id != quote.id and previous.role == quote.role and set(previous.order_ids) == set(quote.order_ids):
            previous.status = "superseded"
            repository.save_quote(previous, action="superseded",
                                  summary=f"{previous.number} superseded by {quote.number}")


def record_quote(repository, payload: QuoteCreate) -> Quote:
    company = repository.get_company(payload.company_id)
    if payload.freight_shipment_id:
        # Check before anything is written, so a rejected link leaves no half-recorded quote.
        target = repository.get_freight_shipment(payload.freight_shipment_id)
        if not target:
            raise NotFoundError("Shipment not found")
        _require_editable(target)
        outside = [oid for oid in payload.order_ids if oid not in target.order_ids]
        if outside:
            raise ConflictError("A combined quote can only cover orders that are on the shipment")
    if payload.kind == "packed":
        result = repository.get_shipment(payload.packing_result_id)
        if not result:
            raise NotFoundError("The saved pallet plan was not found")
        name = result.shipment_name
        shipping_info = result.shipping_info
    else:
        manual = repository.get_manual_quote(payload.manual_quote_id)
        if not manual:
            raise NotFoundError("The measured-pallet quote was not found")
        name = manual.shipment_name
        shipping_info = manual.shipping_info
    existing = repository.quote_for_source(packing_result_id=payload.packing_result_id,
                                           manual_quote_id=payload.manual_quote_id)
    references = payload.order_references or ([] if payload.order_ids else references_from_name(company, name))
    orders = _resolve_orders(repository, company, payload.order_ids, references, reason=f"Referenced by quote {name}")
    summary = summarize_packed(result, orders) if payload.kind == "packed" else summarize_manual(manual)
    if existing and existing.freight_shipment_id:
        linked = repository.get_freight_shipment(existing.freight_shipment_id)
        if linked and linked.combined_quote_id == existing.id and linked.status in ("closed", "cancelled"):
            raise ConflictError(f"{existing.number} is the combined quote of {linked.number}, which is "
                                f"{linked.status}. Reopen the shipment before changing it.")
    if existing:
        if existing.status == "superseded":
            # Re-saving makes this the current estimate again; others for the same orders step back.
            existing.status = "final"
        existing.summary = summary
        existing.name = name
        existing.order_ids = [o.id for o in orders] or existing.order_ids
        existing.notes = payload.notes or existing.notes
        if payload.cost.model_dump(exclude_none=True, exclude_defaults=True):
            existing.cost = payload.cost
        if shipping_info is not None and existing.customer_status != "approved":
            # The source's shipping info is what was just quoted; an approved quote keeps what the customer accepted.
            existing.terms = terms_from_shipping(shipping_info, keep_notes_from=existing.terms)
        repository.save_quote(existing, action="revised", summary=f"{existing.number} revised")
        quote = existing
    else:
        quote = Quote(company_id=company.id, kind=payload.kind, name=name, packing_result_id=payload.packing_result_id,
                      manual_quote_id=payload.manual_quote_id, order_ids=[o.id for o in orders],
                      summary=summary, cost=payload.cost, notes=payload.notes,
                      terms=terms_from_shipping(shipping_info) if shipping_info is not None else None)
        repository.save_quote(quote, summary=f"Quote for {', '.join(o.order_number for o in orders) or name}")
    if payload.freight_shipment_id:
        link_combined_quote(repository, payload.freight_shipment_id, quote.id)
        quote = repository.get_quote(quote.id)
    else:
        _supersede_previous(repository, quote)
    return quote


def _linkable_shipment(repository, shipment_id: str | None) -> str | None:
    """A saved plan or measured quote is always recorded; it is linked only to an editable shipment."""

    if not shipment_id:
        return None
    shipment = repository.get_freight_shipment(shipment_id)
    if shipment is None or shipment.status in ("closed", "cancelled"):
        log.warning("Quote for shipment %s recorded without linking: shipment missing or locked", shipment_id)
        return None
    return shipment_id


def record_packed_quote(repository, request: PackingRequest, result: PackingResult) -> Quote | None:
    """Called whenever a pallet plan is saved. Never blocks saving the plan itself."""

    try:
        payload = QuoteCreate(
            company_id=request.company_id, kind="packed", packing_result_id=result.id,
            order_ids=list(dict.fromkeys(request.order_ids)),
            freight_shipment_id=_linkable_shipment(repository, request.freight_shipment_id),
        )
        try:
            return record_quote(repository, payload)
        except ConflictError:
            if not payload.freight_shipment_id:
                raise
            log.warning("Plan %s covers orders outside shipment %s; recorded without linking",
                        result.id, payload.freight_shipment_id)
            return record_quote(repository, payload.model_copy(update={"freight_shipment_id": None}))
    except Exception:  # pragma: no cover - logged; the plan is already saved
        log.exception("Could not record a quote for pallet plan %s", result.id)
        return None


def record_manual_quote(repository, manual) -> Quote | None:
    try:
        payload = QuoteCreate(
            company_id=manual.company_id, kind="measured", manual_quote_id=manual.id,
            order_ids=list(dict.fromkeys(manual.order_ids)),
            freight_shipment_id=_linkable_shipment(repository, manual.freight_shipment_id),
        )
        try:
            return record_quote(repository, payload)
        except ConflictError:
            if not payload.freight_shipment_id:
                raise
            return record_quote(repository, payload.model_copy(update={"freight_shipment_id": None}))
    except Exception:  # pragma: no cover
        log.exception("Could not record a quote for measured quote %s", manual.id)
        return None


def update_quote(repository, quote_id: str, payload: QuoteUpdate) -> Quote:
    quote = repository.get_quote(quote_id)
    if not quote:
        raise NotFoundError("Quote not found")
    changes: list[str] = []
    shipment = repository.get_freight_shipment(quote.freight_shipment_id) if quote.freight_shipment_id else None
    combined_for = shipment if shipment and shipment.combined_quote_id == quote.id else None
    if combined_for and combined_for.status in ("closed", "cancelled") and (
            (payload.status and payload.status != quote.status) or
            (payload.order_ids is not None and payload.order_ids != quote.order_ids)):
        raise ConflictError(f"{quote.number} is the combined quote of {combined_for.number}, which is "
                            f"{combined_for.status}. Reopen the shipment first.")
    if combined_for and payload.order_ids is not None and set(payload.order_ids) - set(combined_for.order_ids):
        raise ConflictError("A combined quote can only cover orders that are on the shipment")
    if payload.status and payload.status != quote.status:
        if quote.status == "void":
            raise ConflictError("A void quote cannot be reopened; record a new quote instead")
        changes.append(f"status {quote.status} → {payload.status}")
        quote.status = payload.status
    if payload.notes is not None and payload.notes != quote.notes:
        quote.notes = payload.notes
        changes.append("notes")
    if payload.cost is not None and payload.cost != quote.cost:
        quote.cost = payload.cost
        changes.append("cost")
    if payload.order_ids is not None and payload.order_ids != quote.order_ids:
        if len(set(payload.order_ids)) != len(payload.order_ids):
            raise ConflictError("An order can be included in a quote only once")
        orders = repository.get_orders(payload.order_ids)
        if any(order.company_id != quote.company_id for order in orders):
            raise ConflictError("All orders must belong to the quote's company")
        quote.order_ids = payload.order_ids
        if quote.kind == "packed" and quote.packing_result_id:
            result = repository.get_shipment(quote.packing_result_id)
            if result:
                # Per-order weights and pallet shares depend on which orders the quote covers.
                quote.summary = summarize_packed(result, orders)
        changes.append("orders")
    if changes:
        with repository.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            repository._write_quote(connection, quote, action="updated",
                                    summary=f"{quote.number}: {', '.join(changes)}")
            if "cost" in changes:
                sync_shipment_charge(repository, connection, quote)
        if payload.status == "final":
            _supersede_previous(repository, quote)
    return quote


def quote_view(repository, quote: Quote, numbers: dict[str, str] | None = None) -> dict[str, Any]:
    numbers = numbers if numbers is not None else repository.order_numbers(quote.order_ids)
    return {
        **quote.model_dump(mode="json"),
        "orders": [{"id": oid, "order_number": numbers.get(oid, "?")} for oid in quote.order_ids],
    }


def list_quote_views(repository, **filters) -> list[dict[str, Any]]:
    quotes = repository.list_quotes(**filters)
    numbers = repository.order_numbers(oid for q in quotes for oid in q.order_ids)
    shipments = {s.id: s.number for s in repository.list_freight_shipments(limit=100000)}
    views = []
    for quote in quotes:
        view = quote_view(repository, quote, numbers)
        view["shipment_number"] = shipments.get(quote.freight_shipment_id or "")
        views.append(view)
    return views


# ---------------------------------------------------------------------------- shipments


def money(cents: int | None) -> str:
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def resolve_customer_charge(shipment: FreightShipment, combined: Quote | None) -> None:
    """Set ``customer_charge_cents`` from ``charge_source`` (WORKFLOW §3).

    backcharge → backcharge amount, rep_assigned → rep charge, quote → the combined quote's customer price
    (the stored charge is kept while the quote has no price), manual → as typed, none → 0.
    """

    source = shipment.charge_source
    if source == "backcharge":
        if shipment.backcharge_cents is None:
            raise ValueError("Enter the backcharge amount, or choose another source for the customer charge")
        shipment.customer_charge_cents = shipment.backcharge_cents
    elif source == "rep_assigned":
        if shipment.rep_charge_cents is None:
            raise ValueError("Enter the charge the rep assigned, or choose another source for the customer charge")
        shipment.customer_charge_cents = shipment.rep_charge_cents
    elif source == "quote":
        price = combined.cost.customer_price_cents if combined is not None and combined.status != "void" else None
        if price is not None:
            shipment.customer_charge_cents = price
    elif source == "none":
        shipment.customer_charge_cents = 0


def _shipment_in(connection, shipment_id: str) -> FreightShipment | None:
    row = connection.execute("SELECT payload_json FROM freight_shipments WHERE id=?", (shipment_id,)).fetchone()
    return FreightShipment.model_validate_json(row[0]) if row else None


def sync_shipment_charge(repository, connection, quote: Quote) -> FreightShipment | None:
    """A quote's customer price changed: its shipment follows while it is the combined quote, the charge
    source is ``quote`` and the shipment is not locked (closed books keep their charge)."""

    if not quote.freight_shipment_id:
        return None
    shipment = _shipment_in(connection, quote.freight_shipment_id)
    if (shipment is None or shipment.combined_quote_id != quote.id or shipment.charge_source != "quote"
            or shipment.status in ("closed", "cancelled")):
        return None
    price = quote.cost.customer_price_cents
    if price is None or price == shipment.customer_charge_cents:
        return None
    before = shipment.customer_charge_cents
    shipment.customer_charge_cents = price
    repository.write_freight_shipment(
        connection, shipment, action="charge_updated",
        summary=f"Customer charge {money(price)} from {quote.number}",
        details={"changes": {"customer_charge_cents": [before, price]}, "quote_id": quote.id})
    return shipment


def _shipment_warnings(orders: list[Order]) -> list[str]:
    warnings = []
    zips = {o.ship_to.postal_code for o in orders if o.ship_to.postal_code}
    if len(zips) > 1:
        warnings.append(f"Orders ship to different ZIP codes ({', '.join(sorted(zips))}).")
    if any(o.status == "needs_review" for o in orders):
        warnings.append("Some orders still need review.")
    return warnings


def create_shipment(repository, payload: FreightShipmentCreate) -> FreightShipment:
    company = repository.get_company(payload.company_id)
    orders = repository.get_orders(payload.order_ids)
    for order in orders:
        if order.company_id != company.id:
            raise ConflictError(f"Order {order.order_number} belongs to a different company")
        if order.status == "cancelled":
            raise ConflictError(f"Order {order.order_number} is cancelled")
    shipment = FreightShipment(
        company_id=company.id,
        order_ids=payload.order_ids,
        **payload.model_dump(exclude={"company_id", "order_ids"}),
    )
    if not shipment.name:
        shipment.name = " + ".join(order.order_number for order in orders)
    resolve_customer_charge(shipment, None)
    if not shipment.destination.postal_code:
        zips = {o.ship_to.postal_code for o in orders if o.ship_to.postal_code}
        if len(zips) == 1:
            shipment.destination = next(o.ship_to for o in orders if o.ship_to.postal_code)
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        busy = repository.active_shipment_for_orders(connection, payload.order_ids)
        if busy:
            numbers = {o.id: o.order_number for o in orders}
            raise ConflictError("Already in another shipment: " + ", ".join(
                f"{numbers[oid]} ({number})" for oid, number in busy.items()))
        repository.write_freight_shipment(connection, shipment, action="created",
                                          summary=f"Shipment created with {len(orders)} order(s)",
                                          details={"order_ids": shipment.order_ids})
    # Link existing quotes for exactly this set of orders as the combined estimate.
    candidates = [q for q in repository.list_quotes(order_id=payload.order_ids[0], status="final")
                  if set(q.order_ids) == set(payload.order_ids) and not q.freight_shipment_id]
    if candidates:
        link_combined_quote(repository, shipment.id, candidates[0].id)
        shipment = repository.get_freight_shipment(shipment.id)
    return shipment


def _require_editable(shipment: FreightShipment) -> None:
    if shipment.status in ("closed", "cancelled"):
        raise ConflictError(f"Shipment {shipment.number} is {shipment.status}. Reopen it before changing it.")


def update_shipment(
    repository, shipment_id: str, payload: FreightShipmentInput, *, reason: str = ""
) -> FreightShipment:
    shipment = repository.get_freight_shipment(shipment_id)
    if not shipment:
        raise NotFoundError("Shipment not found")
    before = shipment.model_dump(mode="json")
    reopening = shipment.status in ("closed", "cancelled") and payload.status not in ("closed", "cancelled")
    if shipment.status in ("closed", "cancelled") and not reopening:
        changed_fields = {k for k, v in payload.model_dump(mode="json").items() if before.get(k) != v}
        if changed_fields - {"status", "notes"}:
            raise ConflictError(f"Shipment {shipment.number} is {shipment.status}. Reopen it before changing it.")
    if reopening and not reason.strip():
        raise ConflictError("Give a reason for reopening a closed or cancelled shipment")
    if payload.status in ("planning", "booked", "in_transit", "delivered", "closed") and shipment.status == "cancelled":
        with repository.connect() as connection:
            busy = repository.active_shipment_for_orders(
                connection, shipment.order_ids, exclude_shipment_id=shipment.id
            )
        if busy:
            raise ConflictError("Some orders are now in another shipment; this one cannot be reactivated")
    for key in type(payload).model_fields:
        setattr(shipment, key, getattr(payload, key))  # keep nested models typed (e.g. destination)
    if not (before["status"] in ("closed", "cancelled") and not reopening):
        resolve_customer_charge(shipment, current_combined_quote(repository, shipment))
    changes ={k: [before.get(k), v] for k, v in shipment.model_dump(mode="json").items()
               if k in type(payload).model_fields and before.get(k) != v}
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.write_freight_shipment(
            connection, shipment, action="reopened" if reopening else "updated",
            summary=(f"Reopened: {reason}" if reopening else ", ".join(changes) or "saved"),
            details={"changes": changes})
        if payload.status == "closed":
            for order_id in shipment.order_ids:
                repository.set_order_status(connection, order_id, "shipped", f"Shipped in {shipment.number}")
        elif before["status"] == "closed" and payload.status != "closed":
            for order_id in shipment.order_ids:
                row = connection.execute("SELECT payload_json FROM orders WHERE id=?", (order_id,)).fetchone()
                has_lines = bool(row and Order.model_validate_json(row[0]).lines)
                repository.set_order_status(connection, order_id, "ready" if has_lines else "reference_only",
                                            f"{shipment.number} reopened")
    return shipment


def change_shipment_orders(repository, shipment_id: str, *, add: list[str] | None = None,
                           remove: list[str] | None = None) -> FreightShipment:
    shipment = repository.get_freight_shipment(shipment_id)
    if not shipment:
        raise NotFoundError("Shipment not found")
    _require_editable(shipment)
    add = [oid for oid in (add or []) if oid not in shipment.order_ids]
    remove = [oid for oid in (remove or []) if oid in shipment.order_ids]
    if len(set(add)) != len(add):
        raise ConflictError("An order can be added to a shipment only once")
    new_orders = repository.get_orders(add)
    for order in new_orders:
        if order.company_id != shipment.company_id:
            raise ConflictError(f"Order {order.order_number} belongs to a different company")
        if order.status == "cancelled":
            raise ConflictError(f"Order {order.order_number} is cancelled")
    remaining = [oid for oid in shipment.order_ids if oid not in remove] + add
    if not remaining:
        raise ConflictError("A shipment needs at least one order; cancel the shipment instead")
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        busy = repository.active_shipment_for_orders(connection, add, exclude_shipment_id=shipment.id)
        if busy:
            raise ConflictError("Already in another shipment: " + ", ".join(busy.values()))
        shipment.order_ids = remaining
        numbers = {o.id: o.order_number for o in new_orders} | repository.order_numbers(remove)
        summary = "; ".join(filter(None, [
            f"added {', '.join(numbers[o] for o in add)}" if add else "",
            f"removed {', '.join(numbers.get(o, o) for o in remove)}" if remove else "",
        ]))
        repository.write_freight_shipment(connection, shipment, action="orders_changed", summary=summary,
                                          details={"added": add, "removed": remove})
        if remove and shipment.combined_quote_id:
            combined = repository.get_quote(shipment.combined_quote_id)
            if combined and set(combined.order_ids) & set(remove):
                shipment.combined_quote_id = None
                repository.write_freight_shipment(
                    connection, shipment, action="combined_quote_unlinked",
                    summary=f"{combined.number} no longer matches the orders; build or link a new combined quote")
        _clear_allocation(repository, connection, shipment.id, "the orders changed")
    return shipment


def _clear_allocation(repository, connection, shipment_id: str, why: str) -> None:
    if repository_allocation_exists(connection, shipment_id):
        repository.write_allocation(connection, None, shipment_id)
        repository.audit(connection, "shipment", shipment_id, "allocation_cleared",
                         f"Allocation cleared because {why}; recalculate it")


def repository_allocation_exists(connection, shipment_id: str) -> bool:
    return bool(connection.execute("SELECT 1 FROM expense_allocations WHERE shipment_id=?", (shipment_id,)).fetchone())


def link_combined_quote(repository, shipment_id: str, quote_id: str) -> FreightShipment:
    shipment = repository.get_freight_shipment(shipment_id)
    quote = repository.get_quote(quote_id)
    if not shipment or not quote:
        raise NotFoundError("Shipment or quote not found")
    if quote.company_id != shipment.company_id:
        raise ConflictError("The quote belongs to a different company")
    _require_editable(shipment)
    outside = set(quote.order_ids) - set(shipment.order_ids)
    if outside:
        raise ConflictError("The combined quote covers orders that are not in this shipment")
    previous_id = shipment.combined_quote_id
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if previous_id and previous_id != quote.id:
            previous = repository.get_quote(previous_id)
            if previous and previous.status == "final":
                previous.status = "superseded"
                repository._write_quote(connection, previous, action="superseded",
                                        summary=f"{previous.number} replaced as {shipment.number}'s combined quote")
        quote.freight_shipment_id = shipment.id
        quote.role = "combined"
        repository._write_quote(connection, quote, action="linked", summary=f"Combined quote for {shipment.number}")
        shipment.combined_quote_id = quote.id
        before_charge = shipment.customer_charge_cents
        if shipment.charge_source == "quote":
            resolve_customer_charge(shipment, quote)
        details = ({"changes": {"customer_charge_cents": [before_charge, shipment.customer_charge_cents]}}
                   if before_charge != shipment.customer_charge_cents else None)
        repository.write_freight_shipment(connection, shipment, action="combined_quote",
                                          summary=f"Combined quote {quote.number} linked", details=details)
    return shipment


def add_expense(repository, shipment_id: str, payload: ShipmentExpenseInput) -> ShipmentExpense:
    shipment = repository.get_freight_shipment(shipment_id)
    if not shipment:
        raise NotFoundError("Shipment not found")
    _require_editable(shipment)
    expense = ShipmentExpense(shipment_id=shipment.id, **payload.model_dump())
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.write_expense(connection, expense)
        _clear_allocation(repository, connection, shipment.id, "the freight cost changed")
        repository.audit(connection, "shipment", shipment.id, "expense_recorded",
                         f"{expense.kind} {expense.amount_cents / 100:,.2f} from {expense.vendor or 'unknown vendor'}",
                         {"expense_id": expense.id, **payload.model_dump(mode="json")})
    return expense


def void_expense(repository, shipment_id: str, expense_id: str, reason: str) -> ShipmentExpense:
    shipment = repository.get_freight_shipment(shipment_id)
    if not shipment:
        raise NotFoundError("Shipment not found")
    _require_editable(shipment)
    if not reason.strip():
        raise ConflictError("Give a reason for voiding an expense")
    expense = next((e for e in repository.shipment_expenses(shipment_id) if e.id == expense_id), None)
    if not expense:
        raise NotFoundError("Expense not found")
    if expense.voided:
        return expense
    expense.voided = True
    expense.void_reason = reason.strip()
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.write_expense(connection, expense)
        _clear_allocation(repository, connection, shipment.id, "the freight cost changed")
        repository.audit(connection, "shipment", shipment.id, "expense_voided",
                         f"Voided {expense.amount_cents / 100:,.2f}: {reason}", {"expense_id": expense.id})
    return expense


# ---------------------------------------------------------------------------- allocation


def split_cents(total_cents: int, basis: dict[str, float]) -> list[AllocationLine]:
    """Largest-remainder split that always sums to ``total_cents`` exactly.

    Works for credits (negative totals) by splitting the absolute value. Ties in the
    remainder go to the earlier order, so the result is deterministic.
    """

    keys = list(basis)
    weight_total = sum(basis.values())
    if not keys or weight_total <= 0 or any(value < 0 for value in basis.values()):
        raise ValueError("Every order needs a non-negative basis and the total basis must be above zero")
    sign = -1 if total_cents < 0 else 1
    magnitude = abs(total_cents)
    raw = {key: magnitude * basis[key] / weight_total for key in keys}
    floors = {key: math.floor(raw[key]) for key in keys}
    leftover = magnitude - sum(floors.values())
    order = sorted(keys, key=lambda key: (-(raw[key] - floors[key]), keys.index(key)))
    bonus = {key: 1 if index < leftover else 0 for index, key in enumerate(order)}
    return [
        AllocationLine(order_id=key, basis=round(basis[key], 4), share=round(basis[key] / weight_total, 6),
                       amount_cents=sign * (floors[key] + bonus[key]), rounding_adjustment_cents=sign * bonus[key])
        for key in keys
    ]


def separate_estimates(quotes: list[Quote], shipment: FreightShipment) -> tuple[int | None, bool]:
    """Sum of the current, priced quotes the shipment's orders had before being combined.

    Each quote counts once even when it covers several of the orders; superseded, void and
    combined quotes are ignored. Returns (total or None, whether every order is covered).
    """

    orders = set(shipment.order_ids)
    chosen: dict[str, Quote] = {}
    for quote in sorted(quotes, key=lambda q: q.created_at, reverse=True):
        if (quote.status != "final" or quote.role != "individual" or quote.id == shipment.combined_quote_id
                or quote.cost.amount_cents is None or not quote.order_ids or not set(quote.order_ids) <= orders):
            continue
        covered = {oid for q in chosen.values() for oid in q.order_ids}
        if set(quote.order_ids) & covered:
            continue
        chosen[quote.id] = quote
    if not chosen:
        return None, False
    covered = {oid for q in chosen.values() for oid in q.order_ids}
    return sum(q.cost.amount_cents for q in chosen.values()), covered == orders


def current_combined_quote(repository, shipment: FreightShipment) -> Quote | None:
    """The shipment's combined quote, unless it was voided."""

    quote = repository.get_quote(shipment.combined_quote_id) if shipment.combined_quote_id else None
    return quote if quote and quote.status != "void" else None


def allocation_basis(repository, shipment: FreightShipment, method: str) -> tuple[dict[str, float], str]:
    order_ids = shipment.order_ids
    if method == "equal":
        return {oid: 1.0 for oid in order_ids}, "One equal share per order"
    combined = current_combined_quote(repository, shipment)
    if combined and combined.kind == "packed":
        source = combined.summary.order_weights if method == "weight" else combined.summary.order_pallet_shares
        if all(source.get(oid, 0) > 0 for oid in order_ids):
            label = "packed item weight" if method == "weight" else "share of each pallet's weight"
            return {oid: float(source[oid]) for oid in order_ids}, f"Combined plan {combined.number}: {label}"
    basis: dict[str, float] = {}
    sources: list[str] = []
    for oid in order_ids:
        quotes = [q for q in repository.list_quotes(order_id=oid)
                  if q.status in ("final", "superseded") and q.role == "individual"]
        single = next((q for q in quotes if q.order_ids == [oid] and q.status == "final"), None) or \
            next((q for q in quotes if q.order_ids == [oid]), None)
        if single is None:
            raise ValueError(
                f"No {('weight' if method == 'weight' else 'pallet')} basis for "
                f"{repository.order_numbers([oid]).get(oid, oid)}: build a combined plan for the shipment or quote "
                "the order on its own, or use equal or manual allocation.")
        if method == "weight":
            basis[oid] = single.summary.item_weight or single.summary.total_weight
        else:
            basis[oid] = float(single.summary.pallets)
        sources.append(single.number)
    label = "item weight" if method == "weight" else "pallet count"
    return basis, f"Individual quotes ({', '.join(sources)}): {label}"


def expense_total(repository, shipment_id: str) -> int:
    return sum(e.amount_cents for e in repository.shipment_expenses(shipment_id) if not e.voided)


def allocate(repository, shipment_id: str, request: AllocationRequest) -> Allocation:
    shipment = repository.get_freight_shipment(shipment_id)
    if not shipment:
        raise NotFoundError("Shipment not found")
    _require_editable(shipment)
    total = expense_total(repository, shipment.id)
    if total == 0:
        raise ValueError("Record the shipment's freight expense before allocating it")
    if request.method == "manual":
        if set(request.manual_cents) != set(shipment.order_ids):
            raise ValueError("Enter an amount for every order in the shipment")
        if sum(request.manual_cents.values()) != total:
            difference = total - sum(request.manual_cents.values())
            raise ValueError(
                f"Manual amounts must add up to {total / 100:,.2f}; they are off by {difference / 100:,.2f}"
            )
        lines = [AllocationLine(order_id=oid, basis=request.manual_cents[oid] / 100,
                                share=round(request.manual_cents[oid] / total, 6) if total else 0,
                                amount_cents=request.manual_cents[oid]) for oid in shipment.order_ids]
        basis_source = "Entered by hand"
    else:
        basis, basis_source = allocation_basis(repository, shipment, request.method)
        lines = split_cents(total, basis)
    allocation = Allocation(shipment_id=shipment.id, method=request.method, total_cents=total,
                            basis_source=basis_source, lines=lines)
    assert sum(line.amount_cents for line in lines) == total  # reconciliation invariant
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.write_allocation(connection, allocation, shipment.id)
        repository.audit(connection, "shipment", shipment.id, "allocated",
                         f"{total / 100:,.2f} allocated by {request.method}",
                         {"lines": [line.model_dump() for line in lines], "basis": basis_source})
    return allocation


# ---------------------------------------------------------------------------- views


def shipment_view(repository, shipment: FreightShipment) -> dict[str, Any]:
    orders = repository.get_orders(shipment.order_ids)
    expenses = repository.shipment_expenses(shipment.id)
    total = sum(e.amount_cents for e in expenses if not e.voided)
    allocation = repository.get_allocation(shipment.id)
    combined = current_combined_quote(repository, shipment)
    quote_numbers = repository.order_numbers(o.id for o in orders)
    individual: dict[str, list[dict]] = {}
    related: dict[str, Quote] = {}
    for order in orders:
        quotes = [q for q in repository.list_quotes(order_id=order.id) if q.id != shipment.combined_quote_id]
        related.update({q.id: q for q in quotes})
        individual[order.id] = [quote_view(repository, q) for q in quotes]
    separate_total, separate_complete = separate_estimates(list(related.values()), shipment)
    stale = bool(allocation and (allocation.total_cents != total
                                 or {line.order_id for line in allocation.lines} != set(shipment.order_ids)))
    revenue = repository.list_revenue(shipment_id=shipment.id)
    extra_revenue = sum(r.amount_cents for r in revenue if not r.voided)
    has_bills = any(not e.voided for e in expenses)
    reference = reference_quote(shipment, combined, related.values())
    comparison = shipment_comparison(shipment, reference, actual_cost_cents=total if has_bills else None,
                                     extra_revenue_cents=extra_revenue)
    return {
        **shipment.model_dump(mode="json"),
        "orders": [
            {**o.model_dump(mode="json", exclude={"lines"}), "unit_count": o.unit_count, "line_count": len(o.lines)}
            for o in orders
        ],
        "warnings": _shipment_warnings(orders),
        "individual_quotes": individual,
        "combined_quote": quote_view(repository, combined, quote_numbers) if combined else None,
        "expenses": [e.model_dump(mode="json") for e in expenses],
        "expense_total_cents": total,
        "allocation": allocation.model_dump(mode="json") if allocation else None,
        "allocation_stale": stale,
        "revenue": [r.model_dump(mode="json") for r in revenue],
        "revenue_total_cents": extra_revenue,
        "comparison": {
            "separate_estimates_cents": separate_total if separate_complete else None,
            "separate_estimates_partial_cents": separate_total,
            "combined_estimate_cents": combined.cost.amount_cents if combined else None,
            "actual_cents": total if has_bills else None,
            "customer_charge_cents": shipment.customer_charge_cents,
            **comparison,
        },
        "locked": shipment.status in ("closed", "cancelled"),
        "history": [entry.model_dump(mode="json") for entry in repository.audit_trail("shipment", shipment.id)],
    }


def order_trace(repository, order: Order) -> dict[str, Any]:
    """Order → original quotes → shipment → expense and allocated share."""

    quotes = [quote_view(repository, q) for q in repository.list_quotes(order_id=order.id)]
    shipment = None
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT s.id FROM shipment_orders so JOIN freight_shipments s ON s.id=so.shipment_id "
            "WHERE so.order_id=? AND s.status<>'cancelled' ORDER BY s.created_at DESC LIMIT 1", (order.id,)
        ).fetchone()
    if row:
        found = repository.get_freight_shipment(row[0])
        allocation = repository.get_allocation(found.id)
        share = next((line for line in allocation.lines if line.order_id == order.id), None) if allocation else None
        shipment = {
            "id": found.id, "number": found.number, "status": found.status, "name": found.name,
            "carrier": found.carrier, "expense_total_cents": expense_total(repository, found.id),
            "order_count": len(found.order_ids), "allocated_cents": share.amount_cents if share else None,
            "allocation_method": allocation.method if allocation else None,
        }
    source = repository.get_source_file(order.source_file_id) if order.source_file_id else None
    return {
        "order": {**order.model_dump(mode="json"), "unit_count": order.unit_count},
        "source_file": source.model_dump(mode="json", exclude={"stored_path"}) if source else None,
        "quotes": quotes,
        "shipment": shipment,
        "revisions": repository.order_revisions(order.id),
        "history": [entry.model_dump(mode="json") for entry in repository.audit_trail("order", order.id)],
    }


def dashboard(repository, company_id: str | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    quotes = repository.list_quotes(company_id=company_id, limit=100000)
    shipments = repository.list_freight_shipments(company_id=company_id, limit=100000)
    with repository.connect() as connection:
        order_counts = dict(connection.execute(
            "SELECT status, COUNT(*) FROM orders" + (" WHERE company_id=?" if company_id else "") + " GROUP BY status",
            (company_id,) if company_id else ()).fetchall())
        unshipped_quoted = connection.execute(
            "SELECT COUNT(DISTINCT qo.order_id) FROM quote_orders qo JOIN quotes q ON q.id=qo.quote_id "
            "WHERE q.status='final' AND COALESCE(json_extract(q.payload_json,'$.legacy'),0)=0 "
            "AND NOT EXISTS (SELECT 1 FROM shipment_orders so JOIN freight_shipments s "
            "ON s.id=so.shipment_id WHERE so.order_id=qo.order_id AND s.status<>'cancelled')").fetchone()[0]
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        month_expense = connection.execute(
            "SELECT COALESCE(SUM(amount_cents),0) FROM shipment_expenses WHERE voided=0 AND created_at>=?",
            (month_start,)).fetchone()[0]
    expiring = []
    for quote in quotes:
        expires = quote.cost.expires_on
        if quote.status == "final" and expires and 0 <= (expires - now.date()).days <= 7:
            expiring.append({"id": quote.id, "number": quote.number, "name": quote.name,
                             "expires_on": expires.isoformat()})
    open_shipments = [s for s in shipments if s.status in ("planning", "booked", "in_transit", "delivered")]
    expense_sums = repository.expense_sums_by_shipment(company_id)
    without_expense = [s for s in open_shipments if s.status in ("in_transit", "delivered")
                       and expense_sums.get(s.id, (0, 0))[0] == 0]
    recent_quotes = list_quote_views(repository, company_id=company_id, limit=8)
    from .attention import attention

    needs_attention = attention(repository, company_id, now=now)
    return {
        "attention": {"counts": needs_attention["counts"], "total": needs_attention["total"],
                      "items": needs_attention["items"][:10]},
        "quote_answers": {
            # An answered quote counts even after a newer quote replaced it (declined, then re-quoted).
            status: sum(q.customer_status == status for q in quotes if q.status != "void"
                        and (q.status != "superseded" or status in ("approved", "declined", "expired")))
            for status in ("not_sent", "sent", "approved", "declined", "expired")
        },
        "counts": {
            "orders_needing_review": order_counts.get("needs_review", 0),
            "orders_ready": order_counts.get("ready", 0),
            "quoted_orders_not_shipped": unshipped_quoted,
            "open_shipments": len(open_shipments),
            "shipments_missing_expense": len(without_expense),
            "quotes_this_month": sum(q.created_at >= now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                                     for q in quotes),
            "expense_this_month_cents": month_expense,
        },
        "expiring_quotes": expiring[:10],
        "recent_quotes": recent_quotes,
        "open_shipments": [
            {"id": s.id, "number": s.number, "name": s.name, "status": s.status, "carrier": s.carrier,
             "order_count": len(s.order_ids), "pickup_date": s.pickup_date.isoformat() if s.pickup_date else None}
            for s in open_shipments[:10]
        ],
        "shipments_missing_expense": [{"id": s.id, "number": s.number, "name": s.name} for s in without_expense[:10]],
        "activity": [entry.model_dump(mode="json") for entry in repository.recent_activity(15)],
    }


def legacy_backfill(repository) -> dict[str, int]:
    """Create quote records for plans and measured quotes saved before 3.0.

    Idempotent: only sources without a quote are processed, oldest first, so quote
    numbers follow history. Orders are created as reference-only records from the
    references typed into each name. When several legacy quotes cover the same
    orders, the latest stays final and earlier ones are marked superseded.
    """

    company = repository.default_company()
    with repository.connect() as connection:
        sources = [("packed", row[0], row[1]) for row in connection.execute(
            "SELECT id, created_at FROM shipments WHERE id NOT IN (SELECT packing_result_id FROM quotes "
            "WHERE packing_result_id IS NOT NULL)")]
        for row in connection.execute(
            "SELECT id, payload_json FROM manual_quotes WHERE id NOT IN (SELECT manual_quote_id FROM quotes "
            "WHERE manual_quote_id IS NOT NULL)"):
            import json as _json

            sources.append(("measured", row[0], _json.loads(row[1]).get("created_at") or ""))
    created = 0
    for kind, source_id, _ in sorted(sources, key=lambda item: str(item[2])):
        if kind == "packed":
            result = repository.get_shipment(source_id)
            if not result or result.purpose != "production":
                continue
            refs = (result.shipping_info.customer_references if result.shipping_info else []) or \
                references_from_name(company, result.shipment_name)
            orders = _resolve_orders(repository, company, [], refs, reason="Created from saved pallet plan history",
                                     history=True)
            quote = Quote(company_id=company.id, kind="packed", name=result.shipment_name, packing_result_id=result.id,
                          order_ids=[o.id for o in orders], summary=summarize_packed(result, orders), legacy=True,
                          created_at=result.created_at, status="final" if result.freight_ready else "draft")
        else:
            value = repository.get_manual_quote(source_id)
            if not value:
                continue
            orders = _resolve_orders(repository, company, [], references_from_name(company, value.shipment_name),
                                     reason="Created from measured-quote history", history=True)
            quote = Quote(company_id=company.id, kind="measured", name=value.shipment_name, manual_quote_id=value.id,
                          order_ids=[o.id for o in orders], summary=summarize_manual(value), legacy=True,
                          created_at=value.created_at)
        repository.save_quote(quote, action="imported", summary=f"Imported from history: {quote.name}")
        if quote.status == "final":
            _supersede_previous(repository, quote)
        created += 1
    return {"created": created}


QuoteCostType = QuoteCost  # re-export for API typing
