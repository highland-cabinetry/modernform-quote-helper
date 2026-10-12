from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


class MatchStatus(StrEnum):
    EXACT = "exact"
    CONFIRMED_ALIAS = "confirmed_alias"
    FUZZY = "fuzzy_review"
    MANUAL = "manual"
    UNMATCHED = "unmatched"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class PackingState(StrEnum):
    FLATPACK = "flatpack"
    ASSEMBLED = "assembled"


class PackingRole(StrEnum):
    STANDARD = "standard"
    BASE_PANEL = "base_panel"
    TOP_ACCESSORY = "top_accessory"


class ItemKind(StrEnum):
    CABINET = "cabinet"
    CABINET_COMPONENT = "cabinet_component"
    ACCESSORY = "accessory"
    OTHER = "other"


class ItemMaster(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: int | None = None
    sku: str = Field(min_length=1, max_length=100)
    manufacturer_sku: str | None = None
    description: str = ""
    manufacturer_dimensions: str | None = None
    flatpack_length: float | None = Field(default=None, gt=0)
    flatpack_width: float | None = Field(default=None, gt=0)
    flatpack_height: float | None = Field(default=None, gt=0)
    assembled_length: float | None = Field(default=None, gt=0)
    assembled_width: float | None = Field(default=None, gt=0)
    assembled_height: float | None = Field(default=None, gt=0)
    weight: float = Field(gt=0, description="Pounds per unit")
    category: str = "Miscellaneous"
    item_kind: ItemKind = ItemKind.OTHER
    stackable: bool = True
    fragile: bool = False
    must_stay_upright: bool = False
    no_upside_down: bool = True
    allowed_rotations: list[str] = Field(default_factory=lambda: ["LWH", "WLH"])
    max_stack_layers: int | None = Field(default=None, ge=1)
    max_weight_on_top: float = Field(default=1000.0, ge=0)
    load_bearing_strength: float = Field(default=1000.0, ge=0)
    preferred_pallet_type: Literal["standard", "long", "either"] = "either"
    freight_class: str | None = None
    nmfc: str | None = None
    color: str | None = None
    rules_reviewed: bool = False
    packing_role: PackingRole = PackingRole.STANDARD
    bundle_max_units: int = Field(default=1, ge=1, le=5)
    bundle_approved: bool = False
    allow_edge_standing: bool = False
    overhang_approved: bool = False
    max_overhang_per_side: float = Field(default=0.0, ge=0, le=12)
    active: bool = True

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_dimensions(cls, value):
        """Accept V2.0 payloads/templates as flatpack-only without inventing assembled dimensions."""

        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        for axis in ("length", "width", "height"):
            legacy = migrated.pop(axis, None)
            migrated.setdefault(f"flatpack_{axis}", legacy)
        category = str(migrated.get("category") or "").strip().lower()
        if not migrated.get("item_kind"):
            if category == "face frame and door":
                migrated["item_kind"] = ItemKind.CABINET_COMPONENT
            elif category == "accessory":
                migrated["item_kind"] = ItemKind.ACCESSORY
            elif "cabinet" in category:
                migrated["item_kind"] = ItemKind.CABINET
            else:
                migrated["item_kind"] = ItemKind.OTHER
        inferred_kind = str(migrated["item_kind"]).removeprefix("ItemKind.").lower()
        if "bundle_approved" not in migrated:
            migrated["bundle_approved"] = inferred_kind == ItemKind.ACCESSORY.value
        if "bundle_max_units" not in migrated and migrated["bundle_approved"]:
            migrated["bundle_max_units"] = 5
        if "packing_role" not in migrated and inferred_kind == ItemKind.ACCESSORY.value:
            migrated["packing_role"] = PackingRole.TOP_ACCESSORY
        return migrated

    @field_validator("allowed_rotations")
    @classmethod
    def rotations_are_valid(cls, value: list[str]) -> list[str]:
        valid = {"LWH", "WLH", "LHW", "HLW", "WHL", "HWL"}
        normalized = list(dict.fromkeys(v.upper() for v in value))
        invalid = set(normalized) - valid
        if invalid:
            raise ValueError(f"Unsupported rotations: {', '.join(sorted(invalid))}")
        if not normalized:
            raise ValueError("At least one rotation is required")
        return normalized

    @model_validator(mode="after")
    def upright_rotations_only(self) -> ItemMaster:
        if self.must_stay_upright:
            self.allowed_rotations = [r for r in self.allowed_rotations if r[2] == "H"]
            if not self.allowed_rotations:
                self.allowed_rotations = ["LWH", "WLH"]
        return self

    @model_validator(mode="after")
    def dimensions_are_complete(self) -> ItemMaster:
        sets = {
            PackingState.FLATPACK: (self.flatpack_length, self.flatpack_width, self.flatpack_height),
            PackingState.ASSEMBLED: (self.assembled_length, self.assembled_width, self.assembled_height),
        }
        for state, dimensions in sets.items():
            populated = [value is not None for value in dimensions]
            if any(populated) and not all(populated):
                raise ValueError(f"{state.value} dimensions must include length, width, and height")
        if not any(all(value is not None for value in dimensions) for dimensions in sets.values()):
            raise ValueError("At least one complete flatpack or assembled dimension set is required")
        return self

    def dimensions_for(self, state: PackingState) -> tuple[float, float, float] | None:
        dimensions = (
            (self.flatpack_length, self.flatpack_width, self.flatpack_height)
            if state == PackingState.FLATPACK
            else (self.assembled_length, self.assembled_width, self.assembled_height)
        )
        if any(value is None for value in dimensions):
            return None
        return tuple(float(value) for value in dimensions)  # type: ignore[arg-type,return-value]


class SourceReference(BaseModel):
    source_ref: str
    original_sku: str
    quantity: int = Field(ge=1)
    line_number: int | None = None


class PackingSlipLine(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_ref: str = Field(default="Manual entry", min_length=1)
    original_sku: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1, le=10_000)
    packing_state: PackingState = PackingState.FLATPACK
    description: str = ""
    length: float | None = Field(default=None, gt=0)
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    weight: float | None = Field(default=None, gt=0)
    category: str | None = None
    line_number: int | None = None


class SkuAlias(BaseModel):
    id: int
    alias_sku: str
    normalized_alias: str
    target_item_id: int
    target_sku: str
    target_description: str = ""
    created_at: str
    updated_at: str


class SkuAliasCreate(BaseModel):
    alias_sku: str = Field(min_length=1, max_length=200)
    target_item_id: int = Field(gt=0)


class MatchedLine(BaseModel):
    line: PackingSlipLine
    normalized_sku: str
    match_status: MatchStatus
    confidence: float = Field(ge=0, le=100)
    matched_item: ItemMaster | None = None
    review_message: str | None = None


class CombinedItem(BaseModel):
    key: str
    master_item: ItemMaster
    packing_state: PackingState = PackingState.FLATPACK
    quantity: int = Field(ge=1)
    sources: list[SourceReference]
    original_skus: list[str]
    match_status: MatchStatus = MatchStatus.EXACT
    match_confidence: float = 100.0


class PalletSpec(BaseModel):
    name: Literal["standard", "long"]
    display_name: str
    length: float = Field(gt=0)
    width: float = Field(gt=0)
    deck_height: float = Field(default=6.0, ge=0)
    max_total_height: float = Field(default=48.0, gt=0)
    max_total_weight: float = Field(default=2500.0, gt=0)
    tare_weight: float = Field(default=45.0, ge=0)

    @property
    def cargo_height(self) -> float:
        return max(0.0, self.max_total_height - self.deck_height)


def _default_ignored_prefixes() -> list[str]:
    from .edition import current as current_edition

    if current_edition().name == "highland":
        return ["MB", "WHS", "ESS", "GRS", "MSL", "MTS", "ONB", "SBS", "SDW", "SSG", "RTA", "HC"]
    return []   # other editions list their prefixes as cabinet colors


class CabinetColor(BaseModel):
    """A cabinet color / door style and the item prefix its SKUs carry (Natural Shaker → NS-)."""

    name: str = Field(default="", max_length=80)
    prefix: str = Field(min_length=1, max_length=12)

    @field_validator("prefix")
    @classmethod
    def clean_prefix(cls, value: str) -> str:
        cleaned = value.strip().upper().rstrip("-").strip()
        if not cleaned:
            raise ValueError("Item prefix is required")
        return cleaned + "-"


class PackingSettings(BaseModel):
    standard_pallet: PalletSpec = Field(
        default_factory=lambda: PalletSpec(
            name="standard", display_name="Standard 48 × 40", length=48, width=40, max_total_weight=1200
        )
    )
    long_pallet: PalletSpec = Field(
        default_factory=lambda: PalletSpec(
            name="long", display_name="Long 96 × 40", length=96, width=40, tare_weight=80, max_total_weight=1600
        )
    )
    # Total freight height limits (deck included) by packing state. The warehouse
    # builds flatpack loads lower than assembled loads; a pallet carrying any
    # assembled carton uses the assembled limit.
    flatpack_max_total_height: float = Field(default=66.0, gt=0, le=120)
    assembled_max_total_height: float = Field(default=80.0, gt=0, le=120)
    # Overhang allowed for any carton, per side, beyond the pallet deck. Used only
    # when a carton or pile needs it, and always reported in freight dimensions.
    general_overhang_per_side: float = Field(default=2.0, ge=0, le=6)
    # Where 80"+ panels, pantry/OC boxes and trim go on a long pallet.
    long_items_position: Literal["auto", "bottom", "top"] = "auto"
    # Rigid 96 x 40 x 0.5 in load boards the warehouse lays over uneven layers
    # "only when needed, usually once, at most twice". Weight is an estimate
    # until the warehouse weighs one; it is added to the pallet tare.
    load_board_weight: float = Field(default=35.0, ge=0, le=200)
    load_board_thickness: float = Field(default=0.5, gt=0, le=2)
    max_load_boards_per_pallet: int = Field(default=2, ge=0, le=4)
    # Flatpack cabinet cartons may be stood on edge (like books) to fill deck strips
    # too narrow for a flat pile. Door/face-frame cartons and accessories never are.
    flatpack_cartons_on_edge: bool = True
    # "Dedicated truck" optimizer (bulk orders on a box truck or dry van): pallets
    # may be heavier, and the plan minimises truck floor length instead of pallet count.
    truckload_standard_max_weight: float = Field(default=2400.0, gt=0, le=5000)
    truckload_long_max_weight: float = Field(default=1600.0, gt=0, le=5000)
    minimum_support_ratio: float = Field(default=0.75, gt=0, le=1)
    fuzzy_match_threshold: float = Field(default=88.0, ge=0, le=100)
    fragile_heavy_threshold: float = Field(default=25.0, ge=0)
    max_pallets: int = Field(default=50, ge=1, le=200)
    accessory_only_min_freight_height: float = Field(default=24.0, ge=0, le=120)
    support_height_tolerance: float = Field(default=0.45, ge=0, le=3)
    require_dunnage_for_height_gap: bool = True
    require_accessory_dunnage: bool = False
    approved_accessory_overhang_per_side: float = Field(default=1.0, ge=0, le=6)
    same_sku_bundle_limit: int = Field(default=5, ge=1, le=5)
    quote_origin: AddressInfo = Field(default_factory=lambda: AddressInfo())
    default_commodity_description: str = "Kitchen cabinets and accessories"
    default_nmfc: str = ""
    sku_prefixes_to_ignore: list[str] = Field(default_factory=lambda: _default_ignored_prefixes())

    # Cabinet colors / door styles and their item prefixes (the catalog workbook's "Cabinet Colors"
    # sheet). Their prefixes are ignored when looking up the catalog, like sku_prefixes_to_ignore.
    cabinet_colors: list[CabinetColor] = Field(default_factory=list)

    @field_validator("sku_prefixes_to_ignore")
    @classmethod
    def required_cabinet_prefixes_are_present(cls, value: list[str]) -> list[str]:
        from .edition import current as current_edition

        normalized = [prefix.strip().upper().rstrip("-") for prefix in value if prefix.strip()]
        # Highland's colour prefixes are always ignored in the Highland edition.
        required = ["WHS", "ESS", "GRS", "MSL", "MTS", "ONB", "SBS", "SDW", "SSG"] \
            if current_edition().name == "highland" else []
        return list(dict.fromkeys([*normalized, *required]))

    @model_validator(mode="after")
    def color_prefixes_are_ignored(self) -> PackingSettings:
        colors = [color.prefix.rstrip("-") for color in self.cabinet_colors]
        if any(prefix not in self.sku_prefixes_to_ignore for prefix in colors):
            self.sku_prefixes_to_ignore = list(dict.fromkeys([*self.sku_prefixes_to_ignore, *colors]))
        return self


class ManualOverride(BaseModel):
    unit_id: str
    pallet_number: int = Field(ge=1)
    pallet_type: Literal["standard", "long"] | None = None
    x: float
    y: float
    z: float = Field(ge=0, description="Cargo coordinate above the pallet deck")
    rotation: str = "LWH"
    locked: bool = True
    manually_placed: bool = True


class RotationPreference(BaseModel):
    """Preferred deck-plane orientation without fixing a carton to stale coordinates."""

    unit_id: str
    rotation: str = "LWH"


class OverrideOutcome(BaseModel):
    unit_id: str
    kind: Literal["exact_lock", "rotation_preference"]
    status: Literal["accepted", "rejected", "fallback"]
    requested_rotation: str | None = None
    applied_rotation: str | None = None
    message: str = ""


class AddressInfo(BaseModel):
    name: str = ""
    street: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    phone: str = ""
    email: str = ""


class Accessorials(BaseModel):
    """Delivery services for the quote. Location type: residential and limited_access are exclusive
    (neither = commercial); the broker packet carries every flag."""

    residential: bool = False
    liftgate: bool = False
    appointment_delivery: bool = True
    delivery_call_ahead: bool = False
    limited_access: bool = False
    inside_delivery: bool = False


class ShippingQuoteInfo(BaseModel):
    confirmed: bool = False
    customer_references: list[str] = Field(default_factory=list)
    origin: AddressInfo = Field(default_factory=AddressInfo)
    destination: AddressInfo = Field(default_factory=AddressInfo)
    accessorials: Accessorials = Field(default_factory=Accessorials)
    commodity_description: str = "Kitchen cabinets and accessories"
    # Retained only so older saved shipments remain readable. V2.2 quote
    # handoffs calculate class per pallet and leave the broker's date alone.
    freight_class: str = ""
    nmfc: str = ""
    pickup_date: str | None = None
    source_provenance: list[str] = Field(default_factory=list)


class PackingRequest(BaseModel):
    shipment_name: str = Field(default="New shipment", max_length=200)
    lines: list[PackingSlipLine] = Field(min_length=1)
    settings: PackingSettings = Field(default_factory=PackingSettings)
    overrides: list[ManualOverride] = Field(default_factory=list)
    rotation_preferences: list[RotationPreference] = Field(default_factory=list)
    split_bundle_ids: list[str] = Field(default_factory=list)
    allow_fuzzy_matches: bool = False
    save_result: bool = True
    shipping_info: ShippingQuoteInfo | None = None
    # "standard": LTL pallets (fewest pallets, then smallest deck).
    # "truckload": dedicated box truck / dry van (least truck floor length, heavier pallets).
    optimizer: Literal["standard", "truckload"] = "standard"
    # Freight-domain context (3.0). Optional, so requests saved by 2.x still load and
    # 2.x still reads requests saved by 3.0 (unknown keys are ignored there).
    company_id: str | None = None
    order_ids: list[str] = Field(default_factory=list)
    freight_shipment_id: str | None = None


class ValidationIssue(BaseModel):
    code: str
    severity: Severity
    message: str
    pallet_number: int | None = None
    unit_ids: list[str] = Field(default_factory=list)


class SupportContact(BaseModel):
    supporting_unit_id: str
    contact_area: float
    contact_ratio: float
    height_gap: float = 0.0
    requires_dunnage: bool = False


class PlacedItem(BaseModel):
    unit_id: str
    item_key: str
    sku: str
    original_sku: str
    packing_state: PackingState = PackingState.FLATPACK
    description: str
    category: str
    item_kind: ItemKind = ItemKind.OTHER
    source_refs: list[str]
    x: float
    y: float
    z: float
    length: float
    width: float
    height: float
    flatpack_dimensions: tuple[float, float, float] | None = None
    assembled_dimensions: tuple[float, float, float] | None = None
    rotation: str
    weight: float
    fragile: bool
    stackable: bool
    must_stay_upright: bool
    allowed_rotations: list[str]
    max_stack_layers: int | None
    max_weight_on_top: float
    load_bearing_strength: float
    color: str
    locked: bool = False
    manually_placed: bool = False
    support_ratio: float = 1.0
    packing_role: PackingRole = PackingRole.STANDARD
    rules_reviewed: bool = False
    allow_edge_standing: bool = False
    overhang_approved: bool = False
    max_overhang_per_side: float = 0.0
    bundle_id: str | None = None
    bundle_position: int | None = None
    bundle_size: int | None = None
    bundle_approved: bool = False
    pair_group_id: str | None = None
    paired_family: str | None = None
    paired_unit_ids: list[str] = Field(default_factory=list)
    support_contacts: list[SupportContact] = Field(default_factory=list)
    requires_dunnage: bool = False

    @model_validator(mode="before")
    @classmethod
    def derive_legacy_item_kind(cls, value):
        if not isinstance(value, dict) or value.get("item_kind"):
            return value
        migrated = dict(value)
        category = str(migrated.get("category") or "").lower()
        if category == "face frame and door":
            migrated["item_kind"] = ItemKind.CABINET_COMPONENT
        elif category == "accessory":
            migrated["item_kind"] = ItemKind.ACCESSORY
        elif "cabinet" in category:
            migrated["item_kind"] = ItemKind.CABINET
        else:
            migrated["item_kind"] = ItemKind.OTHER
        return migrated


class UnpackedItem(BaseModel):
    unit_id: str
    sku: str
    original_sku: str
    packing_state: PackingState = PackingState.FLATPACK
    description: str
    source_refs: list[str]
    weight: float
    dimensions: tuple[float, float, float]
    reason_code: str
    reason: str


class LayerMetric(BaseModel):
    """Audit metrics for one horizontal placement layer."""

    z: float
    item_count: int
    coverage_percent: float
    largest_void_percent: float
    fragmentation_percent: float
    cabinet_count: int = 0
    target_cabinet_count: int = 0
    close_reason: str = ""


class ConsolidationAttempt(BaseModel):
    """Why a whole-pallet consolidation did or did not succeed."""

    source_pallets: list[int] = Field(default_factory=list)
    target_pallet_type: Literal["standard", "long"] = "long"
    status: Literal["success", "blocked"]
    blocker_code: str = ""
    message: str = ""


class PalletPlan(BaseModel):
    pallet_number: int
    pallet_type: Literal["standard", "long"]
    display_name: str
    length: float
    width: float
    max_total_height: float
    deck_height: float
    max_cargo_height: float
    stack_height: float
    freight_height: float
    item_weight: float
    tare_weight: float
    total_weight: float
    max_total_weight: float
    used_volume_percent: float
    items: list[PlacedItem]
    item_quantities: dict[str, int]
    warnings: list[ValidationIssue] = Field(default_factory=list)
    center_of_gravity: tuple[float, float, float]
    load_min_x: float = 0.0
    load_min_y: float = 0.0
    load_max_x: float = 0.0
    load_max_y: float = 0.0
    load_length: float = 0.0
    load_width: float = 0.0
    overhang: dict[str, float] = Field(default_factory=dict)
    layer_coverage_percent: float = 0.0
    largest_void_percent: float = 0.0
    top_support_coverage_percent: float = 0.0
    layer_metrics: list[LayerMetric] = Field(default_factory=list)
    density_pcf: float = 0.0
    estimated_freight_class: str = ""
    required_actions: list[str] = Field(default_factory=list)
    # Load boards are warehouse dunnage, not order items: their weight is part of
    # tare_weight and they never enter quantity accounting.
    load_boards: list[PlacedItem] = Field(default_factory=list)

    @computed_field
    @property
    def load_fill_percent(self) -> float:
        """Carton volume as a percentage of the current packed load envelope."""

        envelope_volume = self.load_length * self.load_width * self.stack_height
        if envelope_volume <= 0:
            return 0.0
        item_volume = sum(item.length * item.width * item.height for item in self.items)
        return round(min(100.0, item_volume / envelope_volume * 100), 1)


class ShipmentSummary(BaseModel):
    total_items: int
    packed_items: int
    unpacked_items: int
    total_item_weight: float
    packed_item_weight: float
    total_freight_weight: float
    total_pallets: int
    pallet_types: dict[str, int]


class ComparisonEstimate(BaseModel):
    """Transient, review-only alternative; never persisted as a packing result."""

    status: Literal["review_only"] = "review_only"
    label: str = "Estimate — review required"
    pallets: list[PalletPlan]
    summary: ShipmentSummary
    issues: list[ValidationIssue] = Field(default_factory=list)


class BrokerRate(BaseModel):
    broker: Literal["echoship", "tql", "dsv", "myunishippers", "odfl"]
    carrier: str = ""
    service: str = ""
    price: float | None = Field(default=None, ge=0)
    currency: str = "USD"
    transit_estimate: str = ""
    quote_reference: str = ""
    raw_text: str = ""
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContainedItem(BaseModel):
    unit_id: str
    host_unit_id: str
    sku: str
    original_sku: str
    source_refs: list[str] = Field(default_factory=list)
    packing_state: PackingState = PackingState.ASSEMBLED
    weight: float = Field(gt=0)
    host_base_weight: float = Field(gt=0)


class PackingResult(BaseModel):
    purpose: Literal["production", "prototype"] = "production"
    id: str = Field(default_factory=lambda: str(uuid4()))
    shipment_name: str
    engine: str
    engine_version: str
    optimizer: Literal["standard", "truckload"] = "standard"
    # Dedicated-truck estimate (truckload optimizer only): floor length, rows and truck size.
    truck_load: dict | None = None
    plan_status: Literal["ready", "blocked"] = "ready"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: ShipmentSummary
    pallets: list[PalletPlan]
    unpacked: list[UnpackedItem]
    contained_items: list[ContainedItem] = Field(default_factory=list)
    expected_unit_ids: list[str] = Field(default_factory=list)
    warnings: list[ValidationIssue]
    match_review: list[MatchedLine] = Field(default_factory=list)
    candidate_scores: list[dict[str, float | str]] = Field(default_factory=list)
    consolidation_attempts: list[ConsolidationAttempt] = Field(default_factory=list)
    override_outcomes: list[OverrideOutcome] = Field(default_factory=list)
    human_review_required: bool
    shipping_info: ShippingQuoteInfo | None = None
    broker_rates: list[BrokerRate] = Field(default_factory=list)
    disclaimer: str = (
        "This is a deterministic heuristic pallet plan, not a structural engineering certification. "
        "A trained warehouse operator must verify packaging condition, restraint, center of gravity, "
        "and real-world load bearing before shipment."
    )

    @computed_field
    @property
    def accounting_errors(self) -> list[str]:
        from collections import Counter

        from .services.item_accounting import _is_b18
        from .services.sku_matching import normalize_sku
        placed = [item for pallet in self.pallets for item in pallet.items]
        placed_by_id = {item.unit_id: item for item in placed}
        ids = [item.unit_id for item in placed] + [item.unit_id for item in self.contained_items]
        all_ids = ids + [item.unit_id for item in self.unpacked]
        errors = []
        if len(set(all_ids)) != len(all_ids):
            errors.append("A shipping unit is accounted for more than once.")
        if self.expected_unit_ids and Counter(all_ids) != Counter(self.expected_unit_ids):
            errors.append("Shipping-unit identities do not match the input revision.")
        if (self.summary.total_items != len(all_ids) or self.summary.packed_items != len(ids)
                or self.summary.unpacked_items != len(self.unpacked)):
            errors.append("Shipping-unit totals do not reconcile with placed and contained items.")
        hosts = Counter(item.host_unit_id for item in self.contained_items)
        for item in self.contained_items:
            host = placed_by_id.get(item.host_unit_id)
            if (not host or not _is_b18(host) or normalize_sku(item.sku) != "TRASHKIT"
                    or host.packing_state != PackingState.ASSEMBLED
                    or item.packing_state != PackingState.ASSEMBLED or hosts[item.host_unit_id] != 1):
                errors.append(f"{item.unit_id}: assembled trash kit requires one eligible B18 host.")
            elif abs(host.weight - item.host_base_weight - item.weight) > 0.011:
                errors.append(f"{item.unit_id}: contained weight is not included exactly once.")
        for pallet in self.pallets:
            if (abs(sum(item.weight for item in pallet.items) - pallet.item_weight) > 0.02
                    or abs(pallet.item_weight + pallet.tare_weight - pallet.total_weight) > 0.02):
                errors.append(f"Pallet {pallet.pallet_number}: weight does not reconcile.")
        if abs(sum(p.item_weight for p in self.pallets) - self.summary.packed_item_weight) > 0.02:
            errors.append("Packed weight does not reconcile.")
        if abs(sum(p.item_weight for p in self.pallets) + sum(i.weight for i in self.unpacked)
               - self.summary.total_item_weight) > 0.02:
            errors.append("Total item weight does not reconcile.")
        if abs(sum(p.total_weight for p in self.pallets) - self.summary.total_freight_weight) > 0.02:
            errors.append("Freight weight does not reconcile.")
        if (self.summary.total_pallets != len(self.pallets)
                or self.summary.pallet_types != dict(Counter(p.pallet_type for p in self.pallets))):
            errors.append("Pallet totals do not reconcile.")
        return list(dict.fromkeys(errors))

    @computed_field
    @property
    def freight_ready(self) -> bool:
        return (self.plan_status == "ready" and not self.unpacked and not self.accounting_errors
                and bool(self.pallets) and not any(w.severity == Severity.ERROR for w in self.warnings))

    @computed_field
    @property
    def quote_rows(self) -> list[dict]:
        from .services.broker_handoff import quote_rows

        return quote_rows(self)

    @model_validator(mode="before")
    @classmethod
    def block_legacy_incomplete_plans(cls, value):
        """Older saved results did not persist plan_status.

        Treat any such result with unpacked freight as blocked so reopening an old
        shipment cannot accidentally re-enable exports or broker handoff.
        """
        if isinstance(value, dict) and "plan_status" not in value and value.get("unpacked"):
            value = {**value, "plan_status": "blocked"}
        return value
