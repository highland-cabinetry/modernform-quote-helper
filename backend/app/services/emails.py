"""Prepared customer emails (WORKFLOW §5). Quote Helper never sends mail: it prepares the text, the user
opens it in Outlook (mailto) or copies it, and the opening is logged.

Templates use ``{placeholder}`` names; unknown names are left exactly as typed. A segment wrapped in
``[[ ... ]]`` is dropped when any placeholder inside it has no value (e.g. ``[[Valid until {expires}.]]``).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import quote as url_quote

from ..freight_models import Company, EmailRecord, EmailTemplate, FreightShipment, Order, Quote
from ..freight_repository import NotFoundError
from .freight import current_combined_quote, money

EMAIL_KINDS = ("quote", "follow_up", "ready", "shipped", "delivered")
MAILTO_LIMIT = 1900
TRUNCATION_NOTE = "\n\n[Message shortened. Copy the full text from Quote Helper.]"

PLACEHOLDERS = {
    "orders": "Order numbers",
    "invoices": "Invoice numbers",
    "customer": "Customer name",
    "contact": "Customer contact (falls back to the customer name)",
    "price": "Customer freight price",
    "terms": "Delivery terms, e.g. Residential, Liftgate",
    "zip": "Delivery ZIP",
    "city": "Delivery city and state",
    "pallets": "Pallet count",
    "weight": "Total weight in lb",
    "carrier": "Carrier",
    "pro": "PRO number",
    "pickup_date": "Pickup date",
    "delivery_date": "Delivery date",
    "expected_delivery": "Expected delivery date",
    "expires": "Quote valid until",
    "rep": "Sales rep",
    "signature": "Email signature from company settings",
    "quote_number": "Quote number",
    "shipment_number": "Shipment number",
}

DEFAULT_TEMPLATES: dict[str, EmailTemplate] = {
    "quote": EmailTemplate(
        subject="Freight quote for {orders}",
        body=("Hello {contact},\n\n"
              "Freight quote for {orders}: {price} to {zip} ({terms}). {pallets} pallet(s), {weight} lb."
              "[[ Valid until {expires}.]]\n\n"
              "Reply to approve and we will schedule the shipment.\n\n{signature}"),
    ),
    "follow_up": EmailTemplate(
        subject="Following up: freight quote for {orders}",
        body=("Hello {contact},\n\n"
              "Following up on the freight quote for {orders}: {price} to {zip} ({terms})."
              "[[ It is valid until {expires}.]] Let us know if you would like to go ahead or if anything "
              "should change.\n\n{signature}"),
    ),
    "ready": EmailTemplate(
        subject="{orders} is ready to ship",
        body=("Hello {contact},\n\n"
              "{orders} is packed and ready to ship: {pallets} pallet(s), {weight} lb."
              "[[ Pickup is planned for {pickup_date}.]] We will send tracking details once it is on its way."
              "\n\n{signature}"),
    ),
    "shipped": EmailTemplate(
        subject="{orders} has shipped",
        body=("Hello {contact},\n\n"
              "{orders} has shipped[[ with {carrier}]][[ on {pickup_date}]].[[ PRO number: {pro}.]]"
              "[[ Expected delivery: {expected_delivery}.]] The carrier will contact you to arrange delivery."
              "\n\n{signature}"),
    ),
    "delivered": EmailTemplate(
        subject="{orders} was delivered",
        body=("Hello {contact},\n\n"
              "{orders} was delivered[[ on {delivery_date}]]. Please let us know right away if anything "
              "arrived damaged or is missing.\n\nThank you for your business.\n\n{signature}"),
    ),
}

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_OPTIONAL = re.compile(r"\[\[(.*?)\]\]", re.DOTALL)


def render(text: str, values: dict[str, str]) -> str:
    """Safe substitution: known names are replaced, unknown ``{names}`` stay as typed, no format() eval."""

    def optional(match: re.Match) -> str:
        inner = match.group(1)
        names = [name for name in _PLACEHOLDER.findall(inner) if name in values]
        return "" if any(not values[name] for name in names) else inner

    text = _OPTIONAL.sub(optional, text)
    text = _PLACEHOLDER.sub(lambda m: values[m.group(1)] if m.group(1) in values else m.group(0), text)
    text = re.sub(r"\(\s*\)", "", text)  # "()" when terms are empty
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)  # "Hello ," when a name is empty
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def format_date(value: date | str | None) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return f"{value:%a} {value:%b} {value.day}, {value.year}"


def _join(values) -> str:
    return ", ".join(dict.fromkeys(v for v in values if v))


def _invoices(orders: list[Order], prefix: str) -> str:
    refs = []
    for order in orders:
        for reference in order.references:
            if reference.upper() == order.order_number.upper():
                continue
            if not prefix or reference.upper().startswith(prefix.upper()):
                refs.append(reference)
    return _join(refs)


def recipients(company: Company, orders: list[Order]) -> tuple[list[str], list[str]]:
    """To: the orders' customer emails (deduplicated). CC: the sales reps' emails when ``cc_rep``."""

    to: dict[str, str] = {}
    for order in orders:
        for email in order.customer_emails:
            email = email.strip()
            if email:
                to.setdefault(email.lower(), email)
    cc: dict[str, str] = {}
    if company.config.email.cc_rep:
        reps = {rep.name.strip().lower(): rep for rep in company.config.reps if rep.active and rep.email}
        for order in orders:
            rep = reps.get(order.sales_rep.strip().lower())
            if rep and rep.email.lower() not in to:
                cc.setdefault(rep.email.lower(), rep.email)
    return list(to.values()), list(cc.values())


def mailto(to: list[str], cc: list[str], subject: str, body: str, limit: int = MAILTO_LIMIT) -> tuple[str, bool]:
    """``mailto:`` link with %20-style encoding and CRLF line breaks, capped at ``limit`` characters."""

    def build(text: str) -> str:
        query = [f"subject={url_quote(subject, safe='')}"]
        if cc:
            query.insert(0, f"cc={url_quote(','.join(cc), safe='@')}")
        query.append(f"body={url_quote(text.replace(chr(13), '').replace(chr(10), chr(13) + chr(10)), safe='')}")
        return f"mailto:{url_quote(','.join(to), safe='@,')}?{'&'.join(query)}"

    link = build(body)
    if len(link) <= limit:
        return link, False
    low, high = 0, len(body)
    while low < high:  # longest body prefix that still fits with the note
        middle = (low + high + 1) // 2
        if len(build(body[:middle].rstrip() + TRUNCATION_NOTE)) <= limit:
            low = middle
        else:
            high = middle - 1
    return build(body[:low].rstrip() + TRUNCATION_NOTE), True


def template_for(company: Company, kind: str) -> tuple[EmailTemplate, bool]:
    custom = company.config.email.templates.get(kind)
    default = DEFAULT_TEMPLATES[kind]
    if custom and (custom.subject or custom.body):
        return EmailTemplate(subject=custom.subject or default.subject, body=custom.body or default.body), True
    return default, False


def _values(company: Company, orders: list[Order], quote: Quote | None,
            shipment: FreightShipment | None) -> dict[str, str]:
    terms = quote.terms if quote else None
    customer = _join(o.customer_name for o in orders)
    contact = _join(o.customer_contact for o in orders) or customer
    if shipment is not None and shipment.actual and shipment.actual.pallets:
        pallets, weight = shipment.actual.pallet_count, shipment.actual.total_weight
    elif quote is not None and quote.summary.pallets:
        pallets, weight = quote.summary.pallets, quote.summary.total_weight
    else:
        pallets, weight = None, None
    price = quote.cost.customer_price_cents if quote else None
    if shipment is not None and shipment.customer_charge_cents is not None and (
            quote is None or quote.customer_status == "approved"):
        price = shipment.customer_charge_cents
    option = quote.selected_option if quote else None
    destination = shipment.destination if shipment else None
    zip_code = (terms.postal_code if terms and terms.postal_code else destination.postal_code if destination else "")
    city = (", ".join(p for p in (terms.city, terms.state) if p) if terms and (terms.city or terms.state)
            else ", ".join(p for p in (destination.city, destination.state) if p) if destination else "")
    expires = (option.expires_on if option else None) or (quote.cost.expires_on if quote else None)
    return {
        "orders": _join(o.order_number for o in orders),
        "invoices": _invoices(orders, company.config.bare_number_prefix),
        "customer": customer,
        "contact": contact,
        "price": money(price),
        "terms": ", ".join(terms.labels()) if terms else "",
        "zip": zip_code,
        "city": city,
        "pallets": str(pallets) if pallets else "",
        "weight": f"{weight:,.0f}" if weight else "",
        "carrier": (shipment.carrier if shipment and shipment.carrier else option.carrier if option
                    else quote.cost.carrier if quote else ""),
        "pro": shipment.pro_number if shipment else "",
        "pickup_date": format_date(shipment.pickup_date) if shipment else "",
        "delivery_date": format_date(shipment.delivery_date) if shipment else "",
        "expected_delivery": format_date(shipment.expected_delivery_date) if shipment else "",
        "expires": format_date(expires),
        "rep": _join(o.sales_rep for o in orders),
        "signature": company.config.email.signature,
        "quote_number": quote.number if quote else "",
        "shipment_number": shipment.number if shipment else "",
    }


def draft(repository, kind: str, *, quote_id: str | None = None, shipment_id: str | None = None) -> dict[str, Any]:
    if kind not in EMAIL_KINDS:
        raise ValueError(f"Unknown email kind {kind}")
    if bool(quote_id) == bool(shipment_id):
        raise ValueError("Prepare an email for either a quote or a shipment")
    quote: Quote | None = None
    shipment: FreightShipment | None = None
    if quote_id:
        quote = repository.get_quote(quote_id)
        if quote is None:
            raise NotFoundError("Quote not found")
        shipment = repository.get_freight_shipment(quote.freight_shipment_id) if quote.freight_shipment_id else None
        if shipment and shipment.combined_quote_id != quote.id:
            shipment = None
        entity_type, entity_id, company_id, order_ids = "quote", quote.id, quote.company_id, quote.order_ids
    else:
        shipment = repository.get_freight_shipment(shipment_id)
        if shipment is None:
            raise NotFoundError("Shipment not found")
        quote = current_combined_quote(repository, shipment)
        entity_type, entity_id, company_id, order_ids = "shipment", shipment.id, shipment.company_id, shipment.order_ids
    company = repository.get_company(company_id)
    orders = repository.get_orders(order_ids)
    values = _values(company, orders, quote, shipment)
    template, custom = template_for(company, kind)
    subject = " ".join(render(template.subject, values).split())
    body = render(template.body, values)
    used = set(_PLACEHOLDER.findall(template.subject + template.body))
    missing = sorted(name for name in used if name in values and not values[name] and name != "signature")
    to, cc = recipients(company, orders)
    link, truncated = mailto(to, cc, subject, body)
    warnings = []
    if not to:
        warnings.append("No customer email on the order(s); add one on the order or type it in Outlook.")
    if kind in ("quote", "follow_up") and quote is not None and quote.cost.customer_price_cents is None:
        warnings.append("The quote has no customer price yet.")
    return {
        "kind": kind, "entity_type": entity_type, "entity_id": entity_id, "company_id": company.id,
        "to": to, "cc": cc, "subject": subject, "body": body, "mailto": link, "mailto_truncated": truncated,
        "custom_template": custom, "missing": missing, "warnings": warnings,
        "quote_id": quote.id if quote else None, "quote_status": quote.customer_status if quote else None,
        "shipment_id": shipment.id if shipment else None,
    }


def log_email(repository, *, kind: str, entity_type: str, entity_id: str, to: list[str], cc: list[str],
              subject: str) -> EmailRecord:
    if entity_type == "quote":
        entity = repository.get_quote(entity_id)
    elif entity_type == "shipment":
        entity = repository.get_freight_shipment(entity_id)
    else:
        entity = repository.get_order(entity_id)
    if entity is None:
        raise NotFoundError(f"{entity_type.capitalize()} not found")
    record = EmailRecord(company_id=entity.company_id, kind=kind, entity_type=entity_type, entity_id=entity.id,
                         to=to, cc=cc, subject=subject)
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository.write_email_record(connection, record)
        repository.audit(connection, entity_type, entity.id, "email_prepared",
                         f"{kind.replace('_', ' ').capitalize()} email opened for {', '.join(to) or 'no recipient'}",
                         {"email_id": record.id, "subject": subject, "cc": cc})
    return record


def template_catalog(company: Company) -> dict[str, Any]:
    templates = {}
    for kind in EMAIL_KINDS:
        template, custom = template_for(company, kind)
        templates[kind] = {**template.model_dump(), "custom": custom, "default": DEFAULT_TEMPLATES[kind].model_dump()}
    return {"kinds": list(EMAIL_KINDS), "templates": templates, "placeholders": PLACEHOLDERS,
            "signature": company.config.email.signature, "cc_rep": company.config.email.cc_rep}
