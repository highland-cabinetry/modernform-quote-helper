"""Shared packing data structures and result assembly.

Everything here is engine-neutral: unit expansion (quantities, bundles, PC/OC
component pairs, contained trash kits), placement records, and conversion of a
finished arrangement into the persisted ``PackingResult`` contract.  The
independent ``RulesValidator`` is always run on the final arrangement.
"""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha1
from math import inf

from ..models import (
    CombinedItem,
    ConsolidationAttempt,
    ContainedItem,
    ItemKind,
    LayerMetric,
    OverrideOutcome,
    PackingResult,
    PackingRole,
    PackingSettings,
    PackingState,
    PalletPlan,
    PalletSpec,
    PlacedItem,
    Severity,
    ShipmentSummary,
    UnpackedItem,
    ValidationIssue,
)
from ..services.freight_class import density_freight_class
from ..services.product_exceptions import CSB42_RULE, effective_dimensions, is_csb42
from .validator import EPSILON, rectangle_union_area

CATEGORY_COLORS = {
    "cabinetry": "#2F6F73",
    "wall cabinet": "#2F6F73",
    "base cabinet": "#315B7D",
    "tall cabinet": "#24455F",
    "panel": "#9B6A3C",
    "molding": "#B5844F",
    "accessories": "#D09B3C",
    "hardware": "#7C6F64",
    "fragile": "#C95F52",
    "manual / unclassified": "#8B6F9C",
}
FALLBACK_COLORS = ["#2F6F73", "#315B7D", "#B5844F", "#8B6F9C", "#C7764A", "#667A52"]

@dataclass(slots=True)
class Unit:
    unit_id: str
    item_key: str
    sku: str
    original_sku: str
    packing_state: PackingState
    description: str
    category: str
    item_kind: ItemKind
    source_refs: list[str]
    length: float
    width: float
    height: float
    weight: float
    fragile: bool
    stackable: bool
    must_stay_upright: bool
    no_upside_down: bool
    allowed_rotations: list[str]
    max_stack_layers: int | None
    max_weight_on_top: float
    load_bearing_strength: float
    preferred_pallet_type: str
    color: str
    flatpack_dimensions: tuple[float, float, float] | None
    assembled_dimensions: tuple[float, float, float] | None
    packing_role: PackingRole
    rules_reviewed: bool
    bundle_max_units: int
    bundle_approved: bool
    allow_edge_standing: bool
    overhang_approved: bool
    max_overhang_per_side: float
    bundle_id: str | None = None
    bundle_position: int | None = None
    bundle_size: int | None = None
    pair_group_id: str | None = None
    paired_family: str | None = None
    paired_unit_ids: list[str] = field(default_factory=list)
    pair_reserved_height: float = 0.0
    contained_items: list[ContainedItem] = field(default_factory=list)

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height

    @property
    def footprint(self) -> float:
        return self.length * self.width


@dataclass
class WorkPallet:
    spec: PalletSpec
    number: int
    items: list[PlacedItem] = field(default_factory=list)
    load_boards: list[PlacedItem] = field(default_factory=list)

    @property
    def item_weight(self) -> float:
        return sum(item.weight for item in self.items)


@dataclass
class CandidatePlan:
    label: str
    pallets: list[WorkPallet]
    unpacked: list[UnpackedItem]
    score: float = inf
    consolidation_attempts: list[ConsolidationAttempt] = field(default_factory=list)
    override_outcomes: list[OverrideOutcome] = field(default_factory=list)


PAIR_SKU_PATTERN = re.compile(r"^((?:PC|OC)[A-Z0-9./-]*?)(BOX|FFD)$", re.IGNORECASE)


def _pair_source_scope(source_ref: str) -> str:
    return re.split(r"\s+-\s+page\b|\bpage\s+\d+|\.pdf\b", source_ref, maxsplit=1, flags=re.IGNORECASE)[0].strip()


def _color_prefix(original_sku: str) -> str:
    match = re.match(r"^([A-Z0-9]+)-", original_sku.strip().upper())
    return match.group(1) if match else ""


def _compatible_ffd_stack(lower: PlacedItem, upper: PlacedItem) -> bool:
    return (
        lower.sku.upper().endswith("FFD")
        and upper.sku.upper().endswith("FFD")
        and lower.paired_family == upper.paired_family
        and _color_prefix(lower.original_sku) == _color_prefix(upper.original_sku)
        and _pair_source_scope(lower.source_refs[0]) == _pair_source_scope(upper.source_refs[0])
    )


def _assign_component_pairs(units: list[Unit]) -> list[ValidationIssue]:
    grouped: dict[tuple[str, str, str, PackingState], dict[str, list[Unit]]] = {}
    for unit in units:
        match = PAIR_SKU_PATTERN.match(unit.sku.replace(" ", ""))
        if not match:
            continue
        family, component = match.group(1).upper(), match.group(2).upper()
        key = (_pair_source_scope(unit.source_refs[0]), _color_prefix(unit.original_sku), family, unit.packing_state)
        grouped.setdefault(key, {"BOX": [], "FFD": []})[component].append(unit)

    warnings: list[ValidationIssue] = []
    for (source, color, family, state), components in grouped.items():
        boxes = sorted(components["BOX"], key=lambda unit: unit.unit_id)
        ffds = sorted(components["FFD"], key=lambda unit: unit.unit_id)
        paired_count = min(len(boxes), len(ffds))
        for index in range(paired_count):
            box, ffd = boxes[index], ffds[index]
            group_id = sha1(f"{source}|{color}|{family}|{state.value}|{index}".encode()).hexdigest()[:14]
            for member, counterpart in ((box, ffd), (ffd, box)):
                member.pair_group_id = f"pair-{group_id}"
                member.paired_family = family
                member.paired_unit_ids = [counterpart.unit_id]
            box.pair_reserved_height = ffd.height
        if len(boxes) != len(ffds):
            surplus = "BOX" if len(boxes) > len(ffds) else "FFD"
            surplus_units = boxes[paired_count:] if surplus == "BOX" else ffds[paired_count:]
            warnings.append(ValidationIssue(
                code="PAIR_QUANTITY_SURPLUS",
                severity=Severity.WARNING,
                message=(f"{family} has {len(surplus_units)} surplus {surplus} unit(s) for {source or 'this source'}; "
                         "only the available matching quantities were paired."),
                unit_ids=[unit.unit_id for unit in surplus_units],
            ))
    return warnings


def _color_for(category: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    lowered = category.lower()
    for key, color in CATEGORY_COLORS.items():
        if key in lowered:
            return color
    digest = int(sha1(lowered.encode("utf-8")).hexdigest()[:8], 16)
    return FALLBACK_COLORS[digest % len(FALLBACK_COLORS)]


def _orientation(unit: Unit, rotation: str) -> tuple[float, float, float]:
    dimensions = {"L": unit.length, "W": unit.width, "H": unit.height}
    return dimensions[rotation[0]], dimensions[rotation[1]], dimensions[rotation[2]]


def _is_csb42_assembled(value) -> bool:
    """Return whether *value* is the exact approved assembled CSB42 carton."""

    return is_csb42(value) and getattr(value, "packing_state", None) == PackingState.ASSEMBLED


def _dimensions_for_expansion(master, state: PackingState) -> tuple[float, float, float] | None:
    return effective_dimensions(master, state)


def _is_csb42_deck(spec: PalletSpec) -> bool:
    return (
        spec.name == "standard"
        and abs(spec.length - CSB42_RULE.standard_deck[0]) <= EPSILON
        and abs(spec.width - CSB42_RULE.standard_deck[1]) <= EPSILON
    )


def _largest_empty_rectangle_area(
    rectangles: list[tuple[float, float, float, float]],
    length: float,
    width: float,
) -> float:
    """Return the largest axis-aligned empty deck rectangle for one layer.

    Rectangle edges are sufficient candidate boundaries for an exact maximum
    with axis-aligned cartons.  Layer sizes are small, so the intentionally
    explicit search is both deterministic and inexpensive.
    """

    clipped = [
        (max(0.0, x0), max(0.0, y0), min(length, x1), min(width, y1))
        for x0, y0, x1, y1 in rectangles
        if x1 > 0 and y1 > 0 and x0 < length and y0 < width
    ]
    xs = sorted({0.0, length, *[value for rect in clipped for value in (rect[0], rect[2])]})
    best = 0.0
    for left_index, left in enumerate(xs[:-1]):
        for right in xs[left_index + 1 :]:
            if right - left <= EPSILON:
                continue
            blocked = sorted(
                (bottom, top)
                for x0, bottom, x1, top in clipped
                if x0 < right - EPSILON and x1 > left + EPSILON
            )
            cursor = 0.0
            largest_gap = 0.0
            for bottom, top in blocked:
                largest_gap = max(largest_gap, max(0.0, bottom - cursor))
                cursor = max(cursor, top)
            largest_gap = max(largest_gap, max(0.0, width - cursor))
            best = max(best, (right - left) * largest_gap)
    return best


def _expand_units(
    items: list[CombinedItem],
    split_bundle_ids: set[str] | None = None,
) -> tuple[list[Unit], list[UnpackedItem]]:
    split_bundle_ids = split_bundle_ids or set()
    units: list[Unit] = []
    unavailable: list[UnpackedItem] = []
    for combined in items:
        source_sequence: list[tuple[str, str]] = []
        for source in combined.sources:
            source_sequence.extend([(source.source_ref, source.original_sku)] * source.quantity)
        if len(source_sequence) < combined.quantity:
            source_sequence.extend(
                [(combined.sources[0].source_ref, combined.original_skus[0])]
                * (combined.quantity - len(source_sequence))
            )
        master = combined.master_item
        dimensions = _dimensions_for_expansion(master, combined.packing_state)
        bundle_limit = min(master.bundle_max_units, 5) if master.bundle_approved else 1
        bundle_group_totals = Counter((ref, sku.upper()) for ref, sku in source_sequence)
        bundle_group_seen: Counter[tuple[str, str]] = Counter()
        for index in range(combined.quantity):
            source_ref, original_sku = source_sequence[index]
            unit_id = f"{combined.key}-{index + 1:04d}"
            if dimensions is None:
                state_label = combined.packing_state.value
                unavailable.append(
                    UnpackedItem(
                        unit_id=unit_id,
                        sku=master.sku,
                        original_sku=original_sku,
                        packing_state=combined.packing_state,
                        description=master.description,
                        source_refs=[source_ref],
                        weight=master.weight,
                        dimensions=(0, 0, 0),
                        reason_code=f"MISSING_{state_label.upper()}_DIMENSIONS",
                        reason=f"Item master has no complete {state_label} length, width, and height set.",
                    )
                )
                continue
            bundle_group_key = (source_ref, original_sku.upper())
            group_position = bundle_group_seen[bundle_group_key]
            bundle_group_seen[bundle_group_key] += 1
            bundle_index = group_position // bundle_limit if bundle_limit > 1 else None
            bundle_scope = sha1(
                f"{source_ref}|{original_sku.upper()}|{combined.packing_state.value}".encode()
            ).hexdigest()[:10]
            candidate_bundle_id = (
                f"{combined.key}-{bundle_scope}-bundle-{bundle_index + 1:03d}" if bundle_index is not None else None
            )
            if candidate_bundle_id in split_bundle_ids:
                bundle_index = None
                candidate_bundle_id = None
            bundle_size = (
                min(bundle_limit, bundle_group_totals[bundle_group_key] - bundle_index * bundle_limit)
                if bundle_index is not None
                else None
            )
            units.append(
                Unit(
                    unit_id=unit_id,
                    item_key=combined.key,
                    sku=master.sku,
                    original_sku=original_sku,
                    packing_state=combined.packing_state,
                    description=master.description,
                    category=master.category,
                    item_kind=master.item_kind,
                    source_refs=[source_ref],
                    length=dimensions[0],
                    width=dimensions[1],
                    height=dimensions[2],
                    weight=master.weight,
                    fragile=master.fragile,
                    stackable=master.stackable,
                    must_stay_upright=master.must_stay_upright,
                    no_upside_down=master.no_upside_down,
                    allowed_rotations=master.allowed_rotations,
                    max_stack_layers=master.max_stack_layers,
                    max_weight_on_top=master.max_weight_on_top,
                    load_bearing_strength=master.load_bearing_strength,
                    preferred_pallet_type=master.preferred_pallet_type,
                    color=_color_for(master.category, master.color),
                    flatpack_dimensions=_dimensions_for_expansion(master, PackingState.FLATPACK),
                    assembled_dimensions=_dimensions_for_expansion(master, PackingState.ASSEMBLED),
                    packing_role=master.packing_role,
                    rules_reviewed=master.rules_reviewed,
                    bundle_max_units=master.bundle_max_units,
                    bundle_approved=master.bundle_approved,
                    allow_edge_standing=master.allow_edge_standing,
                    overhang_approved=master.overhang_approved,
                    max_overhang_per_side=master.max_overhang_per_side,
                    bundle_id=candidate_bundle_id,
                    bundle_position=(group_position % bundle_limit + 1 if bundle_index is not None else None),
                    bundle_size=bundle_size,
                )
            )
    # Accounting may turn an authored physical unit into a contained item while
    # retaining the host Unit.  Keep this as the final expansion step so every
    # engine strategy sees the same physical-unit list and unavailable reasons.
    from ..services.item_accounting import prepare_units

    physical_units, accounting_unavailable = prepare_units(units)
    unavailable.extend(accounting_unavailable)
    return physical_units, unavailable



class PlanResultBuilder:
    """Mixin: turns WorkPallets into the persisted PackingResult contract."""

    validator = None

    @staticmethod
    def _make_placement(
        unit: Unit,
        point: tuple[float, float, float],
        dimensions: tuple[float, float, float],
        rotation: str,
        *,
        locked: bool = False,
        manually_placed: bool = False,
    ) -> PlacedItem:
        return PlacedItem(
            unit_id=unit.unit_id,
            item_key=unit.item_key,
            sku=unit.sku,
            original_sku=unit.original_sku,
            packing_state=unit.packing_state,
            description=unit.description,
            category=unit.category,
            item_kind=unit.item_kind,
            source_refs=unit.source_refs,
            x=point[0],
            y=point[1],
            z=point[2],
            length=dimensions[0],
            width=dimensions[1],
            height=dimensions[2],
            flatpack_dimensions=unit.flatpack_dimensions,
            assembled_dimensions=unit.assembled_dimensions,
            rotation=rotation,
            weight=unit.weight,
            fragile=unit.fragile,
            stackable=unit.stackable,
            must_stay_upright=unit.must_stay_upright,
            allowed_rotations=unit.allowed_rotations,
            max_stack_layers=unit.max_stack_layers,
            max_weight_on_top=unit.max_weight_on_top,
            load_bearing_strength=unit.load_bearing_strength,
            color=unit.color,
            locked=locked,
            manually_placed=manually_placed,
            packing_role=unit.packing_role,
            rules_reviewed=unit.rules_reviewed,
            allow_edge_standing=unit.allow_edge_standing,
            overhang_approved=unit.overhang_approved,
            max_overhang_per_side=unit.max_overhang_per_side,
            bundle_id=unit.bundle_id,
            bundle_position=unit.bundle_position,
            bundle_size=unit.bundle_size,
            bundle_approved=unit.bundle_approved,
            pair_group_id=unit.pair_group_id,
            paired_family=unit.paired_family,
            paired_unit_ids=unit.paired_unit_ids,
        )


    @staticmethod
    def _unpacked(unit: Unit, code: str, reason: str) -> UnpackedItem:
        return UnpackedItem(
            unit_id=unit.unit_id,
            sku=unit.sku,
            original_sku=unit.original_sku,
            packing_state=unit.packing_state,
            description=unit.description,
            source_refs=unit.source_refs,
            weight=unit.weight,
            dimensions=(unit.length, unit.width, unit.height),
            reason_code=code,
            reason=reason,
        )


    @staticmethod
    def _is_accessory_dominant(items: list[PlacedItem]) -> bool:
        """Allow incidental cartons on a pallet that is overwhelmingly accessories."""
        if not items:
            return False
        accessory_count = sum(
            item.item_kind == ItemKind.ACCESSORY or item.packing_role != PackingRole.STANDARD
            for item in items
        )
        return accessory_count / len(items) >= 0.8

    def _pallet_plans(self, pallets: list[WorkPallet], settings: PackingSettings) -> list[PalletPlan]:
        plans: list[PalletPlan] = []
        for number, pallet in enumerate(pallets, start=1):
            pallet.number = number
            item_weight = sum(item.weight for item in pallet.items)
            boards = list(pallet.load_boards)
            tare_weight = pallet.spec.tare_weight + sum(board.weight for board in boards)
            stack_height = max((item.z + item.height for item in [*pallet.items, *boards]), default=0)
            volume = sum(item.length * item.width * item.height for item in pallet.items)
            capacity = pallet.spec.length * pallet.spec.width * max(pallet.spec.cargo_height, EPSILON)
            total_weight = item_weight + tare_weight
            min_item_x = min((item.x for item in pallet.items), default=0.0)
            min_item_y = min((item.y for item in pallet.items), default=0.0)
            max_item_x = max((item.x + item.length for item in pallet.items), default=pallet.spec.length)
            max_item_y = max((item.y + item.width for item in pallet.items), default=pallet.spec.width)
            freight_min_x = min(0.0, min_item_x)
            freight_min_y = min(0.0, min_item_y)
            freight_max_x = max(pallet.spec.length, max_item_x)
            freight_max_y = max(pallet.spec.width, max_item_y)
            freight_length = freight_max_x - freight_min_x
            freight_width = freight_max_y - freight_min_y
            freight_height = stack_height + pallet.spec.deck_height
            cubic_feet = freight_length * freight_width * freight_height / 1728
            density_pcf = total_weight / cubic_feet if cubic_feet > EPSILON else 0.0
            layer_edges = sorted(
                {
                    round(value, 3)
                    for item in pallet.items
                    for value in (item.z, item.z + item.height)
                }
            )
            layer_metrics: list[LayerMetric] = []
            cabinet_voids: list[float] = []
            weighted_coverage = 0.0
            measured_height = 0.0
            for layer_z, layer_top in zip(layer_edges, layer_edges[1:], strict=False):
                layer_depth = layer_top - layer_z
                if layer_depth <= EPSILON:
                    continue
                midpoint = layer_z + layer_depth / 2
                layer_items = [
                    item
                    for item in pallet.items
                    if item.z <= midpoint + EPSILON
                    and item.z + item.height >= midpoint - EPSILON
                ]
                rectangles = [
                    (item.x, item.y, item.x + item.length, item.y + item.width)
                    for item in layer_items
                ]
                if rectangles:
                    deck_area = pallet.spec.length * pallet.spec.width
                    union = min(deck_area, rectangle_union_area(rectangles))
                    coverage = min(1.0, union / deck_area)
                    largest_void = min(
                        1.0,
                        _largest_empty_rectangle_area(
                            rectangles,
                            pallet.spec.length,
                            pallet.spec.width,
                        )
                        / deck_area,
                    )
                    min_x = min(rectangle[0] for rectangle in rectangles)
                    min_y = min(rectangle[1] for rectangle in rectangles)
                    max_x = max(rectangle[2] for rectangle in rectangles)
                    max_y = max(rectangle[3] for rectangle in rectangles)
                    fragmentation = max(0.0, (max_x - min_x) * (max_y - min_y) - union) / deck_area
                    weighted_coverage += coverage * layer_depth
                    measured_height += layer_depth
                    layer_metrics.append(
                        LayerMetric(
                            z=round(layer_z, 3),
                            item_count=len(layer_items),
                            coverage_percent=round(coverage * 100, 1),
                            largest_void_percent=round(largest_void * 100, 1),
                            fragmentation_percent=round(fragmentation * 100, 1),
                            cabinet_count=sum(
                                item.item_kind in {ItemKind.CABINET, ItemKind.CABINET_COMPONENT}
                                for item in layer_items
                            ),
                            target_cabinet_count=(
                                4
                                if sum(
                                    item.item_kind in {ItemKind.CABINET, ItemKind.CABINET_COMPONENT}
                                    for item in layer_items
                                ) >= 4
                                else 3
                            ),
                            close_reason=(
                                "target met"
                                if sum(
                                    item.item_kind in {ItemKind.CABINET, ItemKind.CABINET_COMPONENT}
                                    for item in layer_items
                                ) >= 3
                                else (
                                    "full-footprint or no additional safe cabinet fit"
                                    if any(
                                        item.item_kind == ItemKind.CABINET
                                        and item.length * item.width >= deck_area * 0.75
                                        for item in layer_items
                                    )
                                    else "no additional safe cabinet fit"
                                )
                            ),
                        )
                    )
                    if any(
                        item.item_kind in {ItemKind.CABINET, ItemKind.CABINET_COMPONENT}
                        for item in layer_items
                    ) and layer_depth > settings.support_height_tolerance + EPSILON:
                        cabinet_voids.append(largest_void)
            layer_coverage = weighted_coverage / measured_height if measured_height else 0.0
            support_coverages = []
            for top_z in {round(item.z + item.height, 3) for item in pallet.items}:
                surface_rectangles = [
                    (item.x, item.y, item.x + item.length, item.y + item.width)
                    for item in pallet.items
                    if 0 <= top_z - (item.z + item.height) <= settings.support_height_tolerance + EPSILON
                ]
                if surface_rectangles:
                    support_coverages.append(
                        min(
                            1.0,
                            rectangle_union_area(surface_rectangles)
                            / (pallet.spec.length * pallet.spec.width),
                        )
                    )
            top_support_coverage = max(support_coverages, default=0.0)
            void_values = cabinet_voids or [
                metric.largest_void_percent / 100 for metric in layer_metrics
            ]
            largest_void = max(void_values, default=0.0)
            overhang = {
                "left": max(0.0, -min_item_x),
                "right": max(0.0, max_item_x - pallet.spec.length),
                "front": max(0.0, -min_item_y),
                "back": max(0.0, max_item_y - pallet.spec.width),
            }
            required_actions = []
            if settings.require_dunnage_for_height_gap and any(item.requires_dunnage for item in pallet.items):
                required_actions.append(
                    "Install and verify dunnage at every marked support gap or unsupported base-panel area."
                )
            if boards:
                required_actions.append(
                    f"Lay {len(boards)} load board{'s' if len(boards) > 1 else ''} "
                    f"({boards[0].length:g} × {boards[0].width:g} × {boards[0].height:g} in) where shown: "
                    + ", ".join(f"{board.z:.1f} in above the deck" for board in boards) + "."
                )
            if any(value > EPSILON for value in overhang.values()):
                required_actions.append("Protect approved overhang and verify load restraint before release.")
            if item_weight:
                cog = (
                    sum((item.x + item.length / 2) * item.weight for item in pallet.items) / item_weight,
                    sum((item.y + item.width / 2) * item.weight for item in pallet.items) / item_weight,
                    sum((item.z + item.height / 2) * item.weight for item in pallet.items) / item_weight,
                )
            else:
                cog = (pallet.spec.length / 2, pallet.spec.width / 2, 0)
            plans.append(
                PalletPlan(
                    pallet_number=number,
                    pallet_type=pallet.spec.name,
                    display_name=pallet.spec.display_name,
                    length=pallet.spec.length,
                    width=pallet.spec.width,
                    max_total_height=pallet.spec.max_total_height,
                    deck_height=pallet.spec.deck_height,
                    max_cargo_height=pallet.spec.cargo_height,
                    stack_height=stack_height,
                    freight_height=freight_height,
                    item_weight=item_weight,
                    tare_weight=tare_weight,
                    total_weight=total_weight,
                    max_total_weight=pallet.spec.max_total_weight,
                    used_volume_percent=round(min(100, volume / capacity * 100), 1),
                    items=pallet.items,
                    item_quantities=dict(
                        Counter(f"{item.original_sku} ({item.packing_state.value})" for item in pallet.items)
                    ),
                    center_of_gravity=tuple(round(value, 2) for value in cog),
                    load_min_x=freight_min_x,
                    load_min_y=freight_min_y,
                    load_max_x=freight_max_x,
                    load_max_y=freight_max_y,
                    load_length=freight_length,
                    load_width=freight_width,
                    overhang={key: round(value, 3) for key, value in overhang.items()},
                    layer_coverage_percent=round(layer_coverage * 100, 1),
                    largest_void_percent=round(largest_void * 100, 1),
                    top_support_coverage_percent=round(top_support_coverage * 100, 1),
                    layer_metrics=layer_metrics,
                    density_pcf=round(density_pcf, 2),
                    estimated_freight_class=density_freight_class(density_pcf),
                    required_actions=required_actions,
                    load_boards=boards,
                )
            )
        return plans

    def _to_result(
        self,
        shipment_name: str,
        units: list[Unit],
        best: CandidatePlan,
        candidates: list[CandidatePlan],
        settings: PackingSettings,
    ) -> PackingResult:
        pallets = self._pallet_plans(best.pallets, settings)
        issues = self.validator.validate(pallets, settings)
        issues.extend(deepcopy(getattr(self, "_pairing_warnings", [])))
        units_by_id = {unit.unit_id: unit for unit in units}
        for unpacked in best.unpacked:
            unit = units_by_id.get(unpacked.unit_id)
            if unit and unit.item_kind in {ItemKind.CABINET, ItemKind.CABINET_COMPONENT}:
                issues.append(
                    ValidationIssue(
                        code="CABINET_PACKING_BLOCKED",
                        severity=Severity.ERROR,
                        message=(
                            f"{unpacked.original_sku} is a matched cabinet carton and was not packed: "
                            f"{unpacked.reason}"
                        ),
                        unit_ids=[unpacked.unit_id],
                    )
                )
        if best.consolidation_attempts and not any(
            attempt.status == "success" for attempt in best.consolidation_attempts
        ):
            seen_blockers: set[tuple[str, str]] = set()
            for attempt in best.consolidation_attempts:
                signature = (attempt.blocker_code, attempt.message)
                if attempt.status != "blocked" or signature in seen_blockers:
                    continue
                seen_blockers.add(signature)
                issues.append(
                    ValidationIssue(
                        code="CONSOLIDATION_BLOCKED",
                        severity=Severity.WARNING,
                        message=f"{attempt.blocker_code}: {attempt.message}",
                    )
                )
        for pallet in pallets:
            pallet.warnings = [issue for issue in issues if issue.pallet_number == pallet.pallet_number]
            if self._is_accessory_dominant(pallet.items):
                if pallet.freight_height + EPSILON < settings.accessory_only_min_freight_height:
                    warning = ValidationIssue(
                        code="ACCESSORY_PALLET_BELOW_THRESHOLD",
                        severity=Severity.WARNING,
                        message=(
                            f"Accessory-dominant pallet is {pallet.freight_height:g} in high, below the configured "
                            f"{settings.accessory_only_min_freight_height:g} in freight-height preference; "
                            "no validated consolidation position was available."
                        ),
                        pallet_number=pallet.pallet_number,
                    )
                    pallet.warnings.append(warning)
                    issues.append(warning)
            unreviewed_base = [
                item for item in pallet.items if item.packing_role == PackingRole.BASE_PANEL and not item.rules_reviewed
            ]
            if unreviewed_base:
                warning = ValidationIssue(
                    code="BASE_PANEL_REVIEW_REQUIRED",
                    severity=Severity.WARNING,
                    message="Base-panel handling is not approved; these panels were not used as load-bearing supports.",
                    pallet_number=pallet.pallet_number,
                    unit_ids=[item.unit_id for item in unreviewed_base],
                )
                pallet.warnings.append(warning)
                issues.append(warning)
            half_length = pallet.length / 2
            half_width = pallet.width / 2
            cog_x, cog_y, _ = pallet.center_of_gravity
            # Small, stable offsets are normal in mixed cabinet loads. Keep this
            # advisory for material imbalance without letting it compete with
            # safe pallet-count reduction.
            if abs(cog_x - half_length) > pallet.length * 0.25 or abs(cog_y - half_width) > pallet.width * 0.25:
                warning = ValidationIssue(
                    code="OFF_CENTER_LOAD",
                    severity=Severity.WARNING,
                    message=(
                        "Load center of gravity is notably off center; warehouse review and restraint are required."
                    ),
                    pallet_number=pallet.pallet_number,
                )
                pallet.warnings.append(warning)
                issues.append(warning)

        placed_ids = {item.unit_id for pallet in pallets for item in pallet.items}
        contained_items = [
            item.model_copy(deep=True) for unit in units if unit.unit_id in placed_ids
            for item in unit.contained_items
        ]
        unpacked_items = deepcopy(best.unpacked)
        for host in units:
            if host.unit_id in placed_ids or not host.contained_items:
                continue
            exception = next((item for item in unpacked_items if item.unit_id == host.unit_id), None)
            if exception is not None:
                exception.weight = host.contained_items[0].host_base_weight
            for item in host.contained_items:
                unpacked_items.append(UnpackedItem(
                    unit_id=item.unit_id, sku=item.sku, original_sku=item.original_sku,
                    packing_state=item.packing_state, description="Contained trash kit",
                    source_refs=item.source_refs, weight=item.weight, dimensions=(0, 0, 0),
                    reason_code="TRASH_KIT_HOST_UNPACKED",
                    reason="The assigned B18 host was not packed; resolve the host's packing blocker.",
                ))
        packed_count = sum(len(pallet.items) for pallet in pallets) + len(contained_items)
        packed_item_weight = sum(pallet.item_weight for pallet in pallets)
        total_item_weight = packed_item_weight + sum(item.weight for item in unpacked_items)
        summary = ShipmentSummary(
            total_items=packed_count + len(unpacked_items),
            packed_items=packed_count,
            unpacked_items=len(unpacked_items),
            total_item_weight=round(total_item_weight, 2),
            packed_item_weight=round(packed_item_weight, 2),
            total_freight_weight=round(sum(pallet.total_weight for pallet in pallets), 2),
            total_pallets=len(pallets),
            pallet_types=dict(Counter(pallet.pallet_type for pallet in pallets)),
        )
        scores = [
            {
                "strategy": candidate.label,
                "score": round(candidate.score, 2),
                "pallets": float(len(candidate.pallets)),
                "unpacked": float(len(candidate.unpacked)),
            }
            for candidate in candidates
        ]
        placed_by_id = {
            item.unit_id: item
            for pallet in pallets
            for item in pallet.items
        }
        override_outcomes = deepcopy(best.override_outcomes)
        accepted_exact = {
            outcome.unit_id
            for outcome in override_outcomes
            if outcome.kind == "exact_lock" and outcome.status == "accepted"
        }
        for outcome in override_outcomes:
            placed = placed_by_id.get(outcome.unit_id)
            if placed and outcome.status == "fallback":
                outcome.applied_rotation = placed.rotation
        for unit_id, requested in getattr(self, "_rotation_preferences", {}).items():
            if unit_id in accepted_exact:
                continue
            unit = next((candidate for candidate in units if candidate.unit_id == unit_id), None)
            placed = placed_by_id.get(unit_id)
            if unit is None or requested not in unit.allowed_rotations:
                override_outcomes.append(
                    OverrideOutcome(
                        unit_id=unit_id,
                        kind="rotation_preference",
                        status="rejected",
                        requested_rotation=requested,
                        applied_rotation=placed.rotation if placed else None,
                        message="Requested rotation is unavailable for this carton.",
                    )
                )
            elif placed and placed.rotation == requested:
                override_outcomes.append(
                    OverrideOutcome(
                        unit_id=unit_id,
                        kind="rotation_preference",
                        status="accepted",
                        requested_rotation=requested,
                        applied_rotation=placed.rotation,
                        message="Preferred rotation was applied during automatic repacking.",
                    )
                )
            else:
                override_outcomes.append(
                    OverrideOutcome(
                        unit_id=unit_id,
                        kind="rotation_preference",
                        status="fallback",
                        requested_rotation=requested,
                        applied_rotation=placed.rotation if placed else None,
                        message=(
                            "Preferred rotation was not safe in the selected plan; "
                            "carton remained automatically packed."
                        ),
                    )
                )

        for outcome in override_outcomes:
            if outcome.status in {"fallback", "rejected"}:
                issues.append(
                    ValidationIssue(
                        code="MANUAL_OVERRIDE_REJECTED",
                        severity=Severity.WARNING,
                        message=outcome.message,
                        unit_ids=[outcome.unit_id],
                    )
                )
        blocking = bool(unpacked_items or any(issue.severity == Severity.ERROR for issue in issues))
        return PackingResult(
            shipment_name=shipment_name,
            engine=self.name,
            engine_version=self.version,
            plan_status="blocked" if blocking else "ready",
            summary=summary,
            pallets=pallets,
            unpacked=unpacked_items,
            contained_items=contained_items,
            expected_unit_ids=getattr(self, "_expected_unit_ids", []),
            warnings=issues,
            candidate_scores=scores,
            consolidation_attempts=best.consolidation_attempts,
            override_outcomes=override_outcomes,
            human_review_required=bool(best.unpacked or issues),
        )


EDGE_ROTATIONS = ["LHW", "HLW", "WHL", "HWL"]


def apply_edge_permission(units: list[Unit], settings: PackingSettings) -> None:
    """Let flatpack cabinet cartons stand on edge when the setting allows it.

    Applied identically by the engine and the job worker so that expected
    rotations and validator flags agree. Fragile door/face-frame cartons,
    accessories and assembled cabinets are never stood on edge.
    """
    if not getattr(settings, "flatpack_cartons_on_edge", False):
        return
    for unit in units:
        if (unit.packing_state == PackingState.FLATPACK and unit.item_kind == ItemKind.CABINET
                and not unit.fragile and unit.stackable):
            unit.allow_edge_standing = True
            unit.must_stay_upright = False
            unit.allowed_rotations = list(dict.fromkeys([*unit.allowed_rotations, *EDGE_ROTATIONS]))
