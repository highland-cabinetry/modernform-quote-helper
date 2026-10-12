import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.models import AddressInfo, PackingSettings, ShippingQuoteInfo
from app.packing import StackPlannerPackingEngine
from app.services.broker_handoff import BridgeEvent, BrokerHandoffManager


def ready_result(combined_factory):
    result = StackPlannerPackingEngine().pack("Quote test", [combined_factory()], PackingSettings())
    result.shipping_info = ShippingQuoteInfo(
        confirmed=True,
        origin=AddressInfo(postal_code="80021"),
        destination=AddressInfo(postal_code="80202"),
        commodity_description="Cabinets",
        nmfc="12345",
    )
    return result


def test_preview_blocks_unconfirmed_or_missing_shipping_fields(combined_factory):
    result = StackPlannerPackingEngine().pack("Quote test", [combined_factory()], PackingSettings())
    preview = BrokerHandoffManager().preview(result)
    assert not preview["ready"]
    assert "confirmed shipping information" in preview["missing_fields"]


def test_handoff_packet_uses_actual_freight_dimensions_and_expiring_token(combined_factory):
    manager = BrokerHandoffManager()
    result = ready_result(combined_factory)
    prepared = manager.prepare("tql", result, bridge_origin="http://127.0.0.1:8003", open_browser=False)
    session = manager._sessions[prepared["request_id"]]
    packet = manager.packet(session.request_id, session.token)
    row = packet["packet"]["pallets"][0]
    assert packet["packet"]["schema_version"] == 2
    assert row["length"] == result.pallets[0].load_length
    assert row["height"] == result.pallets[0].freight_height
    assert row["freight_class"] == result.pallets[0].estimated_freight_class
    assert round(row["density_pcf"], 2) == result.pallets[0].density_pcf
    assert "pickup_date" not in packet["packet"]
    encoded = prepared["url"].split("#build2quote=", 1)[1]
    launch = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert launch["broker"] == "tql"
    assert launch["expires_at"] == prepared["expires_at"]
    with pytest.raises(PermissionError):
        manager.packet(session.request_id, "incorrect-token-that-is-long-enough")


def test_autofill_only_rejects_rate_capture_and_ignores_legacy_rate_fields(combined_factory):
    manager = BrokerHandoffManager()
    result = ready_result(combined_factory)
    prepared = manager.prepare("dsv", result, open_browser=False)
    session = manager._sessions[prepared["request_id"]]
    with pytest.raises(ValueError):
        BridgeEvent(state="rates_captured", rates=[{"broker": "dsv", "price": 123.45}])
    updated = manager.event(
        session.request_id,
        session.token,
        BridgeEvent(state="filled", message="Verified", rates=[{"broker": "dsv", "price": 123.45}]),
    )
    assert not hasattr(updated, "rates")
    assert "rates" not in manager.status(session.request_id)
    assert "Never request rates" in updated.packet["guard"]


@pytest.mark.parametrize("state", ["filled", "completed", "filling"])
def test_expiry_preserves_success_but_never_reactivates_tokens(combined_factory, state):
    manager = BrokerHandoffManager()
    prepared = manager.prepare("tql", ready_result(combined_factory), open_browser=False)
    session = manager._sessions[prepared["request_id"]]
    manager.event(session.request_id, session.token, BridgeEvent(state=state))
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(KeyError):
        manager.packet(session.request_id, session.token)
    status = manager.status(session.request_id)
    assert status["state"] == (state if state in {"filled", "completed"} else "expired")
    assert status["monitoring"] is False
    with pytest.raises(KeyError):
        manager.packet(session.request_id, session.token)


def test_completed_handoff_cannot_be_overwritten_by_late_repair(combined_factory):
    manager = BrokerHandoffManager()
    prepared = manager.prepare("tql", ready_result(combined_factory), open_browser=False)
    session = manager._sessions[prepared["request_id"]]
    manager.event(session.request_id, session.token, BridgeEvent(state="completed"))
    manager.event(session.request_id, session.token, BridgeEvent(state="repairing"))
    assert manager.status(session.request_id)["state"] == "completed"


def test_discovering_is_a_supported_mirrored_bridge_state():
    event = BridgeEvent(state="discovering", message="Waiting for the TQL form")
    assert event.state == "discovering"


def test_myunishippers_is_a_supported_fill_only_target(combined_factory):
    manager = BrokerHandoffManager()
    prepared = manager.prepare("myunishippers", ready_result(combined_factory), open_browser=False)
    assert prepared["broker"] == "myunishippers"
    assert prepared["url"].startswith("https://www.myunishippers.com/ship/ltl")


def test_odfl_is_supported_and_enforces_published_limits(combined_factory):
    manager = BrokerHandoffManager()
    result = ready_result(combined_factory)
    prepared = manager.prepare("odfl", result, open_browser=False)
    assert prepared["broker"] == "odfl"
    assert prepared["url"].startswith("https://www.odfl.com/us/en/tools/freight-shipping-rate-estimate/")

    oversized = ready_result(combined_factory)
    pallet = oversized.pallets[0]
    pallet.items[0].weight += 3001 - pallet.total_weight
    pallet.item_weight = sum(item.weight for item in pallet.items)
    pallet.total_weight = 3001
    oversized.summary.packed_item_weight = pallet.item_weight
    oversized.summary.total_item_weight = pallet.item_weight
    oversized.summary.total_freight_weight = pallet.total_weight
    with pytest.raises(ValueError, match="3,000 lb"):
        BrokerHandoffManager().prepare("odfl", oversized, open_browser=False)


def test_odfl_accepts_absolute_dimensions_and_rejects_overlength(combined_factory):
    manager = BrokerHandoffManager()
    at_absolute_limit = ready_result(combined_factory)
    at_absolute_limit.pallets[0].load_length = 96
    at_absolute_limit.pallets[0].load_width = 96
    at_absolute_limit.pallets[0].freight_height = 99
    assert manager.prepare("odfl", at_absolute_limit, open_browser=False)["broker"] == "odfl"

    oversized = ready_result(combined_factory)
    oversized.pallets[0].load_length = 97
    with pytest.raises(ValueError, match="no larger than 96 x 96 x 99 in"):
        manager.prepare("odfl", oversized, open_browser=False)


def test_odfl_rejects_more_than_fourteen_pallet_rows(combined_factory):
    result = ready_result(combined_factory)
    packet = {
        "total_weight": 1500,
        "pallets": [
            {"pallet": number, "weight": 100, "length": 48, "width": 40, "height": 23}
            for number in range(1, 16)
        ],
    }
    with pytest.raises(ValueError, match="no more than 14 pallets"):
        BrokerHandoffManager().prepare(
            "odfl", result, open_browser=False, _preview={"ready": True, "packet": packet}
        )


def test_odfl_rejects_total_weight_above_twenty_three_thousand_pounds(combined_factory):
    result = ready_result(combined_factory)
    weights = [2875] * 8 + [1]
    packet = {
        "total_weight": sum(weights),
        "pallets": [
            {"pallet": number, "weight": weight, "length": 48, "width": 40, "height": 23}
            for number, weight in enumerate(weights, start=1)
        ],
    }
    with pytest.raises(ValueError, match="no more than 23,000 lb total shipment weight"):
        BrokerHandoffManager().prepare(
            "odfl", result, open_browser=False, _preview={"ready": True, "packet": packet}
        )


def test_active_broker_requires_explicit_replacement(combined_factory):
    manager = BrokerHandoffManager()
    result = ready_result(combined_factory)
    original = manager.prepare("dsv", result, open_browser=False)
    with pytest.raises(ValueError, match="Active handoff"):
        manager.prepare("dsv", result, open_browser=False)
    replacement = manager.prepare("dsv", result, open_browser=False, replace_active=True)
    assert replacement["request_id"] != original["request_id"]
    assert manager.status(original["request_id"])["state"] == "cancelled"


def test_packet_carries_new_accessorials_and_status_keeps_bridge_manual_notes(combined_factory):
    manager = BrokerHandoffManager()
    result = ready_result(combined_factory)
    result.shipping_info.accessorials.limited_access = True
    result.shipping_info.accessorials.inside_delivery = True
    prepared = manager.prepare("tql", result, open_browser=False)
    session = manager._sessions[prepared["request_id"]]
    accessorials = manager.packet(session.request_id, session.token)["packet"]["accessorials"]
    assert accessorials["limited_access"] is True
    assert accessorials["inside_delivery"] is True

    note = "Set manually on the broker page; the Bridge does not select this service here."
    fields = [
        {"field": "Limited access", "ok": False, "manual": "set", "message": note},
        {"field": "Inside delivery", "ok": False, "manual": "set", "message": note},
        {"field": "pickup postal code verification", "ok": True, "message": "Filled and verified"},
    ]
    message = (
        "Quote details filled and verified. Set manually: Limited access, Inside delivery. "
        "Request rates manually when ready."
    )
    manager.event(session.request_id, session.token, BridgeEvent(state="user_action", message=message, fields=fields))
    status = manager.status(session.request_id)
    assert status["state"] == "user_action"
    assert "Set manually: Limited access, Inside delivery." in status["message"]
    assert [field["field"] for field in status["fields"] if not field["ok"]] == ["Limited access", "Inside delivery"]
    assert all(field.get("manual") == "set" for field in status["fields"] if not field["ok"])
