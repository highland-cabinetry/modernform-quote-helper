"""Freight domain model: companies, orders, quotes, shipments, expenses and allocations.

These records are deliberately separate from the packing engine's request/result
models. A *quote* is a freight estimate for one or more orders; a *shipment* is the
physical movement of one or more orders; a *shipment expense* is money actually
recorded against a shipment; an *allocation* is an analytical split of that money
across the shipment's orders. None of these is a copy of another.

Money is stored as integer cents. Every identifier is a stable UUID string; the
human-facing numbers (Q-000123, S-000045) are assigned once and never reused.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import AddressInfo, PackingState

# Stored records (read back from the database) ignore fields they do not know, so an older version can
# read records a newer version wrote (rollback). API inputs (the *Input / *Create / *Update classes)
# stay strict. Nested models shared by both are tolerant.


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return str(uuid4())


# --------------------------------------------------------------------------- companies


class ColumnMapping(BaseModel):
    """Extra header names a company's spreadsheets or PDF tables use for each field."""

    model_config = ConfigDict(extra="ignore")
    sku: list[str] = Field(default_factory=list)
    description: list[str] = Field(default_factory=list)
    quantity: list[str] = Field(default_factory=list)
    order_number: list[str] = Field(default_factory=list)
    packing_state: list[str] = Field(default_factory=list)


NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")


class TextLayout(BaseModel):
    """A configurable text-line PDF layout for companies without table-based documents.

    ``line_pattern`` must define the named groups ``sku`` and ``quantity`` and may
    define ``description``. ``order_pattern`` must define the group ``order``.
    """

    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=80)
    detect_pattern: str = Field(min_length=1, description="Regex that must appear in page 1 text")
    order_pattern: str = Field(min_length=1)
    line_pattern: str = Field(min_length=1)
    po_pattern: str | None = None
    date_pattern: str | None = None
    customer_pattern: str | None = None
    ship_to_zip_pattern: str | None = None
    assembled_pattern: str | None = None

    @model_validator(mode="after")
    def patterns_compile(self) -> TextLayout:
        required = {"line_pattern": ("sku", "quantity"), "order_pattern": ("order",)}
        for field_name in (
            "detect_pattern", "order_pattern", "line_pattern", "po_pattern",
            "date_pattern", "customer_pattern", "ship_to_zip_pattern", "assembled_pattern",
        ):
            value = getattr(self, field_name)
            if not value:
                continue
            try:
                compiled = re.compile(value, re.IGNORECASE | re.MULTILINE)
            except re.error as exc:
                raise ValueError(f"{field_name} is not a valid regular expression: {exc}") from exc
            for group in required.get(field_name, ()):
                if group not in compiled.groupindex:
                    raise ValueError(f"{field_name} must define the named group (?P<{group}>...)")
            if NESTED_QUANTIFIER.search(value):
                # Patterns like (a+)+ can take exponential time on some lines and would stall imports.
                raise ValueError(f"{field_name} repeats a group that already repeats, e.g. (\\d+)+; "
                                 "simplify it so imports stay fast")
            if len(value) > 500:
                raise ValueError(f"{field_name} is longer than 500 characters")
        return self


class PricingRule(BaseModel):
    """How a carrier price becomes the customer's freight price.

    price = carrier x (1 + markup_percent / 100) + markup_fixed, rounded UP to a multiple of
    round_up_to (0 = to the cent), and never below minimum.
    """

    model_config = ConfigDict(extra="ignore")
    markup_percent: float = Field(default=0, ge=0, le=500)
    markup_fixed_cents: int = Field(default=0, ge=0, le=10_000_000)
    round_up_to_cents: int = Field(default=0, ge=0, le=100_000)
    minimum_cents: int = Field(default=0, ge=0, le=10_000_000)

    def price(self, carrier_cents: int) -> int:
        raw = carrier_cents * (1 + self.markup_percent / 100) + self.markup_fixed_cents
        cents = math.ceil(round(raw, 6))
        step = self.round_up_to_cents
        if step:
            cents = -(-cents // step) * step
        return max(cents, self.minimum_cents)


class SalesRep(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(default="", max_length=200)
    active: bool = True


EmailKind = Literal["quote", "follow_up", "ready", "shipped", "delivered"]


class EmailTemplate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    subject: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=10_000)


class EmailSettings(BaseModel):
    """Prepared-email settings. Templates use {placeholders}; missing kinds use built-in defaults."""

    model_config = ConfigDict(extra="ignore")
    signature: str = Field(default="", max_length=2000)
    cc_rep: bool = True
    templates: dict[str, EmailTemplate] = Field(default_factory=dict)


class CompanyConfig(BaseModel):
    """Company-scoped import and matching configuration.

    Packing limits stay in the per-company ``PackingSettings`` record; this holds the
    document and SKU conventions that differ between businesses.
    """

    model_config = ConfigDict(extra="ignore")
    # Which product catalog this company's orders are matched against.
    # "default" is the original Quote Helper item master.
    catalog: str = Field(default="default", pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    # Built-in product rule set (see services/company_rules.py). "highland" carries the
    # approved CSB42 footprint, trash-kit and back-order description rules.
    rule_set: Literal["highland", "modernform", "none"] = "none"
    # Enabled document adapters, in priority order.
    adapters: list[str] = Field(default_factory=lambda: ["table"])
    # Text that identifies this company's documents (used to suggest the company).
    document_markers: list[str] = Field(default_factory=list)
    column_mapping: ColumnMapping = Field(default_factory=ColumnMapping)
    text_layouts: list[TextLayout] = Field(default_factory=list)
    # How bare numbers typed as references are stored (e.g. 68606 -> INV68606).
    bare_number_prefix: str = Field(default="", max_length=10)
    default_packing_state: PackingState = PackingState.FLATPACK
    origin: AddressInfo = Field(default_factory=AddressInfo)
    pricing: PricingRule = Field(default_factory=PricingRule)
    reps: list[SalesRep] = Field(default_factory=list)
    email: EmailSettings = Field(default_factory=EmailSettings)

    @field_validator("adapters")
    @classmethod
    def known_adapters(cls, value: list[str]) -> list[str]:
        from .services.adapters import ADAPTERS

        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        unknown = [item for item in cleaned if item not in ADAPTERS]
        if unknown:
            raise ValueError(f"Unknown document adapters: {', '.join(unknown)}")
        if not cleaned:
            raise ValueError("At least one document adapter is required")
        return cleaned


class CompanyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=2, max_length=12, pattern=r"^[A-Z0-9][A-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    active: bool = True
    config: CompanyConfig = Field(default_factory=CompanyConfig)

    @field_validator("code", mode="before")
    @classmethod
    def upper_code(cls, value):
        return str(value or "").strip().upper()


class Company(CompanyInput):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)  # stored: newer fields are ignored
    id: str = Field(default_factory=_id)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# --------------------------------------------------------------------------- orders

OrderStatus = Literal["needs_review", "ready", "reference_only", "shipped", "cancelled"]


class OrderLine(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: str = Field(default_factory=_id)
    line_number: int | None = None
    original_sku: str = Field(min_length=1, max_length=200)
    description: str = ""
    quantity: int = Field(ge=1, le=10_000)
    packing_state: PackingState = PackingState.FLATPACK
    length: float | None = Field(default=None, gt=0)
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    weight: float | None = Field(default=None, gt=0)
    source_ref: str = ""


class OrderInput(BaseModel):
    """Fields a user may correct on an order."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    order_number: str = Field(min_length=1, max_length=60)
    references: list[str] = Field(default_factory=list)
    customer_name: str = ""
    po_number: str = ""
    order_date: str = ""
    ready_date: str = ""
    sales_rep: str = ""
    ship_to: AddressInfo = Field(default_factory=AddressInfo)
    residential: bool | None = None
    liftgate: bool | None = None
    customer_contact: str = Field(default="", max_length=120)
    customer_emails: list[str] = Field(default_factory=list, max_length=10)
    customer_phone: str = Field(default="", max_length=40)
    packing_state: PackingState = PackingState.FLATPACK
    notes: str = ""
    lines: list[OrderLine] = Field(default_factory=list)


class Order(OrderInput):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)  # stored: newer fields are ignored
    id: str = Field(default_factory=_id)
    company_id: str
    status: OrderStatus = "needs_review"
    document_type: str = "unknown"
    source_file_id: str | None = None
    issues: list[str] = Field(default_factory=list)
    revision: int = 1
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @property
    def unit_count(self) -> int:
        return sum(line.quantity for line in self.lines)


class SourceFile(BaseModel):
    id: str = Field(default_factory=_id)
    company_id: str
    batch_id: str
    filename: str
    sha256: str
    size: int
    media_type: str = ""
    stored_path: str = ""
    adapter: str = ""
    status: Literal["imported", "duplicate", "unsupported", "failed", "partial"] = "imported"
    messages: list[str] = Field(default_factory=list)
    order_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


# --------------------------------------------------------------------------- quotes

QuoteKind = Literal["packed", "measured", "external"]  # external: priced without a plan (e.g. imported)
QuoteStatus = Literal["draft", "final", "superseded", "void"]


class QuoteCost(BaseModel):
    """Optional carrier price the freight manager chose to record by hand.

    Quote Helper never collects rates automatically; this is a manual entry used for
    quote-versus-actual comparisons.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    amount_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    carrier: str = Field(default="", max_length=120)
    broker: str = Field(default="", max_length=120)
    reference: str = Field(default="", max_length=120)
    expires_on: date | None = None
    # What the customer was quoted for freight (carrier cost plus markup), entered by hand.
    customer_price_cents: int | None = Field(default=None, ge=0, le=100_000_000)


class QuoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    company_id: str | None = None
    kind: QuoteKind
    packing_result_id: str | None = None
    manual_quote_id: str | None = None
    order_ids: list[str] = Field(default_factory=list)
    order_references: list[str] = Field(default_factory=list, description="Typed references; created if missing")
    freight_shipment_id: str | None = None
    notes: str = ""
    cost: QuoteCost = Field(default_factory=QuoteCost)

    @model_validator(mode="after")
    def one_source(self) -> QuoteCreate:
        if self.kind == "packed" and not self.packing_result_id:
            raise ValueError("A packed quote needs the saved pallet plan it was built from")
        if self.kind == "measured" and not self.manual_quote_id:
            raise ValueError("A measured quote needs its measured-pallet record")
        if len(set(self.order_ids)) != len(self.order_ids):
            raise ValueError("An order can be included in a quote only once")
        return self


class QuoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: QuoteStatus | None = None
    notes: str | None = None
    cost: QuoteCost | None = None
    order_ids: list[str] | None = None


LocationType = Literal["commercial", "residential", "limited_access"]
CustomerQuoteStatus = Literal["not_sent", "sent", "approved", "declined", "expired"]


class DeliveryTerms(BaseModel):
    """Where and how the freight is delivered, as quoted (snapshot on each quote)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    postal_code: str = Field(default="", max_length=12)
    city: str = Field(default="", max_length=80)
    state: str = Field(default="", max_length=40)
    location_type: LocationType = "commercial"
    liftgate: bool = False
    appointment: bool = False
    call_ahead: bool = False
    inside_delivery: bool = False
    notes: str = Field(default="", max_length=500)

    @classmethod
    def from_shipping(cls, info) -> DeliveryTerms:
        """From a pallet plan's or measured quote's ``ShippingQuoteInfo``."""
        services = info.accessorials
        location = ("limited_access" if getattr(services, "limited_access", False)
                    else "residential" if services.residential else "commercial")
        return cls(postal_code=info.destination.postal_code, city=info.destination.city,
                   state=info.destination.state, location_type=location, liftgate=services.liftgate,
                   appointment=services.appointment_delivery, call_ahead=services.delivery_call_ahead,
                   inside_delivery=getattr(services, "inside_delivery", False))

    def labels(self) -> list[str]:
        names = {"commercial": "Commercial", "residential": "Residential", "limited_access": "Limited access"}
        return [names[self.location_type]] + [label for flag, label in (
            (self.liftgate, "Liftgate"), (self.appointment, "Appointment"), (self.call_ahead, "Call ahead"),
            (self.inside_delivery, "Inside delivery")) if flag]


class QuoteOption(BaseModel):
    """One carrier/broker price found for this quote (typed by the user from the broker page)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: str = Field(default_factory=_id)
    source: str = Field(default="other", max_length=40, description="echo, myunishippers, dsv, tql, odfl or other")
    source_label: str = Field(default="", max_length=80)
    carrier: str = Field(default="", max_length=120)
    service: str = Field(default="", max_length=80)
    transit_days: int | None = Field(default=None, ge=0, le=60)
    price_cents: int = Field(ge=0, le=100_000_000)
    reference: str = Field(default="", max_length=120)
    expires_on: date | None = None
    notes: str = Field(default="", max_length=500)
    recorded_at: datetime = Field(default_factory=_now)


class QuoteSummary(BaseModel):
    """Measured numbers copied from the source when the quote is recorded.

    Keeping this snapshot means a quote stays readable even if the underlying pallet
    plan is later removed by an item-master replacement.
    """

    pallets: int = 0
    standard_pallets: int = 0
    long_pallets: int = 0
    total_weight: float = 0
    item_weight: float = 0
    total_units: int = 0
    freight_ready: bool = False
    optimizer: str = "standard"
    rows: list[dict] = Field(default_factory=list)
    order_weights: dict[str, float] = Field(default_factory=dict)
    order_pallet_shares: dict[str, float] = Field(default_factory=dict)


class Quote(BaseModel):
    id: str = Field(default_factory=_id)
    number: str = ""
    company_id: str
    kind: QuoteKind
    status: QuoteStatus = "final"
    name: str = ""
    packing_result_id: str | None = None
    manual_quote_id: str | None = None
    order_ids: list[str] = Field(default_factory=list)
    freight_shipment_id: str | None = None
    role: Literal["individual", "combined"] = "individual"
    summary: QuoteSummary = Field(default_factory=QuoteSummary)
    cost: QuoteCost = Field(default_factory=QuoteCost)
    notes: str = ""
    legacy: bool = False
    # 3.2: delivery terms as quoted, the prices found per broker page and which one was used,
    # and the customer's answer.
    terms: DeliveryTerms | None = None
    options: list[QuoteOption] = Field(default_factory=list)
    selected_option_id: str | None = None
    price_manual: bool = False  # customer price typed by hand instead of from the pricing rule
    customer_status: CustomerQuoteStatus = "not_sent"
    sent_at: datetime | None = None
    responded_at: datetime | None = None
    response_note: str = Field(default="", max_length=1000)
    external_source: str = ""
    external_ref: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @property
    def selected_option(self) -> QuoteOption | None:
        return next((option for option in self.options if option.id == self.selected_option_id), None)


# --------------------------------------------------------------------------- shipments

ShipmentStatus = Literal["planning", "booked", "in_transit", "delivered", "closed", "cancelled"]
ShipmentPurpose = Literal["standard", "back_order", "warranty", "damage_warranty", "correction", "transfer", "sister"]
ChargeSource = Literal["quote", "rep_assigned", "backcharge", "manual", "none"]
ACTIVE_SHIPMENT_STATUSES = ("planning", "booked", "in_transit", "delivered", "closed")
LOCKED_SHIPMENT_STATUSES = ("closed", "cancelled")


class FreightShipmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="", max_length=200)
    status: ShipmentStatus = "planning"
    carrier: str = Field(default="", max_length=120)
    broker: str = Field(default="", max_length=120)
    pro_number: str = Field(default="", max_length=60)
    bol_number: str = Field(default="", max_length=60)
    pickup_date: date | None = None
    delivery_date: date | None = None
    destination: AddressInfo = Field(default_factory=AddressInfo)
    # What the customer pays for this freight (Freight Manager "Customer Payment").
    customer_charge_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    notes: str = ""
    purpose: ShipmentPurpose = "standard"
    expected_delivery_date: date | None = None
    # Where customer_charge_cents came from (Freight Manager: backcharge > rep charges > quote).
    charge_source: ChargeSource = "quote"
    rep_charge_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    backcharge_cents: int | None = Field(default=None, ge=0, le=100_000_000)


class FreightShipmentCreate(FreightShipmentInput):
    company_id: str | None = None
    order_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_orders(self) -> FreightShipmentCreate:
        if len(set(self.order_ids)) != len(self.order_ids):
            raise ValueError("An order can be added to a shipment only once")
        return self


class PalletMeasurement(BaseModel):
    model_config = ConfigDict(extra="ignore")
    length: float = Field(gt=0, le=720)
    width: float = Field(gt=0, le=120)
    height: float = Field(gt=0, le=144)
    weight: float = Field(gt=0, le=20_000)
    quantity: int = Field(default=1, ge=1, le=60)
    note: str = Field(default="", max_length=200)


class ActualMeasurements(BaseModel):
    """What actually shipped: warehouse-measured pallets, and the carrier's billed weight/class."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    pallets: list[PalletMeasurement] = Field(default_factory=list, max_length=60)
    measured_on: date | None = None
    source: Literal["warehouse", "carrier_bill", "bol", "other"] = "warehouse"
    billed_weight: float | None = Field(default=None, gt=0, le=100_000)
    billed_class: str = Field(default="", max_length=10)
    notes: str = Field(default="", max_length=1000)

    @property
    def pallet_count(self) -> int:
        return sum(pallet.quantity for pallet in self.pallets)

    @property
    def total_weight(self) -> float:
        return round(sum(pallet.weight * pallet.quantity for pallet in self.pallets), 1)


class FreightShipment(FreightShipmentInput):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)  # stored: newer fields are ignored
    id: str = Field(default_factory=_id)
    number: str = ""
    company_id: str
    order_ids: list[str] = Field(default_factory=list)
    combined_quote_id: str | None = None
    actual: ActualMeasurements | None = None
    external_source: str = ""
    external_ref: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


ExpenseKind = Literal["linehaul", "fuel", "accessorial", "redelivery", "reweigh", "storage", "adjustment", "other"]


class ShipmentExpenseInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    amount_cents: int = Field(ge=-100_000_000, le=100_000_000)
    kind: ExpenseKind = "linehaul"
    vendor: str = Field(default="", max_length=120)
    invoice_reference: str = Field(default="", max_length=120)
    incurred_on: date | None = None
    notes: str = Field(default="", max_length=1000)
    category: str = Field(default="", max_length=80, description="Free label, e.g. the Freight Manager charge type")

    @model_validator(mode="after")
    def non_zero(self) -> ShipmentExpenseInput:
        if self.amount_cents == 0:
            raise ValueError("Enter a non-zero amount")
        if self.amount_cents < 0 and self.kind != "adjustment":
            raise ValueError("Only adjustments (credits) can be negative")
        return self


class ShipmentExpense(ShipmentExpenseInput):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)  # stored: newer fields are ignored
    id: str = Field(default_factory=_id)
    shipment_id: str
    voided: bool = False
    void_reason: str = ""
    external_source: str = ""
    external_ref: str = ""
    created_at: datetime = Field(default_factory=_now)


class UnassignedExpense(ShipmentExpenseInput):
    """A freight bill not (yet) tied to a shipment, e.g. a 3PL invoice for a period."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: str = Field(default_factory=_id)
    company_id: str
    voided: bool = False
    void_reason: str = ""
    external_source: str = ""
    external_ref: str = ""
    created_at: datetime = Field(default_factory=_now)


RevenueKind = Literal["accessorial_rebill", "claim_recovery", "other"]


class ShipmentRevenueInput(BaseModel):
    """Freight revenue beyond the shipment's customer charge: re-billed accessorials, claim recoveries."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    amount_cents: int = Field(gt=0, le=100_000_000)
    kind: RevenueKind = "other"
    payer: str = Field(default="", max_length=120)
    reference: str = Field(default="", max_length=120)
    received_on: date | None = None
    notes: str = Field(default="", max_length=1000)


class ShipmentRevenue(ShipmentRevenueInput):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: str = Field(default_factory=_id)
    company_id: str
    shipment_id: str | None = None
    voided: bool = False
    void_reason: str = ""
    external_source: str = ""
    external_ref: str = ""
    created_at: datetime = Field(default_factory=_now)


class EmailRecord(BaseModel):
    """A prepared customer email (opened in the mail program; sending is the user's action)."""

    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=_id)
    company_id: str
    kind: EmailKind
    entity_type: Literal["quote", "shipment", "order"]
    entity_id: str
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str = ""
    created_at: datetime = Field(default_factory=_now)


AllocationMethod = Literal["equal", "weight", "pallets", "manual"]


class AllocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: AllocationMethod
    manual_cents: dict[str, int] = Field(default_factory=dict, description="order_id -> cents (manual only)")


class AllocationLine(BaseModel):
    order_id: str
    basis: float
    share: float
    amount_cents: int
    rounding_adjustment_cents: int = 0


class Allocation(BaseModel):
    shipment_id: str
    method: AllocationMethod
    total_cents: int
    basis_source: str = ""
    lines: list[AllocationLine]
    created_at: datetime = Field(default_factory=_now)


class AuditEntry(BaseModel):
    id: int | None = None
    entity_type: str
    entity_id: str
    action: str
    summary: str = ""
    details: dict = Field(default_factory=dict)
    actor: str = "freight-manager"
    at: datetime = Field(default_factory=_now)


# --------------------------------------------------------------------------- 3.2 workflow inputs (strict)

QUOTE_OPTION_SOURCES = {
    "echo": "Echo", "myunishippers": "MyUniShippers", "dsv": "DSV", "tql": "TQL", "odfl": "ODFL", "other": "Other",
}


class DeliveryTermsInput(DeliveryTerms):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class QuoteOptionInput(BaseModel):
    """A price the user found on one broker/carrier page (typed by hand; never collected automatically)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source: str = Field(default="other", max_length=40)
    source_label: str = Field(default="", max_length=80)
    carrier: str = Field(default="", max_length=120)
    service: str = Field(default="", max_length=80)
    transit_days: int | None = Field(default=None, ge=0, le=60)
    price_cents: int = Field(ge=0, le=100_000_000)
    reference: str = Field(default="", max_length=120)
    expires_on: date | None = None
    notes: str = Field(default="", max_length=500)

    @field_validator("source", mode="before")
    @classmethod
    def source_slug(cls, value):
        text = re.sub(r"[^a-z0-9_-]", "", str(value or "").strip().lower())
        return text or "other"

    @model_validator(mode="after")
    def default_label(self) -> QuoteOptionInput:
        if not self.source_label:
            self.source_label = QUOTE_OPTION_SOURCES.get(self.source, self.source.upper())
        return self


class ActualMeasurementsInput(ActualMeasurements):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class UnassignedExpenseCreate(ShipmentExpenseInput):
    company_id: str | None = None


class ShipmentRevenueCreate(ShipmentRevenueInput):
    """Extra revenue recorded at company level, optionally for a shipment (claims may be unmatched)."""

    company_id: str | None = None
    shipment_id: str | None = None
