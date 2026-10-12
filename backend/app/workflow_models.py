from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import BrokerRate, PackingRequest, PackingState, ShippingQuoteInfo


class ManualPallet(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    id: str = Field(default_factory=lambda: str(uuid4()))
    quantity: int = Field(default=1, ge=1, le=200, strict=True)
    length: float = Field(gt=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    weight: float = Field(gt=0, description="Gross pounds per pallet; includes tare")


class ManualQuoteInput(BaseModel):
    shipment_name: str = Field(min_length=1, max_length=200)
    pallets: list[ManualPallet] = Field(min_length=1, max_length=200)
    shipping_info: ShippingQuoteInfo
    # Freight-domain context (3.0); ignored by 2.x readers.
    company_id: str | None = None
    order_ids: list[str] = Field(default_factory=list)
    freight_shipment_id: str | None = None

    @field_validator("shipment_name")
    @classmethod
    def reference_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Shipment reference is required")
        return value

    @model_validator(mode="after")
    def bounded_quantity(self):
        if sum(row.quantity for row in self.pallets) > 200:
            raise ValueError("A manual quote supports at most 200 physical pallets")
        return self


class ManualQuoteShipment(ManualQuoteInput):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_kind: Literal["manual"] = "manual"
    revision: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    broker_rates: list[BrokerRate] = Field(default_factory=list)


class SupportProfile(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["cabinet", "panel", "light_trim", "other"] = "other"
    skus: list[str] = Field(min_length=1)
    packing_state: PackingState = PackingState.FLATPACK
    packaging: Literal["loose", "boxed", "secured_bundle"] | None = None
    max_unsupported_span: float | None = Field(default=None, ge=0)
    max_end_extension: float | None = Field(default=None, ge=0)
    min_contact_band: float | None = Field(default=None, gt=0)
    bundle_limit: int = Field(default=1, ge=1, le=5)
    approved: bool = False
    reviewed_by: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def complete_review(self):
        self.skus = list(dict.fromkeys(sku.strip().upper() for sku in self.skus if sku.strip()))
        if not self.skus:
            raise ValueError("At least one canonical SKU is required")
        if self.approved and not self.reviewed_by.strip():
            raise ValueError("Warehouse reviewer is required")
        if (
            self.approved
            and self.kind == "light_trim"
            and any(
                value is None
                for value in (
                    self.packaging,
                    self.max_unsupported_span,
                    self.max_end_extension,
                    self.min_contact_band,
                )
            )
        ):
            raise ValueError("Approved trim requires packaging, span, end-extension and contact-band limits")
        return self


class ExperimentInput(BaseModel):
    request: PackingRequest
    preset: Literal["current", "conservative"] = "current"
    mode: Literal["quick", "thorough"] = "quick"
    parent_id: str | None = None


PROTOTYPE_LABEL = "Prototype — not approved for shipping"
