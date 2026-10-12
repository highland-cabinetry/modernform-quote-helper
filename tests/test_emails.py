"""3.2 prepared emails: templates, safe rendering, recipients, mailto cap and the email log."""

from __future__ import annotations

from urllib.parse import unquote

import pytest
from app.services.emails import DEFAULT_TEMPLATES, EMAIL_KINDS, MAILTO_LIMIT, mailto, render
from fixtures_freight import pack

SHIPPING = {"destination": {"postal_code": "87501", "city": "Santa Fe", "state": "NM"},
            "accessorials": {"residential": True, "liftgate": True, "appointment_delivery": False}}


def save_company(api, **config_changes) -> dict:
    company = next(c for c in api.get("/api/companies").json() if c["is_default"])
    config = {**company["config"], **config_changes}
    response = api.put(f"/api/companies/{company['id']}", json={"code": company["code"], "name": company["name"],
                                                                 "active": True, "config": config})
    assert response.status_code == 200, response.text
    return response.json()


def order(api, number: str, *, emails: list[str], rep: str = "", contact: str = "", customer: str = "") -> dict:
    response = api.post("/api/orders", json={
        "order_number": number, "ship_to": {"postal_code": "87501"}, "customer_emails": emails, "sales_rep": rep,
        "customer_contact": contact, "customer_name": customer,
        "lines": [{"original_sku": "B30", "quantity": 1, "source_ref": f"{number}.pdf"}]})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def priced_quote(api):
    save_company(api, reps=[{"name": "Jamie Doe", "email": "jamie@highland.example"},
                            {"name": "Old Rep", "email": "old@highland.example", "active": False}],
                 email={"signature": "James\nHighland Cabinetry freight", "cc_rep": True})
    a = order(api, "SO83001", emails=["buyer@acme.example", "Buyer@Acme.example"], rep="jamie doe",
              contact="Pat", customer="Acme Kitchens")
    b = order(api, "SO83002", emails=["ap@acme.example", "jamie@highland.example"], rep="Old Rep",
              customer="Acme Kitchens")
    _, quote = pack(api, [a["id"], b["id"]], shipping_info=SHIPPING)
    api.post(f"/api/quotes/{quote['id']}/options", json={"price_cents": 123456, "carrier": "Estes", "select": True,
                                                         "expires_on": "2026-10-20"})
    api.put(f"/api/quotes/{quote['id']}/customer-price", json={"cents": 145000})
    return api.get(f"/api/quotes/{quote['id']}").json(), (a, b)


# --------------------------------------------------------------------------- rendering


def test_render_substitutes_known_names_and_keeps_unknown_ones():
    values = {"orders": "SO1", "price": "$1,234.56", "expires": "", "contact": ""}
    assert render("Quote {orders}: {price} {unknown} {Orders} {0} {}", values) == \
        "Quote SO1: $1,234.56 {unknown} {Orders} {0} {}"
    assert render("Hello {contact},\nValid[[ until {expires}]].[[ Total {price}.]]", values) == \
        "Hello,\nValid. Total $1,234.56."
    assert render("{orders.__class__} {orders!r} {{orders}}", values) == "{orders.__class__} {orders!r} {SO1}"
    assert render("Terms ({terms}).", {"terms": ""}) == "Terms."
    assert render("a\n\n\n\nb", {}) == "a\n\nb"


def test_every_kind_has_a_short_default_template():
    assert set(DEFAULT_TEMPLATES) == set(EMAIL_KINDS)
    for template in DEFAULT_TEMPLATES.values():
        assert len(template.body) < 400 and "{orders}" in template.subject + template.body


def test_mailto_encodes_and_caps_the_length():
    link, truncated = mailto(["a@x.example", "b@x.example"], ["c@x.example"], "Freight quote SO1 & SO2",
                             "Line one\nLine two: 100% ready")
    assert link.startswith("mailto:a@x.example,b@x.example?cc=c@x.example&subject=Freight%20quote%20SO1%20%26%20SO2")
    assert "&body=Line%20one%0D%0ALine%20two%3A%20100%25%20ready" in link and truncated is False
    long_link, truncated = mailto(["a@x.example"], [], "Subject", "word " * 2000)
    assert truncated is True and len(long_link) <= MAILTO_LIMIT
    assert unquote(long_link).endswith("[Message shortened. Copy the full text from Quote Helper.]")
    assert len(mailto(["a@x.example"], [], "S", "é" * 1000)[0]) <= MAILTO_LIMIT


# --------------------------------------------------------------------------- drafts


def test_quote_email_draft(api, priced_quote):
    quote, (a, b) = priced_quote
    draft = api.post("/api/emails/draft", json={"kind": "quote", "quote_id": quote["id"]})
    assert draft.status_code == 200, draft.text
    draft = draft.json()
    assert draft["to"] == ["buyer@acme.example", "ap@acme.example", "jamie@highland.example"]
    assert draft["cc"] == []  # the rep is already a recipient; the inactive rep is never copied
    assert draft["subject"] == "Freight quote for SO83001, SO83002"
    pallets, weight = quote["summary"]["pallets"], f"{quote['summary']['total_weight']:,.0f}"
    assert draft["body"] == (
        "Hello Pat,\n\n"
        f"Freight quote for SO83001, SO83002: $1,450.00 to 87501 (Residential, Liftgate). {pallets} pallet(s), "
        f"{weight} lb. Valid until Tue Oct 20, 2026.\n\n"
        "Reply to approve and we will schedule the shipment.\n\n"
        "James\nHighland Cabinetry freight")
    assert draft["mailto"].startswith("mailto:buyer@acme.example,ap@acme.example,jamie@highland.example?subject=")
    assert draft["mailto_truncated"] is False and draft["custom_template"] is False
    assert draft["missing"] == [] and draft["warnings"] == []
    assert (draft["entity_type"], draft["entity_id"], draft["quote_status"]) == ("quote", quote["id"], "not_sent")
    # Preparing the email does not mark the quote sent.
    assert api.get(f"/api/quotes/{quote['id']}").json()["customer_status"] == "not_sent"


def test_rep_is_copied_by_name_case_insensitively(api, priced_quote):
    quote, (a, _) = priced_quote
    _, single = pack(api, [a["id"]])
    draft = api.post("/api/emails/draft", json={"kind": "follow_up", "quote_id": single["id"]}).json()
    assert draft["to"] == ["buyer@acme.example"] and draft["cc"] == ["jamie@highland.example"]
    assert "cc=jamie@highland.example" in draft["mailto"]
    assert draft["subject"] == "Following up: freight quote for SO83001"
    assert "valid until" not in draft["body"] and "()" not in draft["body"]  # no price option, no terms
    assert set(draft["missing"]) >= {"price", "terms"}
    assert draft["warnings"] == ["The quote has no customer price yet."]
    save_company(api, email={"cc_rep": False})
    assert api.post("/api/emails/draft", json={"kind": "quote", "quote_id": single["id"]}).json()["cc"] == []


def test_custom_template_and_shipment_emails(api, priced_quote):
    quote, _ = priced_quote
    save_company(api, email={"signature": "-- J", "templates": {
        "shipped": {"subject": "Shipped: {orders} {pro}",
                    "body": "PRO {pro} via {carrier}. {unknown_name} {signature}"},
        "delivered": {"subject": "", "body": "Delivered {orders}"}}})
    approved = api.post(f"/api/quotes/{quote['id']}/approve", json={"invoice_numbers": ["68606"]}).json()["shipment"]
    fields = ("name", "carrier", "broker", "bol_number", "destination", "customer_charge_cents", "notes")
    api.put(f"/api/freight-shipments/{approved['id']}", json={
        **{k: approved[k] for k in fields}, "status": "in_transit", "pro_number": "PRO123",
        "pickup_date": "2026-10-12", "expected_delivery_date": "2026-10-15"})
    shipped = api.post("/api/emails/draft", json={"kind": "shipped", "shipment_id": approved["id"]}).json()
    assert shipped["subject"] == "Shipped: SO83001, SO83002 PRO123"
    assert shipped["body"] == "PRO PRO123 via Estes. {unknown_name} -- J"
    assert shipped["custom_template"] is True and shipped["entity_type"] == "shipment"
    delivered = api.post("/api/emails/draft", json={"kind": "delivered", "shipment_id": approved["id"]}).json()
    assert delivered["subject"] == "SO83001, SO83002 was delivered"  # empty subject falls back to the default
    assert delivered["body"] == "Delivered SO83001, SO83002"
    ready = api.post("/api/emails/draft", json={"kind": "ready", "shipment_id": approved["id"]}).json()
    assert "Pickup is planned for Mon Oct 12, 2026." in ready["body"]

    templates = api.get("/api/emails/templates").json()
    assert templates["templates"]["shipped"]["custom"] is True and templates["templates"]["quote"]["custom"] is False
    assert "orders" in templates["placeholders"] and templates["kinds"] == list(EMAIL_KINDS)
    # Invoices placeholder: invoice references added at approval.
    save_company(api, email={"templates": {"ready": {"body": "{invoices} | {customer} | {rep} | {shipment_number}"}}})
    ready = api.post("/api/emails/draft", json={"kind": "ready", "quote_id": quote["id"]}).json()
    assert ready["body"] == f"INV68606 | Acme Kitchens | jamie doe, Old Rep | {approved['number']}"


def test_draft_validation(api, priced_quote):
    quote, _ = priced_quote
    assert api.post("/api/emails/draft", json={"kind": "quote"}).status_code == 422
    assert api.post("/api/emails/draft", json={"kind": "quote", "quote_id": quote["id"],
                                               "shipment_id": "x"}).status_code == 422
    assert api.post("/api/emails/draft", json={"kind": "invite", "quote_id": quote["id"]}).status_code == 422
    assert api.post("/api/emails/draft", json={"kind": "quote", "quote_id": "nope"}).status_code == 404
    assert api.post("/api/emails/draft", json={"kind": "ready", "shipment_id": "nope"}).status_code == 404


def test_no_recipients_warns(api):
    bare = order(api, "SO83101", emails=[])
    _, quote = pack(api, [bare["id"]])
    draft = api.post("/api/emails/draft", json={"kind": "quote", "quote_id": quote["id"]}).json()
    assert draft["to"] == [] and draft["mailto"].startswith("mailto:?subject=")
    assert draft["warnings"][0].startswith("No customer email")
    assert draft["body"].startswith("Hello,")


# --------------------------------------------------------------------------- log


def test_email_log_records_and_lists(api, priced_quote):
    quote, _ = priced_quote
    logged = api.post("/api/emails/log", json={"kind": "quote", "entity_type": "quote", "entity_id": quote["id"],
                                               "to": ["buyer@acme.example"], "cc": [], "subject": "Freight quote"})
    assert logged.status_code == 200, logged.text
    record = logged.json()
    assert record["company_id"] == quote["company_id"] and record["entity_id"] == quote["id"]
    second = api.post("/api/emails/log", json={"kind": "follow_up", "entity_type": "quote",
                                               "entity_id": quote["number"], "subject": "Following up"}).json()
    assert second["entity_id"] == quote["id"]  # a quote number resolves to its id
    listed = api.get("/api/emails", params={"entity_type": "quote", "entity_id": quote["id"]}).json()
    assert [r["kind"] for r in listed] == ["follow_up", "quote"]
    assert api.get("/api/emails", params={"company_id": "HCI"}).json()[0]["id"] == second["id"]
    history = api.get(f"/api/quotes/{quote['id']}").json()["history"]
    assert history[-1]["action"] == "email_prepared" and history[-1]["details"]["email_id"] == second["id"]
    assert api.get(f"/api/quotes/{quote['id']}").json()["customer_status"] == "not_sent"
    assert api.post("/api/emails/log", json={"kind": "quote", "entity_type": "quote",
                                             "entity_id": "nope"}).status_code == 404
    assert api.post("/api/emails/log", json={"kind": "quote", "entity_type": "invoice",
                                             "entity_id": quote["id"]}).status_code == 422
