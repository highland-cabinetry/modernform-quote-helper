"""Create a demo database with invented orders, quotes, shipments and money for trying the UI.

    python scripts/seed_demo.py --db tmp/demo/quote_helper_demo.sqlite3 [--force]

Everything is fictional (example.com addresses). It refuses to touch the live database
(data/quote_helper_v2.sqlite3) or any existing file unless --force is given for a demo file.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = (ROOT / "data" / "quote_helper_v2.sqlite3").resolve()


def ship_info(zip_code: str, city: str, state: str, **services) -> dict:
    accessorials = {"residential": False, "liftgate": False, "appointment_delivery": False,
                    "delivery_call_ahead": False, "limited_access": False, "inside_delivery": False, **services}
    return {"confirmed": True, "origin": {"city": "Commerce City", "state": "CO", "postal_code": "80022"},
            "destination": {"city": city, "state": state, "postal_code": zip_code},
            "accessorials": accessorials, "commodity_description": "Kitchen cabinets", "nmfc": "79300"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="replace an existing demo database")
    args = parser.parse_args(argv)
    db = args.db.resolve()
    if db == LIVE:
        print("Refusing to seed the live database.", file=sys.stderr)
        return 2
    if db.exists():
        if not args.force:
            print(f"{db} exists; use --force to replace this demo database.", file=sys.stderr)
            return 2
        for suffix in ("", "-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
    db.parent.mkdir(parents=True, exist_ok=True)
    os.environ["QUOTE_HELPER_V2_DATABASE"] = str(db)
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "tests")]

    from app import main as app_main
    from app.database import Repository
    from fastapi.testclient import TestClient

    repo = Repository(db)
    app_main.repository = repo  # the module-level repository was created before the env var on some paths
    with TestClient(app_main.app) as api:
        from fixtures_freight import catalog_item, make_order, pack

        repo.catalog_upsert("default", [catalog_item(sku, weight=w, height=h) for sku, w, h in [
            ("B30", 42, 5), ("B18", 30, 4), ("W3030", 25, 3), ("W1530", 16, 3), ("SB36", 48, 6), ("DB24", 52, 6),
            ("PANTRY", 95, 8), ("FILLER3", 4, 2)]], reason="demo seed")
        company = api.get("/api/companies").json()[0]
        config = company["config"] | {
            "pricing": {"markup_percent": 15, "markup_fixed_cents": 0, "round_up_to_cents": 500,
                        "minimum_cents": 15000},
            "reps": [{"name": "Alex Rivera", "email": "alex.rivera@example.com"},
                     {"name": "Sam Lee", "email": "sam.lee@example.com"}],
            "email": {"signature": "Freight Desk\nDemo Cabinet Co.", "cc_rep": True, "templates": {}},
        }
        api.put(f"/api/companies/{company['id']}", json={"code": company["code"], "name": company["name"],
                                                          "active": True, "config": config})

        def order(number, lines, zip_code, customer, rep, emails):
            created = make_order(api, number, *lines, postal_code=zip_code)
            detail = api.get(f"/api/orders/{created['id']}").json()["order"]
            body = {key: detail[key] for key in ("order_number", "references", "po_number", "order_date",
                                                 "ready_date", "ship_to", "packing_state", "notes", "lines")}
            body.update(customer_name=customer, sales_rep=rep, customer_contact=customer.split()[0] + " Buyer",
                        customer_emails=emails, reason="demo details")
            api.put(f"/api/orders/{created['id']}", json=body)
            return created

        def options(quote_id, rows, select=None):
            for index, (source, carrier, cents) in enumerate(rows):
                api.post(f"/api/quotes/{quote_id}/options", json={
                    "source": source, "carrier": carrier, "price_cents": cents, "transit_days": 3 + index,
                    "select": source == select})

        a = order("SO81001", [("B30", 6), ("W3030", 4), ("PANTRY", 1)], "87501", "Mesa Kitchens", "Alex Rivera",
                  ["orders@mesa.example.com"])
        b = order("SO81002", [("B18", 4), ("W1530", 6)], "80301", "Front Range Remodel", "Sam Lee",
                  ["purchasing@frontrange.example.com"])
        c = order("SO81003", [("SB36", 2), ("DB24", 3)], "80302", "Front Range Remodel", "Sam Lee",
                  ["purchasing@frontrange.example.com"])
        d = order("SO81004", [("B30", 3), ("FILLER3", 6)], "59601", "Big Sky Homes", "Alex Rivera",
                  ["jo@bigsky.example.com"])
        e = order("SO81005", [("W3030", 8)], "84101", "Wasatch Builders", "Sam Lee", ["office@wasatch.example.com"])

        # SO81001: quoted three times (residential + liftgate, then commercial, then limited access).
        _, q1 = pack(api, [a["id"]], shipping_info=ship_info("87501", "Santa Fe", "NM", residential=True,
                                                                  liftgate=True, delivery_call_ahead=True))
        options(q1["id"], [("echo", "Estes", 64800), ("tql", "Saia", 61250), ("myunishippers", "XPO", 66500)], "tql")
        api.post(f"/api/quotes/{q1['id']}/sent")
        api.post(f"/api/quotes/{q1['id']}/decline", json={"note": "Customer will pick a commercial address"})
        _, q2 = pack(api, [a["id"]], shipping_info=ship_info("87507", "Santa Fe", "NM", appointment_delivery=True))
        options(q2["id"], [("echo", "Estes", 48900), ("dsv", "Southwest Motor", 45600)], "dsv")
        api.post(f"/api/quotes/{q2['id']}/sent")
        api.post(f"/api/quotes/{q2['id']}/expire")
        _, q3 = pack(api, [a["id"]], shipping_info=ship_info("87507", "Santa Fe", "NM", limited_access=True,
                                                                  appointment_delivery=True))
        options(q3["id"], [("tql", "Saia", 52300), ("odfl", "Old Dominion", 55800), ("echo", "Estes", 53900)], "tql")
        api.post(f"/api/quotes/{q3['id']}/sent")
        approved = api.post(f"/api/quotes/{q3['id']}/approve", json={"invoice_numbers": ["68801"]}).json()
        shipment = approved["shipment"]
        view = api.get(f"/api/freight-shipments/{shipment['id']}").json()
        fields = {key: view[key] for key in ("name", "carrier", "broker", "pro_number", "bol_number", "pickup_date",
                                              "delivery_date", "destination", "customer_charge_cents", "notes",
                                              "purpose", "expected_delivery_date", "charge_source",
                                              "rep_charge_cents", "backcharge_cents")}
        api.put(f"/api/freight-shipments/{shipment['id']}", json=fields | {
            "status": "delivered", "pro_number": "PRO 4418820", "pickup_date": "2026-10-01",
            "delivery_date": "2026-10-06", "expected_delivery_date": "2026-10-05"})
        api.put(f"/api/freight-shipments/{shipment['id']}/actual", json={
            "pallets": [{"length": 48, "width": 40, "height": 52, "weight": 610, "quantity": 1},
                        {"length": 96, "width": 40, "height": 30, "weight": 420, "quantity": 1}],
            "measured_on": "2026-09-30", "source": "warehouse"})
        api.post(f"/api/freight-shipments/{shipment['id']}/expenses",
                 json={"amount_cents": 52300, "kind": "linehaul", "vendor": "TQL", "invoice_reference": "TQL-55120"})
        api.post(f"/api/freight-shipments/{shipment['id']}/expenses",
                 json={"amount_cents": 7500, "kind": "reweigh", "vendor": "TQL", "category": "Reweigh"})

        # SO81002 + SO81003: quoted together, approved, in transit.
        _, q4 = pack(api, [b["id"], c["id"]], shipping_info=ship_info("80301", "Boulder", "CO", liftgate=True,
                                                                           residential=True))
        options(q4["id"], [("echo", "Estes", 38200), ("dsv", "Pitt Ohio", 35900)], "dsv")
        api.post(f"/api/quotes/{q4['id']}/sent")
        approved = api.post(f"/api/quotes/{q4['id']}/approve",
                            json={"order_invoices": {b["id"]: ["68811"], c["id"]: ["68812"]}}).json()
        sid = approved["shipment"]["id"]
        view = api.get(f"/api/freight-shipments/{sid}").json()
        fields = {key: view[key] for key in ("name", "carrier", "broker", "pro_number", "bol_number", "pickup_date",
                                              "delivery_date", "destination", "customer_charge_cents", "notes",
                                              "purpose", "expected_delivery_date", "charge_source",
                                              "rep_charge_cents", "backcharge_cents")}
        api.put(f"/api/freight-shipments/{sid}", json=fields | {
            "status": "in_transit", "pro_number": "773300119", "pickup_date": "2026-10-07",
            "expected_delivery_date": "2026-10-08"})

        # SO81004: quoted and waiting for the customer.
        _, q5 = pack(api, [d["id"]], shipping_info=ship_info("59601", "Helena", "MT", residential=True,
                                                                  liftgate=True, inside_delivery=True))
        options(q5["id"], [("odfl", "Old Dominion", 71200), ("tql", "R+L", 69800)], "tql")
        api.post(f"/api/quotes/{q5['id']}/sent")

        # SO81005: plan built, not priced yet.
        pack(api, [e["id"]], shipping_info=ship_info("84101", "Salt Lake City", "UT"))

        api.post("/api/unassigned-expenses", json={"amount_cents": 18500, "kind": "storage", "vendor": "Mountain 3PL",
                                                   "invoice_reference": "M3-1009", "category": "Storage"})
        api.post("/api/revenue", json={"amount_cents": 12000, "kind": "claim_recovery", "payer": "Estes",
                                       "reference": "CLM-2231", "notes": "Damaged pantry panel"})
    print(f"Demo database ready: {db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
