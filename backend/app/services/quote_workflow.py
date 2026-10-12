"""3.2 freight workflow (contract: docs/WORKFLOW.md).

Quote terms and price options, the customer's answer, approval → shipment, actual measurements,
extra revenue and freight bills without a shipment. Every change is one transaction with an audit
entry; closed and cancelled shipments stay locked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..freight_models import (
    ActualMeasurements,
    DeliveryTerms,
    FreightShipment,
    Order,
    PricingRule,
    Quote,
    QuoteOption,
    QuoteOptionInput,
    ShipmentExpense,
    ShipmentExpenseInput,
    ShipmentRevenue,
    ShipmentRevenueInput,
    UnassignedExpense,
)
from ..freight_repository import ConflictError, NotFoundError
from ..models import AddressInfo
from .adapters.base import canonical_reference
from .freight import (
    _clear_allocation,
    _shipment_in,
    money,
    quote_view,
    resolve_customer_charge,
    sync_shipment_charge,
)

MAX_OPTIONS = 30
LOCKED = ("closed", "cancelled")


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------- loading


def _quote_in(connection, quote_id: str) -> Quote | None:
    row = connection.execute("SELECT payload_json FROM quotes WHERE id=? OR number=?", (quote_id, quote_id)).fetchone()
    return Quote.model_validate_json(row[0]) if row else None


def load_quote(repository, quote_id: str) -> Quote:
    quote = repository.get_quote(quote_id)
    if quote is None:
        raise NotFoundError("Quote not found")
    return quote


def _editable_quote(repository, quote_id: str) -> Quote:
    quote = load_quote(repository, quote_id)
    if quote.status == "void":
        raise ConflictError(f"{quote.number} is void; record a new quote instead")
    return quote


def _shipment(repository, shipment_id: str) -> FreightShipment:
    shipment = repository.get_freight_shipment(shipment_id)
    if shipment is None:
        raise NotFoundError("Shipment not found")
    return shipment


def _require_unlocked(shipment: FreightShipment, what: str = "changing it") -> None:
    if shipment.status in LOCKED:
        raise ConflictError(f"Shipment {shipment.number} is {shipment.status}. Reopen it before {what}.")


def _save_quote(repository, quote: Quote, *, action: str, summary: str, details: dict | None = None,
                price_changed: bool = False) -> Quote:
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository._write_quote(connection, quote, action=action, summary=summary, details=details)
        if price_changed:
            sync_shipment_charge(repository, connection, quote)
    return quote


# ---------------------------------------------------------------------------- pricing and options


def pricing_rule(repository, company_id: str | None) -> PricingRule:
    return repository.get_company(company_id).config.pricing


def pricing_preview(repository, company_id: str | None, carrier_cents: int,
                    rule: PricingRule | None = None) -> dict[str, Any]:
    rule = rule or pricing_rule(repository, company_id)
    price = rule.price(carrier_cents)
    return {"carrier_cents": carrier_cents, "customer_price_cents": price, "markup_cents": price - carrier_cents,
            "rule": rule.model_dump()}


def apply_selection(quote: Quote, rule: PricingRule) -> None:
    """Copy the selected option into ``cost`` and price it; no selection clears those fields.

    A customer price typed by hand (``price_manual``) is never replaced.
    """

    option = quote.selected_option
    cost = quote.cost
    if option is None:
        quote.selected_option_id = None
        cost.amount_cents = None
        cost.carrier = cost.broker = cost.reference = ""
        cost.expires_on = None
        if not quote.price_manual:
            cost.customer_price_cents = None
        return
    cost.amount_cents = option.price_cents
    cost.carrier = option.carrier
    cost.broker = option.source_label
    cost.reference = option.reference
    cost.expires_on = option.expires_on
    if not quote.price_manual:
        cost.customer_price_cents = rule.price(option.price_cents)


def _option_label(option: QuoteOption) -> str:
    parts = [option.source_label or option.source, option.carrier]
    return f"{' · '.join(p for p in parts if p)} {money(option.price_cents)}".strip()


def add_option(repository, quote_id: str, payload: QuoteOptionInput, *, select: bool = False) -> Quote:
    quote = _editable_quote(repository, quote_id)
    if len(quote.options) >= MAX_OPTIONS:
        raise ValueError(f"A quote can hold at most {MAX_OPTIONS} price options")
    option = QuoteOption(**payload.model_dump())
    quote.options.append(option)
    before_price = quote.cost.customer_price_cents
    if select:
        quote.selected_option_id = option.id
        apply_selection(quote, pricing_rule(repository, quote.company_id))
    summary = f"{quote.number}: option {_option_label(option)} added" + (" and selected" if select else "")
    return _save_quote(repository, quote, action="option_added", summary=summary,
                       details={"option": option.model_dump(mode="json"), "selected": select},
                       price_changed=quote.cost.customer_price_cents != before_price)


def _find_option(quote: Quote, option_id: str) -> int:
    index = next((i for i, option in enumerate(quote.options) if option.id == option_id), None)
    if index is None:
        raise NotFoundError("Price option not found")
    return index


def edit_option(repository, quote_id: str, option_id: str, payload: QuoteOptionInput) -> Quote:
    quote = _editable_quote(repository, quote_id)
    index = _find_option(quote, option_id)
    previous = quote.options[index]
    updated = QuoteOption(**payload.model_dump(), id=previous.id, recorded_at=previous.recorded_at)
    if updated == previous:
        return quote
    quote.options[index] = updated
    before_price = quote.cost.customer_price_cents
    if quote.selected_option_id == option_id:
        apply_selection(quote, pricing_rule(repository, quote.company_id))
    changes = {k: [v, updated.model_dump(mode="json")[k]] for k, v in previous.model_dump(mode="json").items()
               if updated.model_dump(mode="json")[k] != v}
    return _save_quote(repository, quote, action="option_updated",
                       summary=f"{quote.number}: option {_option_label(updated)} changed ({', '.join(changes)})",
                       details={"option_id": option_id, "changes": changes},
                       price_changed=quote.cost.customer_price_cents != before_price)


def remove_option(repository, quote_id: str, option_id: str) -> Quote:
    quote = _editable_quote(repository, quote_id)
    index = _find_option(quote, option_id)
    option = quote.options.pop(index)
    before_price = quote.cost.customer_price_cents
    was_selected = quote.selected_option_id == option_id
    if was_selected:
        quote.selected_option_id = None
        apply_selection(quote, pricing_rule(repository, quote.company_id))
    return _save_quote(repository, quote, action="option_removed",
                       summary=f"{quote.number}: option {_option_label(option)} removed"
                               + (" (it was the selected price)" if was_selected else ""),
                       details={"option": option.model_dump(mode="json"), "was_selected": was_selected},
                       price_changed=quote.cost.customer_price_cents != before_price)


def select_option(repository, quote_id: str, option_id: str | None) -> Quote:
    quote = _editable_quote(repository, quote_id)
    if option_id is not None:
        _find_option(quote, option_id)
    if option_id == quote.selected_option_id and option_id is not None:
        return quote
    before_price = quote.cost.customer_price_cents
    quote.selected_option_id = option_id
    apply_selection(quote, pricing_rule(repository, quote.company_id))
    option = quote.selected_option
    summary = (f"{quote.number}: selected {_option_label(option)} → customer price "
               f"{money(quote.cost.customer_price_cents)}" if option else f"{quote.number}: price option cleared")
    return _save_quote(repository, quote, action="option_selected", summary=summary,
                       details={"option_id": option_id, "cost": quote.cost.model_dump(mode="json")},
                       price_changed=quote.cost.customer_price_cents != before_price)


def set_customer_price(repository, quote_id: str, cents: int | None) -> Quote:
    """A typed customer price (``price_manual``); ``None`` returns the quote to the pricing rule."""

    quote = _editable_quote(repository, quote_id)
    before = quote.cost.customer_price_cents
    if cents is None:
        quote.price_manual = False
        option = quote.selected_option
        quote.cost.customer_price_cents = (pricing_rule(repository, quote.company_id).price(option.price_cents)
                                           if option else None)
        priced = money(quote.cost.customer_price_cents) or "no price"
        summary = f"{quote.number}: customer price back to the pricing rule ({priced})"
    else:
        quote.price_manual = True
        quote.cost.customer_price_cents = cents
        summary = f"{quote.number}: customer price set by hand to {money(cents)}"
    return _save_quote(repository, quote, action="price_set", summary=summary,
                       details={"changes": {"customer_price_cents": [before, quote.cost.customer_price_cents]},
                                "price_manual": quote.price_manual},
                       price_changed=quote.cost.customer_price_cents != before)


def set_terms(repository, quote_id: str, terms: DeliveryTerms) -> Quote:
    quote = _editable_quote(repository, quote_id)
    terms = DeliveryTerms(**terms.model_dump())
    if terms == quote.terms:
        return quote
    before = quote.terms.model_dump(mode="json") if quote.terms else None
    quote.terms = terms
    after = terms.model_dump(mode="json")
    changes = {k: [before.get(k) if before else None, v] for k, v in after.items() if not before or before[k] != v}
    note = " (quote already approved)" if quote.customer_status == "approved" else ""
    return _save_quote(repository, quote, action="terms_updated",
                       summary=f"{quote.number}: delivery terms {', '.join(terms.labels())} to "
                               f"{terms.postal_code or 'no ZIP'}{note}",
                       details={"changes": changes})


# ---------------------------------------------------------------------------- customer answer


def _active_linked_shipment(repository, quote: Quote) -> FreightShipment | None:
    if not quote.freight_shipment_id:
        return None
    shipment = repository.get_freight_shipment(quote.freight_shipment_id)
    if shipment and shipment.combined_quote_id == quote.id and shipment.status != "cancelled":
        return shipment
    return None


def mark_sent(repository, quote_id: str, note: str = "") -> Quote:
    quote = _editable_quote(repository, quote_id)
    if quote.customer_status in ("approved", "declined", "expired"):
        raise ConflictError(f"{quote.number} is already {quote.customer_status}"
                            + ("" if quote.customer_status == "approved" else "; reopen it to send it again"))
    again = quote.customer_status == "sent"
    quote.customer_status = "sent"
    quote.sent_at = _now()
    if note:
        quote.response_note = note
    return _save_quote(repository, quote, action="sent",
                       summary=f"{quote.number} {'sent again' if again else 'sent'} to the customer"
                               + (f" at {money(quote.cost.customer_price_cents)}"
                                  if quote.cost.customer_price_cents is not None else ""))


def _answer(repository, quote_id: str, status: str, note: str) -> Quote:
    quote = _editable_quote(repository, quote_id)
    if quote.customer_status == "approved":
        if status == "expired":
            raise ConflictError(f"{quote.number} was approved; an approved quote cannot expire")
        linked = _active_linked_shipment(repository, quote)
        if linked:
            raise ConflictError(f"{quote.number} was approved and is the quote of shipment {linked.number}; "
                                "cancel that shipment first")
    if quote.customer_status == "declined" and status == "expired":
        raise ConflictError(f"{quote.number} was declined")
    previous = quote.customer_status
    quote.customer_status = status  # type: ignore[assignment]
    quote.responded_at = _now()
    quote.response_note = note
    word = {"declined": "declined by the customer", "expired": "marked expired"}[status]
    return _save_quote(repository, quote, action=status,
                       summary=f"{quote.number} {word}" + (f": {note}" if note else ""),
                       details={"previous": previous, "note": note})


def decline(repository, quote_id: str, note: str = "") -> Quote:
    return _answer(repository, quote_id, "declined", note)


def expire(repository, quote_id: str, note: str = "") -> Quote:
    return _answer(repository, quote_id, "expired", note)


def reopen(repository, quote_id: str, note: str = "") -> Quote:
    quote = _editable_quote(repository, quote_id)
    if quote.customer_status in ("not_sent", "sent"):
        raise ConflictError(f"{quote.number} is still open")
    if quote.customer_status == "approved":
        linked = _active_linked_shipment(repository, quote)
        if linked:
            raise ConflictError(f"{quote.number} is the approved quote of shipment {linked.number}; "
                                "cancel that shipment before reopening the quote")
    previous = quote.customer_status
    quote.customer_status = "sent"
    quote.sent_at = quote.sent_at or _now()
    quote.responded_at = None
    quote.response_note = note
    return _save_quote(repository, quote, action="reopened",
                       summary=f"{quote.number} reopened (was {previous})" + (f": {note}" if note else ""),
                       details={"previous": previous, "note": note})


def _invoice_plan(quote: Quote, prefix: str, invoice_numbers: list[str],
                  order_invoices: dict[str, list[str]] | None) -> dict[str, list[str]]:
    def canonical(value: str) -> str:
        reference = canonical_reference(value, prefix)
        if not reference:
            raise ValueError(f"'{value}' is not a valid invoice number")
        return reference

    plan: dict[str, list[str]] = {order_id: [] for order_id in quote.order_ids}
    for value in invoice_numbers:
        reference = canonical(value)
        for order_id in quote.order_ids:
            plan[order_id].append(reference)
    for order_id, values in (order_invoices or {}).items():
        if order_id not in plan:
            raise ValueError("Invoice numbers can only be added to the quote's own orders")
        plan[order_id] += [canonical(value) for value in values]
    return {order_id: list(dict.fromkeys(refs)) for order_id, refs in plan.items()}


def _destination(orders, terms: DeliveryTerms | None) -> AddressInfo:
    zips = {o.ship_to.postal_code for o in orders if o.ship_to.postal_code}
    base = next(o.ship_to for o in orders if o.ship_to.postal_code) if len(zips) == 1 else AddressInfo()
    if terms and terms.postal_code and terms.postal_code != base.postal_code:
        return AddressInfo(postal_code=terms.postal_code, city=terms.city, state=terms.state)
    if terms:
        return base.model_copy(update={k: v for k, v in (("city", terms.city), ("state", terms.state))
                                       if v and not getattr(base, k)})
    return base


def approve(repository, quote_id: str, *, invoice_numbers: list[str] | None = None,
            order_invoices: dict[str, list[str]] | None = None,
            note: str = "") -> tuple[Quote, FreightShipment, bool]:
    """Customer approved: invoice numbers become order references, and the quote becomes the combined quote
    of the orders' shipment (created if they have none). Returns (quote, shipment, created)."""

    quote = _editable_quote(repository, quote_id)
    if quote.cost.customer_price_cents is None and quote.selected_option is None:
        raise ValueError("Select the price option that was used, or enter the customer price, before approving")
    if not quote.order_ids:
        raise ValueError("Link the quote to its order(s) before approving it")
    company = repository.get_company(quote.company_id)
    plan = _invoice_plan(quote, company.config.bare_number_prefix, invoice_numbers or [], order_invoices)
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        quote = _quote_in(connection, quote.id) or quote
        option = quote.selected_option
        orders: list[Order] = []
        for order_id in quote.order_ids:
            row = connection.execute("SELECT payload_json FROM orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise NotFoundError(f"Order {order_id} not found")
            orders.append(Order.model_validate_json(row[0]))
        numbers = {o.id: o.order_number for o in orders}
        added: dict[str, list[str]] = {}
        for order in orders:
            known = {value.upper() for value in [order.order_number, *order.references]}
            new = [ref for ref in plan[order.id] if ref.upper() not in known]
            for reference in new:
                owner = repository.reference_owner(connection, company.id, reference)
                if owner and owner not in quote.order_ids:
                    owner_number = connection.execute("SELECT order_number FROM orders WHERE id=?",
                                                      (owner,)).fetchone()
                    raise ConflictError(f"Invoice {reference} already belongs to order "
                                        f"{owner_number[0] if owner_number else owner}")
            if new:
                order.references = [*order.references, *new]
                order.revision += 1
                repository._write_order(connection, order)
                repository.audit(connection, "order", order.id, "references_added",
                                 f"Invoice {', '.join(new)} added (quote {quote.number} approved)",
                                 {"references": new, "quote_id": quote.id})
                added[order.id] = new

        busy = repository.active_shipments_by_order(connection, quote.order_ids)
        shipment_ids = set(busy.values())
        created = False
        if shipment_ids:
            if len(shipment_ids) > 1 or len(busy) != len(quote.order_ids):
                shipment_numbers = {sid: (found.number if (found := _shipment_in(connection, sid)) else sid)
                                    for sid in shipment_ids}
                spread = ", ".join(
                    f"{numbers[oid]} on {shipment_numbers[busy[oid]]}" if oid in busy else f"{numbers[oid]} on none"
                    for oid in quote.order_ids)
                raise ConflictError(f"The quote's orders are not on one shipment ({spread}); put them on the same "
                                    "shipment, or approve a quote for exactly that shipment's orders")
            shipment = _shipment_in(connection, next(iter(shipment_ids)))
            _require_unlocked(shipment, "approving a quote for it")
            previous_id = shipment.combined_quote_id
            if previous_id and previous_id != quote.id:
                previous = _quote_in(connection, previous_id)
                if previous and previous.status == "final":
                    previous.status = "superseded"
                    repository._write_quote(connection, previous, action="superseded",
                                            summary=f"{previous.number} replaced by approved {quote.number} "
                                                    f"as {shipment.number}'s quote")
            before_charge = shipment.customer_charge_cents
            shipment.combined_quote_id = quote.id
            if shipment.charge_source == "quote":
                resolve_customer_charge(shipment, quote)
            if option and not shipment.carrier:
                shipment.carrier = option.carrier
            if option and not shipment.broker:
                shipment.broker = option.source_label
            repository.write_freight_shipment(
                connection, shipment, action="combined_quote",
                summary=f"Approved quote {quote.number} linked",
                details={"changes": {"customer_charge_cents": [before_charge, shipment.customer_charge_cents]}}
                if before_charge != shipment.customer_charge_cents else None)
        else:
            cancelled = [o.order_number for o in orders if o.status == "cancelled"]
            if cancelled:
                raise ConflictError(f"Order {', '.join(cancelled)} is cancelled")
            shipment = FreightShipment(
                company_id=company.id, order_ids=list(quote.order_ids), status="planning",
                name=" + ".join(o.order_number for o in orders), combined_quote_id=quote.id, charge_source="quote",
                customer_charge_cents=quote.cost.customer_price_cents,
                carrier=option.carrier if option else quote.cost.carrier,
                broker=option.source_label if option else quote.cost.broker,
                destination=_destination(orders, quote.terms))
            repository.write_freight_shipment(connection, shipment, action="created",
                                              summary=f"Shipment created from approved quote {quote.number}",
                                              details={"order_ids": shipment.order_ids, "quote_id": quote.id})
            created = True
        first_approval = quote.customer_status != "approved"
        quote.freight_shipment_id = shipment.id
        quote.role = "combined"
        quote.customer_status = "approved"
        if first_approval or quote.responded_at is None:
            quote.responded_at = _now()
        if note:
            quote.response_note = note
        invoices = sorted({ref for refs in added.values() for ref in refs})
        summary = (f"{quote.number} {'approved' if first_approval else 'approval updated'} at "
                   f"{money(quote.cost.customer_price_cents) or 'no price'}; shipment {shipment.number}"
                   + (" created" if created else " linked") + (f"; invoice {', '.join(invoices)}" if invoices else ""))
        repository._write_quote(connection, quote, action="approved", summary=summary,
                                details={"shipment_id": shipment.id, "shipment_created": created,
                                         "invoices": added, "note": note})
    return quote, shipment, created


# ---------------------------------------------------------------------------- views


def quote_detail(repository, quote: Quote, numbers: dict[str, str] | None = None,
                 shipments: dict[str, FreightShipment] | None = None) -> dict[str, Any]:
    view = quote_view(repository, quote, numbers)
    terms = quote.terms
    terms_source = "recorded" if terms else None
    if terms is None:
        terms = _terms_from_source(repository, quote)
        terms_source = "source" if terms else None
    shipment = None
    if quote.freight_shipment_id:
        found = (shipments or {}).get(quote.freight_shipment_id) or repository.get_freight_shipment(
            quote.freight_shipment_id)
        if found:
            shipment = {"id": found.id, "number": found.number, "status": found.status,
                        "is_combined_quote": found.combined_quote_id == quote.id}
    view.update({
        "terms": terms.model_dump(mode="json") if terms else None,
        "terms_labels": terms.labels() if terms else [],
        "terms_source": terms_source,
        "selected_option": quote.selected_option.model_dump(mode="json") if quote.selected_option else None,
        "counted": counts_as_quoted(quote),
        "shipment": shipment,
    })
    return view


def _terms_from_source(repository, quote: Quote) -> DeliveryTerms | None:
    """Quotes recorded before 3.2 have no terms; show them from the saved plan or measured quote."""

    try:
        if quote.packing_result_id:
            source = repository.get_shipment(quote.packing_result_id)
        elif quote.manual_quote_id:
            source = repository.get_manual_quote(quote.manual_quote_id)
        else:
            return None
    except Exception:  # pragma: no cover - an unreadable legacy source just has no terms
        return None
    info = getattr(source, "shipping_info", None) if source else None
    return DeliveryTerms.from_shipping(info) if info is not None else None


def counts_as_quoted(quote: Quote) -> bool:
    """One "time quoted": not void, and priced for the customer or sent."""

    return quote.status != "void" and (
        quote.cost.customer_price_cents is not None or quote.sent_at is not None
        or quote.customer_status != "not_sent")


def quote_history(repository, order_id: str) -> dict[str, Any]:
    order = repository.get_order(order_id)
    if order is None:
        raise NotFoundError("Order not found")
    quotes = [q for q in repository.list_quotes(order_id=order.id, limit=1000) if q.status != "void"]
    quotes.sort(key=lambda q: (q.created_at, q.number), reverse=True)
    numbers = repository.order_numbers(oid for q in quotes for oid in q.order_ids)
    shipment_ids = {q.freight_shipment_id for q in quotes if q.freight_shipment_id}
    shipments = {sid: s for sid in shipment_ids if (s := repository.get_freight_shipment(sid))}
    views = [quote_detail(repository, q, numbers, shipments) for q in quotes]
    counted = [v for v in views if v["counted"]]
    for position, view in enumerate(reversed(counted), start=1):
        view["time_quoted"] = position  # 1 = first time this order was quoted
    for view in views:
        view.setdefault("time_quoted", None)
    latest_terms = next((v for v in views if v["terms"]), None)
    approved = next((q for q in quotes if q.customer_status == "approved"), None)
    return {
        "order": {"id": order.id, "order_number": order.order_number, "references": order.references,
                  "customer_name": order.customer_name, "status": order.status, "sales_rep": order.sales_rep,
                  "ship_to": order.ship_to.model_dump(), "customer_emails": order.customer_emails},
        "quoted_count": len(counted),
        "latest_terms": latest_terms["terms"] if latest_terms else None,
        "latest_terms_labels": latest_terms["terms_labels"] if latest_terms else [],
        "approved_quote_id": approved.id if approved else None,
        "quotes": views,
    }


# ---------------------------------------------------------------------------- shipments: actuals, revenue, bills


def record_actuals(repository, shipment_id: str, actual: ActualMeasurements | None) -> FreightShipment:
    shipment = _shipment(repository, shipment_id)
    _require_unlocked(shipment, "recording what shipped")
    cleared = actual is None or not (actual.pallets or actual.billed_weight or actual.billed_class or actual.notes)
    value = None if cleared else ActualMeasurements(**actual.model_dump())
    if value == shipment.actual:
        return shipment
    shipment.actual = value
    if value is None:
        summary = "Actual measurements cleared"
    else:
        parts = []
        if value.pallets:
            parts.append(f"{value.pallet_count} pallet(s), {value.total_weight:,.0f} lb measured")
        if value.billed_weight:
            parts.append(f"billed {value.billed_weight:,.0f} lb" + (f" class {value.billed_class}"
                                                                     if value.billed_class else ""))
        summary = "Actual: " + ("; ".join(parts) or "notes")
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.write_freight_shipment(connection, shipment, summary=summary,
                                          action="actuals_recorded" if value else "actuals_cleared",
                                          details={"actual": value.model_dump(mode="json") if value else None})
    return shipment


def add_revenue(repository, shipment_id: str, payload: ShipmentRevenueInput) -> ShipmentRevenue:
    shipment = _shipment(repository, shipment_id)
    _require_unlocked(shipment, "recording revenue")
    revenue = ShipmentRevenue(company_id=shipment.company_id, shipment_id=shipment.id,
                              **payload.model_dump())
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.write_revenue(connection, revenue)
        repository.audit(connection, "shipment", shipment.id, "revenue_recorded",
                         f"{revenue.kind.replace('_', ' ')} {money(revenue.amount_cents)}"
                         + (f" from {revenue.payer}" if revenue.payer else ""),
                         {"revenue_id": revenue.id, **payload.model_dump(mode="json")})
    return revenue


def add_company_revenue(repository, company_id: str | None, payload: ShipmentRevenueInput,
                        shipment_id: str | None = None) -> ShipmentRevenue:
    if shipment_id:
        shipment = _shipment(repository, shipment_id)
        if company_id and repository.get_company(company_id).id != shipment.company_id:
            raise ConflictError("The shipment belongs to a different company")
        return add_revenue(repository, shipment_id, payload)
    company = repository.get_company(company_id)
    revenue = ShipmentRevenue(company_id=company.id, **payload.model_dump())
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.write_revenue(connection, revenue)
        repository.audit(connection, "revenue", revenue.id, "recorded",
                         f"Unassigned {revenue.kind.replace('_', ' ')} {money(revenue.amount_cents)}",
                         payload.model_dump(mode="json"))
    return revenue


def _revenue(repository, revenue_id: str) -> ShipmentRevenue:
    revenue = repository.get_revenue(revenue_id)
    if revenue is None:
        raise NotFoundError("Revenue entry not found")
    return revenue


def assign_revenue(repository, revenue_id: str, shipment_id: str) -> ShipmentRevenue:
    revenue = _revenue(repository, revenue_id)
    if revenue.voided:
        raise ConflictError("This revenue entry is void")
    if revenue.shipment_id:
        raise ConflictError("This revenue entry already belongs to a shipment")
    shipment = _shipment(repository, shipment_id)
    if shipment.company_id != revenue.company_id:
        raise ConflictError("The shipment belongs to a different company")
    _require_unlocked(shipment, "recording revenue")
    revenue.shipment_id = shipment.id
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.write_revenue(connection, revenue)
        repository.audit(connection, "shipment", shipment.id, "revenue_assigned",
                         f"{revenue.kind.replace('_', ' ')} {money(revenue.amount_cents)} assigned",
                         {"revenue_id": revenue.id})
        repository.audit(connection, "revenue", revenue.id, "assigned", f"Assigned to {shipment.number}",
                         {"shipment_id": shipment.id})
    return revenue


def void_revenue(repository, revenue_id: str, reason: str, shipment_id: str | None = None) -> ShipmentRevenue:
    revenue = _revenue(repository, revenue_id)
    if shipment_id and revenue.shipment_id != shipment_id:
        raise NotFoundError("Revenue entry not found on this shipment")
    if not reason.strip():
        raise ConflictError("Give a reason for voiding revenue")
    shipment = _shipment(repository, revenue.shipment_id) if revenue.shipment_id else None
    if shipment:
        _require_unlocked(shipment)
    if revenue.voided:
        return revenue
    revenue.voided = True
    revenue.void_reason = reason.strip()
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.write_revenue(connection, revenue)
        entity = ("shipment", shipment.id, "revenue_voided") if shipment else ("revenue", revenue.id, "voided")
        repository.audit(connection, *entity, f"Voided revenue {money(revenue.amount_cents)}: {reason.strip()}",
                         {"revenue_id": revenue.id})
    return revenue


def add_unassigned_expense(repository, company_id: str | None, payload: ShipmentExpenseInput) -> UnassignedExpense:
    company = repository.get_company(company_id)
    expense = UnassignedExpense(company_id=company.id, **ShipmentExpenseInput(**payload.model_dump(
        include=set(ShipmentExpenseInput.model_fields))).model_dump())
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.write_unassigned_expense(connection, expense)
        repository.audit(connection, "unassigned_expense", expense.id, "recorded",
                         f"{expense.kind} {money(expense.amount_cents)} from {expense.vendor or 'unknown vendor'}",
                         expense.model_dump(mode="json", include=set(ShipmentExpenseInput.model_fields)))
    return expense


def _unassigned(repository, expense_id: str) -> UnassignedExpense:
    expense = repository.get_unassigned_expense(expense_id)
    if expense is None:
        raise NotFoundError("Expense not found (it may already be assigned to a shipment)")
    return expense


def void_unassigned_expense(repository, expense_id: str, reason: str) -> UnassignedExpense:
    expense = _unassigned(repository, expense_id)
    if not reason.strip():
        raise ConflictError("Give a reason for voiding an expense")
    if expense.voided:
        return expense
    expense.voided = True
    expense.void_reason = reason.strip()
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.write_unassigned_expense(connection, expense)
        repository.audit(connection, "unassigned_expense", expense.id, "voided",
                         f"Voided {money(expense.amount_cents)}: {reason.strip()}")
    return expense


def assign_unassigned_expense(repository, expense_id: str, shipment_id: str) -> ShipmentExpense:
    """Move a bill onto a shipment: same id and data, recorded as the shipment's expense."""

    expense = _unassigned(repository, expense_id)
    if expense.voided:
        raise ConflictError("A void expense cannot be assigned")
    shipment = _shipment(repository, shipment_id)
    if shipment.company_id != expense.company_id:
        raise ConflictError("The shipment belongs to a different company")
    _require_unlocked(shipment, "adding expenses")
    moved = ShipmentExpense(**expense.model_dump(exclude={"company_id"}), shipment_id=shipment.id)
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        deleted = connection.execute("DELETE FROM unassigned_expenses WHERE id=? AND voided=0", (expense.id,))
        if deleted.rowcount != 1:
            raise ConflictError("This expense was changed meanwhile; reload and try again")
        repository.write_expense(connection, moved)
        _clear_allocation(repository, connection, shipment.id, "the freight cost changed")
        repository.audit(connection, "shipment", shipment.id, "expense_assigned",
                         f"{moved.kind} {money(moved.amount_cents)} from {moved.vendor or 'unknown vendor'} "
                         "moved from unassigned bills",
                         {"expense_id": moved.id, **moved.model_dump(mode="json", include=set(
                             ShipmentExpenseInput.model_fields))})
        repository.audit(connection, "unassigned_expense", expense.id, "assigned", f"Assigned to {shipment.number}",
                         {"shipment_id": shipment.id})
    return moved
