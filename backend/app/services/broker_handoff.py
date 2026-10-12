from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from ..models import PackingResult, ShippingQuoteInfo
from ..workflow_models import ManualQuoteShipment
from .freight_class import density_freight_class

BrokerName = Literal["echoship", "tql", "dsv", "myunishippers", "odfl"]
BROKER_URLS: dict[BrokerName, str] = {
    "echoship": "https://echoship.com/new-quote/ltl",
    "tql": "https://trax.tqlapp.com/quote-details",
    "dsv": "https://mytms.us.dsv.com/dsvweb/dashboard/quote",
    "myunishippers": "https://www.myunishippers.com/ship/ltl",
    "odfl": "https://www.odfl.com/us/en/tools/freight-shipping-rate-estimate/ltl-rate-estimate.html",
}
HANDOFF_LIFETIME = timedelta(minutes=15)


class ActiveHandoffConflict(ValueError):
    def __init__(self, brokers: list[BrokerName]) -> None:
        self.brokers = brokers
        super().__init__(f"Active handoff already exists for: {', '.join(brokers)}")


class QuoteHandoffRequest(BaseModel):
    shipment_id: str
    source_kind: Literal["packed", "manual"] = "packed"
    replace_active: bool = False


class BridgeEvent(BaseModel):
    state: Literal[
        "page_seen",
        "login_required",
        "discovering",
        "filling",
        "repairing",
        "filled",
        "user_action",
        "completed",
        "error",
    ]
    message: str = ""
    fields: list[dict] = Field(default_factory=list)


@dataclass
class HandoffSession:
    request_id: str
    token: str
    broker: BrokerName
    shipment_id: str
    packet: dict
    created_at: datetime
    expires_at: datetime
    source_kind: str = "packed"
    source_revision: str = ""
    state: str = "ready"
    message: str = "Quote packet is ready."
    fields: list[dict] = field(default_factory=list)
    cancelled: bool = False


def _required_missing(info: ShippingQuoteInfo | None, result: PackingResult | ManualQuoteShipment) -> list[str]:
    missing: list[str] = []
    if isinstance(result, PackingResult) and (not result.freight_ready):
        missing.append("a freight-ready pallet plan with no unpacked items")
    if isinstance(result, PackingResult) and (result.purpose == "prototype" or "prototype" in result.engine):
        missing.append("production plan (Packing Lab results cannot be quoted)")
    if not info or not info.confirmed:
        missing.append("confirmed shipping information")
    if not info or not info.origin.postal_code.strip():
        missing.append("origin postal code")
    if not info or not info.destination.postal_code.strip():
        missing.append("destination postal code")
    if not result.pallets:
        missing.append("at least one packed pallet")
    for label, address in (
        ("origin", info.origin if info else None),
        ("destination", info.destination if info else None),
    ):
        if (
            address
            and address.postal_code.strip()
            and not re.fullmatch(r"\d{5}(?:-\d{4})?", address.postal_code.strip())
        ):
            missing.append(f"valid {label} US ZIP code")
    if not result.shipment_name.strip():
        missing.append("shipment reference")
    weights = [p.weight if isinstance(result, ManualQuoteShipment) else p.total_weight for p in result.pallets]
    if any(not math.isfinite(w) or math.floor(w + 0.5) < 1 for w in weights):
        missing.append("at least one whole pound per pallet after rounding")
    return missing


def source_revision(result: PackingResult | ManualQuoteShipment) -> str:
    if isinstance(result, ManualQuoteShipment):
        return str(result.revision)
    value = result.model_dump(mode="json", exclude={"broker_rates"})
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def quote_values(length: float, width: float, height: float, weight: float) -> dict:
    length, width, height = math.ceil(length), math.ceil(width), math.ceil(height)
    weight = math.floor(weight + 0.5)
    density = weight * 1728 / (length * width * height)
    return dict(
        length=length,
        width=width,
        height=height,
        weight=weight,
        density_pcf=round(density, 4),
        freight_class=density_freight_class(density),
        dimensions=f"{length} x {width} x {height} in",
    )


def build_quote_packet(result: PackingResult | ManualQuoteShipment) -> dict:
    info = result.shipping_info
    if info is None:
        raise ValueError("Shipment has no confirmed shipping information")
    manual = isinstance(result, ManualQuoteShipment)
    pallets = []
    canonical = iter(quote_rows(result))
    for pallet in result.pallets:
        for _ in range(pallet.quantity if manual else 1):
            pallets.append(
                {
                    **next(canonical),
                    "type": "Pallet" if manual else pallet.display_name,
                    "commodity_description": info.commodity_description,
                    "nmfc": info.nmfc,
                    "stackable": False,
                    "warehouse_review_required": manual or bool(pallet.warnings),
                }
            )
    return {
        "schema_version": 2,
        "shipment_id": result.id,
        "shipment_name": result.shipment_name,
        "source_kind": "manual" if manual else "packed",
        "source_revision": source_revision(result),
        "customer_references": info.customer_references,
        "origin": info.origin.model_dump(mode="json"),
        "destination": info.destination.model_dump(mode="json"),
        "accessorials": info.accessorials.model_dump(mode="json"),
        "commodity_description": info.commodity_description,
        "nmfc": info.nmfc,
        "total_weight": sum(p["weight"] for p in pallets),
        "pallets": pallets,
        "guard": "Fill and verify only. Never request rates, book, tender, or schedule automatically.",
    }


def _validate_broker_packet(broker: BrokerName, packet: dict) -> None:
    if broker != "odfl":
        return
    pallets = packet["pallets"]
    problems: list[str] = []
    if len(pallets) > 14:
        problems.append("no more than 14 pallets")
    if packet["total_weight"] > 23_000:
        problems.append("no more than 23,000 lb total shipment weight")
    for row in pallets:
        number = row["pallet"]
        if row["weight"] > 3_000:
            problems.append(f"pallet {number} no more than 3,000 lb")
        if row["length"] > 96 or row["width"] > 96 or row["height"] > 99:
            problems.append(f"pallet {number} no larger than 96 x 96 x 99 in")
    if problems:
        raise ValueError("Old Dominion cannot quote this shipment: " + "; ".join(problems))


def quote_rows(result: PackingResult | ManualQuoteShipment) -> list[dict]:
    """Broker measurements without requiring address confirmation."""
    rows = []
    manual = isinstance(result, ManualQuoteShipment)
    for pallet in result.pallets:
        values = quote_values(
            pallet.length if manual else pallet.load_length or pallet.length,
            pallet.width if manual else pallet.load_width or pallet.width,
            pallet.height if manual else pallet.freight_height,
            pallet.weight if manual else pallet.total_weight,
        )
        for _ in range(pallet.quantity if manual else 1):
            rows.append({"pallet": len(rows) + 1, **values})
    return rows


class BrokerHandoffManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, HandoffSession] = {}

    def preview(self, result: PackingResult | ManualQuoteShipment) -> dict:
        missing = _required_missing(result.shipping_info, result)
        return {
            "ready": not missing,
            "quote_rows": quote_rows(result),
            "missing_fields": missing,
            "packet": build_quote_packet(result) if not missing else None,
            "extension_name": "Build2Quote Bridge",
        }

    def prepare(
        self,
        broker: BrokerName,
        result: PackingResult | ManualQuoteShipment,
        *,
        bridge_origin: str = "http://127.0.0.1:8000",
        open_browser: bool = False,
        replace_active: bool = False,
        _preview: dict | None = None,
    ) -> dict:
        preview = _preview if _preview is not None else self.preview(result)
        if not preview["ready"]:
            raise ValueError("Complete these quote fields first: " + ", ".join(preview["missing_fields"]))
        _validate_broker_packet(broker, preview["packet"])
        now = datetime.now(UTC)
        request_id = secrets.token_urlsafe(18)
        token = secrets.token_urlsafe(32)
        session = HandoffSession(
            request_id=request_id,
            token=token,
            broker=broker,
            shipment_id=result.id,
            packet=preview["packet"],
            created_at=now,
            # Account selection and identity-provider returns can take several
            # minutes on carrier sites. Keep the immutable, memory-only packet
            # available long enough for that manual prerequisite to finish.
            expires_at=now + HANDOFF_LIFETIME,
            source_kind="manual" if isinstance(result, ManualQuoteShipment) else "packed",
            source_revision=source_revision(result),
        )
        with self._lock:
            active = [
                existing for existing in self._sessions.values()
                if existing.broker == broker
                and not existing.cancelled
                and existing.expires_at > now
                and existing.state not in {"completed", "cancelled", "expired", "error"}
            ]
            if active and not replace_active:
                raise ActiveHandoffConflict([broker])
            for existing in active:
                if replace_active:
                    existing.cancelled = True
                    existing.state = "cancelled"
                    existing.message = "Superseded by a newer handoff."
            self._sessions[request_id] = session
        launch_payload = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "request_id": request_id,
                        "token": token,
                        "bridge_origin": bridge_origin,
                        "broker": broker,
                        "expires_at": session.expires_at.isoformat(),
                    }
                ).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
        url = f"{BROKER_URLS[broker]}#build2quote={launch_payload}"
        # Tabs are created by Build2Quote Bridge.  Keeping browser creation out
        # of the server lets the extension bind the handoff to the new tab
        # before a carrier or identity-provider redirect can occur.
        return {
            "request_id": request_id,
            "broker": broker,
            "state": session.state,
            "message": session.message,
            "expires_at": session.expires_at.isoformat(),
            "url": url,
        }

    def prepare_many(
        self,
        brokers,
        result,
        persist,
        *,
        bridge_origin: str,
        replace_active: bool = False,
        skipped: list[dict] | None = None,
    ) -> list[dict]:
        """Stage one immutable packet; a failed commit preserves existing sessions.

        With a ``skipped`` list, a broker that cannot quote this shipment (for example Old Dominion's
        96 in pallet limit) is left out and reported there instead of stopping every broker.
        """
        preview = self.preview(result)
        staged = BrokerHandoffManager()
        launches = []
        for broker in brokers:
            try:
                launches.append(staged.prepare(broker, result, bridge_origin=bridge_origin, _preview=preview))
            except ValueError as exc:
                if skipped is None or not preview["ready"]:
                    raise
                skipped.append({"broker": broker, "reason": str(exc)})
        if not launches:
            raise ValueError("; ".join(entry["reason"] for entry in skipped or []) or "No broker can quote this.")
        brokers = tuple(launch["broker"] for launch in launches)
        sessions = list(staged._sessions.values())
        now = datetime.now(UTC)
        with self._lock:
            active = [
                existing for existing in self._sessions.values()
                if existing.broker in brokers
                and not existing.cancelled
                and existing.expires_at > now
                and existing.state not in {"completed", "cancelled", "expired", "error"}
            ]
            if active and not replace_active:
                conflicts = sorted({existing.broker for existing in active})
                raise ActiveHandoffConflict(conflicts)
            persist(sessions)
            for existing in active:
                if replace_active:
                    existing.cancelled = True
                    existing.state = "cancelled"
                    existing.message = "Superseded by a newer handoff."
            self._sessions.update(staged._sessions)
        return launches

    def _get(self, request_id: str, token: str | None = None) -> HandoffSession:
        with self._lock:
            session = self._sessions.get(request_id)
        if not session or session.cancelled:
            raise KeyError("Quote handoff is not active")
        if session.expires_at <= datetime.now(UTC):
            session.cancelled = True
            if session.state not in {"filled", "completed"}:
                session.state = "expired"
                session.message = "Autofill timed out. Start a new handoff."
            raise KeyError("Quote handoff expired")
        if token is not None and not secrets.compare_digest(session.token, token):
            raise PermissionError("Invalid quote handoff token")
        return session

    def packet(self, request_id: str, token: str) -> dict:
        session = self._get(request_id, token)
        return {"broker": session.broker, "packet": session.packet, "expires_at": session.expires_at.isoformat()}

    def status(self, request_id: str) -> dict:
        with self._lock:
            session = self._sessions.get(request_id)
            if not session:
                raise KeyError("Quote handoff not found")
            if session.expires_at <= datetime.now(UTC) and not session.cancelled:
                session.cancelled = True
                if session.state not in {"filled", "completed"}:
                    session.state = "expired"
                    session.message = "Autofill timed out. Start a new handoff."
        return {
            "request_id": request_id,
            "broker": session.broker,
            "shipment_name": session.packet["shipment_name"],
            "source_kind": session.source_kind,
            "source_revision": session.source_revision,
            "state": session.state,
            "message": session.message,
            "fields": session.fields,
            "monitoring": not session.cancelled and session.state != "completed",
            "expires_at": session.expires_at.isoformat(),
        }

    def event(self, request_id: str, token: str, event: BridgeEvent) -> HandoffSession:
        session = self._get(request_id, token)
        if session.state == "completed":
            return session
        session.state = event.state
        session.message = event.message
        session.fields = event.fields
        return session

    def cancel(self, request_id: str) -> None:
        session = self._get(request_id)
        session.cancelled = True
        session.state = "cancelled"
        session.message = "Cancelled by Quote Helper."


broker_handoffs = BrokerHandoffManager()
