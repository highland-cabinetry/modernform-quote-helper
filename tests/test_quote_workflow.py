"""3.2 quote workflow: pricing rule, price options, delivery terms, quote history, customer answer, approval."""

from __future__ import annotations

import pytest
from app.freight_models import PricingRule, QuoteOptionInput
from fixtures_freight import catalog_item, make_order, pack

SHIPPING = {"destination": {"postal_code": "87501", "city": "Santa Fe", "state": "NM"},
            "accessorials": {"residential": True, "liftgate": True, "appointment_delivery": False,
                             "delivery_call_ahead": True}}


def manual_payload(order_ids: list[str], **changes) -> dict:
    return {
        "shipment_name": "Measured",
        "pallets": [{"length": 48, "width": 40, "height": 52, "weight": 410, "quantity": 2}],
        "shipping_info": {"destination": {"postal_code": "79763"},
                          "accessorials": {"limited_access": True, "appointment_delivery": True}},
        "order_ids": order_ids,
        **changes,
    }


def set_pricing(api, **rule) -> None:
    company = next(c for c in api.get("/api/companies").json() if c["is_default"])
    config = {**company["config"], "pricing": rule}
    payload = {"code": company["code"], "name": company["name"], "active": True, "config": config}
    response = api.put(f"/api/companies/{company['id']}", json=payload)
    assert response.status_code == 200, response.text


def add_option(api, quote_id: str, price_cents: int, **extra) -> dict:
    response = api.post(f"/api/quotes/{quote_id}/options", json={"price_cents": price_cents, **extra})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def orders(api):
    api.repo.catalog_upsert("default", [catalog_item("W3030", weight=25, height=3)], reason="test weights")
    a = make_order(api, "SO81001", ("B30", 2))
    b = make_order(api, "SO81002", ("W3030", 1))
    return a, b


# --------------------------------------------------------------------------- pricing rule


@pytest.mark.parametrize(("rule", "carrier", "expected"), [
    ({}, 41250, 41250),
    ({"markup_percent": 15}, 10000, 11500),  # 10000 * 1.15 = 11499.999… in floating point: still exact
    ({"markup_percent": 15}, 33333, 38333),  # 38332.95 rounds UP to the cent
    ({"markup_fixed_cents": 2500}, 10000, 12500),
    ({"markup_percent": 10, "markup_fixed_cents": 1000}, 50000, 56000),
    ({"markup_percent": 12.5, "round_up_to_cents": 500}, 41250, 46500),  # 46406.25 → next $5
    ({"round_up_to_cents": 1000}, 50000, 50000),  # already a multiple: unchanged
    ({"round_up_to_cents": 1000}, 50001, 51000),
    ({"markup_percent": 20, "minimum_cents": 15000}, 10000, 15000),  # minimum wins
    ({"markup_percent": 20, "minimum_cents": 15000}, 20000, 24000),
    ({"markup_percent": 7}, 0, 0),
])
def test_pricing_rule_marks_up_then_rounds_up(rule, carrier, expected):
    assert PricingRule(**rule).price(carrier) == expected


def test_pricing_preview_uses_the_company_rule_or_an_unsaved_one(api):
    set_pricing(api, markup_percent=10, round_up_to_cents=100)
    preview = api.post("/api/quotes/pricing-preview", json={"carrier_cents": 41250}).json()
    assert preview == {"carrier_cents": 41250, "customer_price_cents": 45400, "markup_cents": 4150,
                       "rule": {"markup_percent": 10, "markup_fixed_cents": 0, "round_up_to_cents": 100,
                                "minimum_cents": 0}}
    trial = api.post("/api/quotes/pricing-preview", json={"carrier_cents": 41250,
                                                          "pricing": {"markup_fixed_cents": 5000}}).json()
    assert trial["customer_price_cents"] == 46250
    assert api.post("/api/quotes/pricing-preview", json={"carrier_cents": -1}).status_code == 422
    assert api.post("/api/quotes/pricing-preview", json={"carrier_cents": 1, "company_id": "nope"}).status_code == 404


# --------------------------------------------------------------------------- options


def test_option_input_normalises_the_source():
    option = QuoteOptionInput(source=" TQL ", price_cents=100)
    assert (option.source, option.source_label) == ("tql", "TQL")
    assert QuoteOptionInput(source="", price_cents=1).source == "other"
    assert QuoteOptionInput(source="Freight Center", price_cents=1).source_label == "FREIGHTCENTER"
    assert QuoteOptionInput(source="echo", source_label="Echo Global", price_cents=1).source_label == "Echo Global"


def test_options_add_select_edit_and_remove_apply_the_pricing_rule(api, orders):
    a, _ = orders
    set_pricing(api, markup_percent=20, round_up_to_cents=500)
    _, quote = pack(api, [a["id"]])
    qid = quote["id"]

    first = add_option(api, qid, 41250, source="echo", carrier="Estes", service="Standard", transit_days=3,
                       reference="E-1", expires_on="2026-10-20")
    assert first["selected_option_id"] is None and first["cost"]["customer_price_cents"] is None
    echo = first["options"][0]
    assert (echo["source"], echo["source_label"], echo["carrier"]) == ("echo", "Echo", "Estes")

    second = add_option(api, qid, 38000, source="tql", carrier="ODFL", select=True)
    tql = second["options"][1]
    assert second["selected_option_id"] == tql["id"]
    assert second["selected_option"]["id"] == tql["id"]
    assert second["cost"] == {"amount_cents": 38000, "carrier": "ODFL", "broker": "TQL", "reference": "",
                              "expires_on": None, "customer_price_cents": 46000}  # 45600 rounds up to $460

    chosen = api.post(f"/api/quotes/{qid}/select", json={"option_id": echo["id"]}).json()
    assert chosen["cost"]["amount_cents"] == 41250 and chosen["cost"]["broker"] == "Echo"
    assert chosen["cost"]["reference"] == "E-1" and chosen["cost"]["expires_on"] == "2026-10-20"
    assert chosen["cost"]["customer_price_cents"] == 49500  # 49500.0 → already a multiple of 500

    edited = api.put(f"/api/quotes/{qid}/options/{echo['id']}",
                     json={"price_cents": 42000, "source": "echo", "carrier": "Estes", "reference": "E-2"})
    assert edited.status_code == 200
    edited = edited.json()
    assert edited["cost"]["amount_cents"] == 42000 and edited["cost"]["customer_price_cents"] == 50500
    assert edited["options"][0]["id"] == echo["id"] and edited["options"][0]["recorded_at"] == echo["recorded_at"]

    # Editing an option that is not selected leaves the price alone.
    other = api.put(f"/api/quotes/{qid}/options/{tql['id']}", json={"price_cents": 1, "source": "tql"}).json()
    assert other["cost"]["customer_price_cents"] == 50500

    removed = api.delete(f"/api/quotes/{qid}/options/{echo['id']}").json()
    assert removed["selected_option_id"] is None and [o["id"] for o in removed["options"]] == [tql["id"]]
    assert removed["cost"] == {"amount_cents": None, "carrier": "", "broker": "", "reference": "",
                               "expires_on": None, "customer_price_cents": None}

    cleared = api.post(f"/api/quotes/{qid}/select", json={"option_id": tql["id"]}).json()
    assert cleared["cost"]["customer_price_cents"] == 500  # 1 cent * 1.2 rounds up to $5
    cleared = api.post(f"/api/quotes/{qid}/select", json={"option_id": None}).json()
    assert cleared["cost"]["amount_cents"] is None and cleared["selected_option_id"] is None

    actions = [e["action"] for e in api.get(f"/api/quotes/{qid}").json()["history"]]
    assert actions == ["created", "option_added", "option_added", "option_selected", "option_updated",
                       "option_updated", "option_removed", "option_selected", "option_selected"]

    assert api.post(f"/api/quotes/{qid}/select", json={"option_id": "missing"}).status_code == 404
    assert api.put(f"/api/quotes/{qid}/options/missing", json={"price_cents": 1}).status_code == 404
    assert api.delete(f"/api/quotes/{qid}/options/missing").status_code == 404
    assert api.post(f"/api/quotes/{qid}/options", json={"price_cents": -5}).status_code == 422
    assert api.post(f"/api/quotes/{qid}/options", json={"price_cents": 5, "bogus": 1}).status_code == 422
    assert api.post("/api/quotes/nope/options", json={"price_cents": 5}).status_code == 404


def test_changing_the_pricing_rule_does_not_rewrite_existing_quotes(api, orders):
    a, _ = orders
    set_pricing(api, markup_percent=10)
    _, quote = pack(api, [a["id"]])
    priced = add_option(api, quote["id"], 10000, select=True)
    assert priced["cost"]["customer_price_cents"] == 11000
    set_pricing(api, markup_percent=50)
    assert api.get(f"/api/quotes/{quote['id']}").json()["cost"]["customer_price_cents"] == 11000
    # Selecting again applies the current rule.
    again = api.post(f"/api/quotes/{quote['id']}/select", json={"option_id": priced["options"][0]["id"]}).json()
    assert again["cost"]["customer_price_cents"] == 11000  # same option: nothing changes
    second = add_option(api, quote["id"], 10000, select=True)
    assert second["cost"]["customer_price_cents"] == 15000


def test_manual_customer_price_survives_selection_until_reset(api, orders):
    a, _ = orders
    set_pricing(api, markup_percent=10)
    _, quote = pack(api, [a["id"]])
    qid = quote["id"]
    manual = api.put(f"/api/quotes/{qid}/customer-price", json={"cents": 60000}).json()
    assert manual["price_manual"] is True and manual["cost"]["customer_price_cents"] == 60000
    selected = add_option(api, qid, 40000, select=True)
    assert selected["cost"]["customer_price_cents"] == 60000 and selected["cost"]["amount_cents"] == 40000
    unselected = api.post(f"/api/quotes/{qid}/select", json={"option_id": None}).json()
    assert unselected["cost"]["customer_price_cents"] == 60000  # a typed price is kept
    api.post(f"/api/quotes/{qid}/select", json={"option_id": selected["options"][0]["id"]})
    reset = api.put(f"/api/quotes/{qid}/customer-price", json={"cents": None}).json()
    assert reset["price_manual"] is False and reset["cost"]["customer_price_cents"] == 44000
    api.post(f"/api/quotes/{qid}/select", json={"option_id": None})
    assert api.put(f"/api/quotes/{qid}/customer-price", json={}).json()["cost"]["customer_price_cents"] is None
    assert api.put(f"/api/quotes/{qid}/customer-price", json={"cents": -1}).status_code == 422
    history = api.get(f"/api/quotes/{qid}").json()["history"]
    assert [e["action"] for e in history].count("price_set") == 3
    assert history[1]["details"]["changes"] == {"customer_price_cents": [None, 60000]}


def test_void_quotes_cannot_be_priced_or_answered(api, orders):
    a, _ = orders
    _, quote = pack(api, [a["id"]])
    api.put(f"/api/quotes/{quote['id']}", json={"status": "void"})
    for method, path, body in [
        ("post", "options", {"price_cents": 100}), ("put", "customer-price", {"cents": 5}),
        ("post", "select", {"option_id": None}), ("put", "terms", {"postal_code": "1"}),
        ("post", "sent", {}), ("post", "approve", {}), ("post", "decline", {}),
    ]:
        response = getattr(api, method)(f"/api/quotes/{quote['id']}/{path}", json=body)
        assert response.status_code == 409, (path, response.text)


# --------------------------------------------------------------------------- delivery terms


def test_terms_are_captured_from_the_pallet_plan_and_follow_its_shipping_info(api, orders):
    a, _ = orders
    plan, quote = pack(api, [a["id"]], shipping_info=SHIPPING)
    detail = api.get(f"/api/quotes/{quote['id']}/detail").json()
    assert detail["terms"] == {"postal_code": "87501", "city": "Santa Fe", "state": "NM",
                               "location_type": "residential", "liftgate": True, "appointment": False,
                               "call_ahead": True, "inside_delivery": False, "notes": ""}
    assert detail["terms_labels"] == ["Residential", "Liftgate", "Call ahead"]
    assert detail["terms_source"] == "recorded"

    # Notes typed on the quote survive a change of the plan's shipping info.
    api.put(f"/api/quotes/{quote['id']}/terms", json={**detail["terms"], "notes": "gate code 1234"})
    changed = {**SHIPPING, "accessorials": {"liftgate": False, "inside_delivery": True}}
    assert api.put(f"/api/shipments/{plan['id']}/shipping-info", json=changed).status_code == 200
    detail = api.get(f"/api/quotes/{quote['id']}/detail").json()
    assert detail["terms_labels"] == ["Commercial", "Appointment", "Inside delivery"]  # appointment is the default
    assert detail["terms"]["notes"] == "gate code 1234"
    actions = [e["action"] for e in api.get(f"/api/quotes/{quote['id']}").json()["history"]]
    assert actions == ["created", "terms_updated", "terms_updated"]
    # Saving the same shipping info again records nothing new.
    api.put(f"/api/shipments/{plan['id']}/shipping-info", json=changed)
    assert len(api.get(f"/api/quotes/{quote['id']}").json()["history"]) == 3


def test_terms_from_a_measured_quote_and_manual_edits(api, orders):
    a, _ = orders
    manual = api.post("/api/manual-quotes", json=manual_payload([a["id"]])).json()
    quote = manual["freight_quote"]
    assert quote["terms"]["location_type"] == "limited_access" and quote["terms"]["appointment"] is True
    assert quote["terms"]["postal_code"] == "79763"

    edited = api.put(f"/api/quotes/{quote['id']}/terms", json={
        "postal_code": "79763", "location_type": "commercial", "liftgate": True, "notes": "dock closes 3pm"})
    assert edited.status_code == 200
    assert edited.json()["terms_labels"] == ["Commercial", "Liftgate"]
    history = api.get(f"/api/quotes/{quote['id']}").json()["history"]
    assert history[-1]["action"] == "terms_updated"
    assert history[-1]["details"]["changes"]["location_type"] == ["limited_access", "commercial"]
    # Saving the same terms again records nothing.
    api.put(f"/api/quotes/{quote['id']}/terms", json=edited.json()["terms"])
    assert len(api.get(f"/api/quotes/{quote['id']}").json()["history"]) == len(history)
    assert api.put(f"/api/quotes/{quote['id']}/terms", json={"location_type": "farm"}).status_code == 422
    assert api.put(f"/api/quotes/{quote['id']}/terms", json={"zip": "1"}).status_code == 422

    # Re-saving the measured quote captures the shipping info it was saved with.
    revised = api.put(f"/api/manual-quotes/{manual['id']}", json=manual_payload([a["id"]])).json()["freight_quote"]
    assert revised["terms"]["location_type"] == "limited_access"
    assert revised["terms"]["notes"] == "dock closes 3pm"


def test_quotes_recorded_before_terms_show_terms_from_their_source(api, orders):
    a, _ = orders
    _, quote = pack(api, [a["id"]], shipping_info=SHIPPING)
    stored = api.repo.get_quote(quote["id"])
    stored.terms = None  # as recorded by 3.0/3.1
    api.repo.save_quote(stored, action="imported")
    detail = api.get(f"/api/quotes/{quote['id']}/detail").json()
    assert detail["terms_source"] == "source" and detail["terms_labels"] == ["Residential", "Liftgate", "Call ahead"]
    no_info = pack(api, [a["id"]])[1]
    assert api.get(f"/api/quotes/{no_info['id']}/detail").json()["terms"] is None


# --------------------------------------------------------------------------- quote history


def test_order_quote_history_counts_priced_or_sent_quotes(api, orders):
    a, b = orders
    _, draft = pack(api, [a["id"]], shipping_info=SHIPPING)  # listed, not counted
    _, priced = pack(api, [a["id"], b["id"]])
    add_option(api, priced["id"], 30000, source="dsv", carrier="Saia", select=True)
    manual = api.post("/api/manual-quotes", json=manual_payload([a["id"]])).json()["freight_quote"]
    api.post(f"/api/quotes/{manual['id']}/sent", json={})
    _, voided = pack(api, [a["id"]])
    api.put(f"/api/quotes/{voided['id']}", json={"cost": {"customer_price_cents": 1}})
    assert api.put(f"/api/quotes/{voided['id']}", json={"status": "void"}).status_code == 200

    history = api.get(f"/api/orders/{a['id']}/quote-history").json()
    assert history["order"]["order_number"] == "SO81001"
    assert [q["number"] for q in history["quotes"]] == [manual["number"], priced["number"], draft["number"]]
    assert history["quoted_count"] == 2
    assert [q["counted"] for q in history["quotes"]] == [True, True, False]
    assert [q["time_quoted"] for q in history["quotes"]] == [2, 1, None]
    assert history["latest_terms"]["location_type"] == "limited_access"
    assert history["latest_terms_labels"] == ["Limited access", "Appointment"]
    assert history["approved_quote_id"] is None
    entry = history["quotes"][1]
    assert entry["selected_option"]["carrier"] == "Saia" and entry["cost"]["customer_price_cents"] == 30000
    assert [o["order_number"] for o in entry["orders"]] == ["SO81001", "SO81002"]
    assert entry["summary"]["pallets"] >= 1 and entry["summary"]["rows"]
    assert entry["shipment"] is None and entry["customer_status"] == "not_sent"
    assert history["quotes"][2]["terms_labels"] == ["Residential", "Liftgate", "Call ahead"]

    other = api.get(f"/api/orders/{b['id']}/quote-history").json()
    assert other["quoted_count"] == 1 and other["latest_terms"] is None
    assert api.get("/api/orders/nope/quote-history").status_code == 404


def test_order_list_shows_times_quoted_and_the_latest_answer(api, orders):
    a, b = orders
    c = make_order(api, "SO81009", ("B30", 1))
    pack(api, [a["id"]])  # a draft without a price: listed, not counted
    _, priced = pack(api, [a["id"], b["id"]])
    add_option(api, priced["id"], 30000, source="tql", carrier="Saia", select=True)
    api.post(f"/api/quotes/{priced['id']}/decline", json={"note": "too high"})
    _, resent = pack(api, [a["id"]])
    api.post(f"/api/quotes/{resent['id']}/sent")
    _, voided = pack(api, [a["id"]])
    api.post(f"/api/quotes/{voided['id']}/sent")
    assert api.put(f"/api/quotes/{voided['id']}", json={"status": "void"}).status_code == 200

    rows = {row["order_number"]: row for row in api.get("/api/orders").json()}
    assert rows["SO81001"]["quoted_count"] == 2
    assert rows["SO81001"]["latest_customer_status"] == "sent"  # the newest counted quote, void ignored
    assert len(rows["SO81001"]["quote_ids"]) == 3
    assert (rows["SO81002"]["quoted_count"], rows["SO81002"]["latest_customer_status"]) == (1, "declined")
    assert (rows["SO81009"]["quoted_count"], rows["SO81009"]["latest_customer_status"]) == (0, None)
    assert rows["SO81009"]["quote_ids"] == [] and c["id"] == rows["SO81009"]["id"]
    history = api.get(f"/api/orders/{a['id']}/quote-history").json()
    assert history["quoted_count"] == rows["SO81001"]["quoted_count"]


# --------------------------------------------------------------------------- customer answer


def test_sent_decline_expire_and_reopen(api, orders):
    a, _ = orders
    _, quote = pack(api, [a["id"]])
    qid = quote["id"]
    assert api.post(f"/api/quotes/{qid}/reopen", json={}).status_code == 409  # still open
    sent = api.post(f"/api/quotes/{qid}/sent").json()
    assert sent["customer_status"] == "sent" and sent["sent_at"] and sent["counted"] is True
    again = api.post(f"/api/quotes/{qid}/sent", json={"note": "resent with liftgate"}).json()
    assert again["sent_at"] >= sent["sent_at"]
    declined = api.post(f"/api/quotes/{qid}/decline", json={"note": "too expensive"}).json()
    assert declined["customer_status"] == "declined" and declined["response_note"] == "too expensive"
    assert declined["responded_at"]
    assert api.post(f"/api/quotes/{qid}/sent").status_code == 409
    assert api.post(f"/api/quotes/{qid}/expire").status_code == 409
    reopened = api.post(f"/api/quotes/{qid}/reopen", json={"note": "customer called back"}).json()
    assert reopened["customer_status"] == "sent" and reopened["responded_at"] is None
    assert reopened["sent_at"] == again["sent_at"]
    expired = api.post(f"/api/quotes/{qid}/expire").json()
    assert expired["customer_status"] == "expired"
    assert api.post(f"/api/quotes/{qid}/reopen").json()["customer_status"] == "sent"
    actions = [e["action"] for e in api.get(f"/api/quotes/{qid}").json()["history"]]
    assert actions == ["created", "sent", "sent", "declined", "reopened", "expired", "reopened"]
    assert api.post("/api/quotes/nope/sent").status_code == 404


def test_approve_needs_a_price_and_orders(api, orders):
    a, _ = orders
    _, quote = pack(api, [a["id"]])
    refused = api.post(f"/api/quotes/{quote['id']}/approve", json={})
    assert refused.status_code == 422 and "price" in refused.json()["detail"]
    stored = api.repo.get_quote(quote["id"])
    stored.order_ids = []
    stored.cost.customer_price_cents = 100
    api.repo.save_quote(stored, action="updated")
    assert api.post(f"/api/quotes/{quote['id']}/approve", json={}).status_code == 422
    assert api.post(f"/api/quotes/{quote['id']}/approve", json={"invoice_numbers": ["--"]}).status_code == 422


def test_approve_creates_the_shipment_with_invoices_and_the_quoted_price(api, orders):
    a, b = orders
    set_pricing(api, markup_percent=25)
    _, quote = pack(api, [a["id"], b["id"]], shipping_info=SHIPPING)
    add_option(api, quote["id"], 40000, source="myunishippers", carrier="XPO", select=True)
    api.post(f"/api/quotes/{quote['id']}/sent")

    response = api.post(f"/api/quotes/{quote['id']}/approve",
                        json={"invoice_numbers": ["68606"], "order_invoices": {b["id"]: ["inv 68607"]},
                              "note": "PO 4471"})
    assert response.status_code == 200, response.text
    body = response.json()
    approved, shipment = body["quote"], body["shipment"]
    assert body["shipment_created"] is True
    assert approved["customer_status"] == "approved" and approved["responded_at"]
    assert approved["response_note"] == "PO 4471" and approved["role"] == "combined"
    assert approved["shipment"] == {"id": shipment["id"], "number": shipment["number"], "status": "planning",
                                    "is_combined_quote": True}
    assert shipment["status"] == "planning" and shipment["order_ids"] == [a["id"], b["id"]]
    assert shipment["combined_quote"]["id"] == quote["id"]
    assert shipment["customer_charge_cents"] == 50000 and shipment["charge_source"] == "quote"
    assert (shipment["carrier"], shipment["broker"]) == ("XPO", "MyUniShippers")
    assert shipment["destination"]["postal_code"] == "87501"
    assert shipment["comparison"]["cost"]["expected_cents"] == 40000
    assert shipment["comparison"]["profit_cents"] == 10000 and shipment["comparison"]["profit_estimated"] is True

    refs = {o["order_number"]: o["references"] for o in api.get("/api/orders").json()}
    assert refs["SO81001"] == ["INV68606"]
    assert refs["SO81002"] == ["INV68606", "INV68607"]
    found = api.get("/api/orders", params={"search": "68607"}).json()
    assert [o["order_number"] for o in found] == ["SO81002"]
    order_history = api.get(f"/api/orders/{a['id']}").json()["history"]
    assert order_history[-1]["action"] == "references_added"

    history = api.get(f"/api/orders/{a['id']}/quote-history").json()
    assert history["approved_quote_id"] == quote["id"]
    assert history["quotes"][0]["shipment"]["number"] == shipment["number"]

    # Approving again (an invoice that arrived later) keeps the shipment and adds the reference.
    later = api.post(f"/api/quotes/{quote['id']}/approve", json={"order_invoices": {a["id"]: ["68610"]}}).json()
    assert later["shipment_created"] is False and later["shipment"]["id"] == shipment["id"]
    assert later["quote"]["responded_at"] == approved["responded_at"]
    assert api.get(f"/api/orders/{a['id']}").json()["order"]["references"] == ["INV68606", "INV68610"]
    assert len(api.get("/api/freight-shipments").json()) == 1
    # An approved quote can no longer expire, and cannot be declined while its shipment is active.
    assert api.post(f"/api/quotes/{quote['id']}/expire").status_code == 409
    assert api.post(f"/api/quotes/{quote['id']}/decline").status_code == 409
    assert api.post(f"/api/quotes/{quote['id']}/reopen").status_code == 409
    assert api.post(f"/api/quotes/{quote['id']}/sent").status_code == 409


def test_approve_refuses_an_invoice_owned_by_another_order(api, orders):
    a, b = orders
    other = make_order(api, "INV70001", ("B18", 1))
    _, quote = pack(api, [a["id"]])
    api.put(f"/api/quotes/{quote['id']}/customer-price", json={"cents": 25000})
    clash = api.post(f"/api/quotes/{quote['id']}/approve", json={"invoice_numbers": ["70001"]})
    assert clash.status_code == 409 and "INV70001" in clash.json()["detail"] and "INV70001" in other["order_number"]
    # Nothing was written: no reference, no shipment, quote still open.
    assert api.get(f"/api/orders/{a['id']}").json()["order"]["references"] == []
    assert api.get("/api/freight-shipments").json() == []
    assert api.get(f"/api/quotes/{quote['id']}").json()["customer_status"] == "not_sent"
    assert api.post(f"/api/quotes/{quote['id']}/approve",
                    json={"order_invoices": {b["id"]: ["123"]}}).status_code == 422  # not the quote's order


def test_approve_links_the_existing_shipment_and_replaces_its_quote(api, orders):
    a, b = orders
    _, first = pack(api, [a["id"], b["id"]])
    shipment = api.post("/api/freight-shipments", json={"order_ids": [a["id"], b["id"]], "carrier": "Estes"}).json()
    assert shipment["combined_quote"]["id"] == first["id"]
    _, second = pack(api, [a["id"], b["id"]])
    add_option(api, second["id"], 30000, carrier="Saia", source="dsv", select=True)
    api.put(f"/api/quotes/{second['id']}/customer-price", json={"cents": 39900})
    body = api.post(f"/api/quotes/{second['id']}/approve", json={}).json()
    assert body["shipment_created"] is False and body["shipment"]["id"] == shipment["id"]
    view = body["shipment"]
    assert view["combined_quote"]["id"] == second["id"]
    assert view["customer_charge_cents"] == 39900
    assert view["carrier"] == "Estes" and view["broker"] == "DSV"  # a carrier already set is kept
    assert view["history"][-1]["action"] == "combined_quote"

    # Later corrections of the approved price follow to the shipment while its charge comes from the quote.
    api.put(f"/api/quotes/{second['id']}/customer-price", json={"cents": 41000})
    assert api.get(f"/api/freight-shipments/{shipment['id']}").json()["customer_charge_cents"] == 41000
    actions = [e["action"] for e in api.get(f"/api/freight-shipments/{shipment['id']}").json()["history"]]
    assert actions[-1] == "charge_updated"


def test_approve_refuses_a_locked_or_split_shipment(api, orders):
    a, b = orders
    c = make_order(api, "SO81003", ("B18", 1))
    _, quote = pack(api, [a["id"], b["id"]])
    api.put(f"/api/quotes/{quote['id']}/customer-price", json={"cents": 10000})
    only_a = api.post("/api/freight-shipments", json={"order_ids": [a["id"]]}).json()
    split = api.post(f"/api/quotes/{quote['id']}/approve", json={})
    assert split.status_code == 409 and "SO81002 on none" in split.json()["detail"]
    api.post("/api/freight-shipments", json={"order_ids": [b["id"]]})
    assert api.post(f"/api/quotes/{quote['id']}/approve", json={}).status_code == 409

    _, quote_c = pack(api, [c["id"]])
    api.put(f"/api/quotes/{quote_c['id']}/customer-price", json={"cents": 5000})
    closed = api.post("/api/freight-shipments", json={"order_ids": [c["id"]]}).json()
    payload = {k: closed[k] for k in ("name", "carrier", "broker", "pro_number", "bol_number", "destination",
                                      "customer_charge_cents", "notes")}
    assert api.put(f"/api/freight-shipments/{closed['id']}", json={**payload, "status": "closed"}).status_code == 200
    locked = api.post(f"/api/quotes/{quote_c['id']}/approve", json={"invoice_numbers": ["99999"]})
    assert locked.status_code == 409 and "Reopen" in locked.json()["detail"]
    assert api.get(f"/api/orders/{c['id']}").json()["order"]["references"] == []
    assert api.get(f"/api/quotes/{quote_c['id']}").json()["customer_status"] == "not_sent"
    assert only_a["status"] == "planning"


def test_declined_after_cancelling_the_shipment_and_reapproval_creates_a_new_one(api, orders):
    a, _ = orders
    _, quote = pack(api, [a["id"]])
    api.put(f"/api/quotes/{quote['id']}/customer-price", json={"cents": 20000})
    first = api.post(f"/api/quotes/{quote['id']}/approve", json={}).json()["shipment"]
    payload = {k: first[k] for k in ("name", "carrier", "broker", "pro_number", "bol_number", "destination",
                                     "customer_charge_cents", "notes")}
    api.put(f"/api/freight-shipments/{first['id']}", json={**payload, "status": "cancelled"})
    declined = api.post(f"/api/quotes/{quote['id']}/decline", json={"note": "order cancelled"}).json()
    assert declined["customer_status"] == "declined"
    again = api.post(f"/api/quotes/{quote['id']}/approve", json={}).json()
    assert again["shipment_created"] is True and again["shipment"]["id"] != first["id"]
    assert again["quote"]["customer_status"] == "approved"


def test_approve_of_a_cancelled_order_is_refused(api, orders):
    a, _ = orders
    _, quote = pack(api, [a["id"]])
    api.put(f"/api/quotes/{quote['id']}/customer-price", json={"cents": 100})
    api.post(f"/api/orders/{a['id']}/status", json={"status": "cancelled", "reason": "customer"})
    refused = api.post(f"/api/quotes/{quote['id']}/approve", json={})
    assert refused.status_code == 409 and "cancelled" in refused.json()["detail"]


def test_approved_quote_keeps_the_terms_the_customer_accepted(api, orders):
    a, _ = orders
    plan, quote = pack(api, [a["id"]], shipping_info=SHIPPING)
    api.put(f"/api/quotes/{quote['id']}/customer-price", json={"cents": 30000})
    api.post(f"/api/quotes/{quote['id']}/approve", json={})
    api.put(f"/api/shipments/{plan['id']}/shipping-info", json={"destination": {"postal_code": "10001"}})
    detail = api.get(f"/api/quotes/{quote['id']}/detail").json()
    assert detail["terms"]["postal_code"] == "87501" and detail["terms_labels"][0] == "Residential"
    # A correction by hand is still possible and audited.
    corrected = api.put(f"/api/quotes/{quote['id']}/terms", json={**detail["terms"], "liftgate": False}).json()
    assert corrected["terms_labels"] == ["Residential", "Call ahead"]
    history = api.get(f"/api/quotes/{quote['id']}").json()["history"]
    assert history[-1]["action"] == "terms_updated" and "already approved" in history[-1]["summary"]
