from __future__ import annotations

import sqlite3

import pytest
from app.database import Repository
from app.models import ShippingQuoteInfo
from app.services.broker_handoff import BrokerHandoffManager, build_quote_packet, quote_values
from app.workflow_models import ManualQuoteInput, ManualQuoteShipment
from pydantic import ValidationError


def manual(**changes):
    value = dict(
        shipment_name="TEST-123",
        pallets=[dict(length=48.1, width=40, height=30.01, weight=123.5, quantity=2)],
        shipping_info=ShippingQuoteInfo(
            confirmed=True, origin={"postal_code": "80022"}, destination={"postal_code": "90210"}, nmfc="79300"
        ),
    )
    value.update(changes)
    return ManualQuoteShipment(**value)


def test_manual_precision_quantity_rounding_and_no_tare():
    value = manual()
    packet = build_quote_packet(value)
    assert len(packet["pallets"]) == 2
    assert packet["pallets"][0]["length"] == 49
    assert packet["pallets"][0]["height"] == 31
    assert packet["total_weight"] == 248
    assert value.pallets[0].weight == 123.5
    assert packet["pallets"][0]["density_pcf"] == round(124 * 1728 / (49 * 40 * 31), 4)
    assert quote_values(48, 40, 30, 123.499)["weight"] == 123


@pytest.mark.parametrize(
    "field,value", [("length", 0), ("height", -1), ("weight", float("nan")), ("quantity", 1.2), ("quantity", 0)]
)
def test_manual_invalid_measurements(field, value):
    pallet = dict(length=48, width=40, height=30, weight=100, quantity=1)
    pallet[field] = value
    with pytest.raises(ValidationError):
        manual(pallets=[pallet])


def test_manual_zip_and_reference_gates():
    manager = BrokerHandoffManager()
    value = manual()
    value.shipping_info.destination.postal_code = "9021"
    assert not manager.preview(value)["ready"]
    with pytest.raises(ValidationError):
        manual(shipment_name=" ")


def test_manual_storage_revisions_backup_and_broker_independence(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    repo.initialize()
    value = ManualQuoteInput(**manual().model_dump())
    first = repo.save_manual_quote(value)
    manager = BrokerHandoffManager()
    sessions = {}
    for broker in ("echoship", "tql", "dsv", "myunishippers"):
        response = manager.prepare(broker, first, open_browser=False)
        sessions[broker] = manager._get(response["request_id"])
        repo.save_quote_snapshot(sessions[broker])
    value.pallets[0].weight = 500
    second = repo.save_manual_quote(value, first.id)
    assert second.revision == 2
    assert manager.status(sessions["echoship"].request_id)["state"] != "cancelled"
    newer = manager.prepare("tql", second, open_browser=False, replace_active=True)
    assert manager.status(sessions["tql"].request_id)["state"] == "cancelled"
    assert not sessions["dsv"].cancelled
    assert sessions["echoship"].packet["total_weight"] == 248
    manager.cancel(newer["request_id"])
    assert not sessions["myunishippers"].cancelled
    assert repo.quote_history("manual", first.id)[0]["revision"] == "1"
    with repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM manual_quote_revisions").fetchone()[0] == 2
        backup = db.execute("SELECT backup_path FROM workflow_schema").fetchone()[0]
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert repo.item_count() == 0
    assert repo.list_shipments() == []


