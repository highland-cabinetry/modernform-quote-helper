from __future__ import annotations

from collections import defaultdict

from ..models import (
    ItemKind,
    PackingRole,
    PackingSettings,
    PackingState,
    PalletPlan,
    Severity,
    SupportContact,
    ValidationIssue,
)
from ..services.product_exceptions import CSB42_RULE, is_csb42

EPSILON = 1e-6

def _is_csb42_assembled(item) -> bool:
    return is_csb42(item) and getattr(item, "packing_state", None) == PackingState.ASSEMBLED


def _is_csb42_deck(pallet: PalletPlan) -> bool:
    return (
        pallet.pallet_type == "standard"
        and abs(pallet.length - CSB42_RULE.standard_deck[0]) <= EPSILON
        and abs(pallet.width - CSB42_RULE.standard_deck[1]) <= EPSILON
    )


def _pair_scope(item) -> tuple[str, str, str]:
    original = item.original_sku.upper()
    prefix = original.split("-", 1)[0] if "-" in original else ""
    source = item.source_refs[0].lower().split(" - page", 1)[0].split(".pdf", 1)[0]
    return item.paired_family or "", prefix, source


def _compatible_ffd_stack(lower, upper) -> bool:
    """Face-frame/door cartons may be stacked on one another.

    Measured warehouse loads stack door cartons from different door styles and
    orders together (for example SO77223: 13 mixed-style FFD cartons on one
    48 x 40), so style, color prefix and source no longer restrict the stack.
    """
    return lower.sku.upper().endswith("FFD") and upper.sku.upper().endswith("FFD")


def overlap_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def overlap_area(a, b) -> float:
    return overlap_1d(a.x, a.x + a.length, b.x, b.x + b.length) * overlap_1d(
        a.y, a.y + a.width, b.y, b.y + b.width
    )


def collides(a, b) -> bool:
    return (
        overlap_1d(a.x, a.x + a.length, b.x, b.x + b.length) > EPSILON
        and overlap_1d(a.y, a.y + a.width, b.y, b.y + b.width) > EPSILON
        and overlap_1d(a.z, a.z + a.height, b.z, b.z + b.height) > EPSILON
    )


def rectangle_union_area(rectangles: list[tuple[float, float, float, float]]) -> float:
    """Exact area of axis-aligned rectangles; used to avoid double-counting support."""

    xs = sorted({value for rect in rectangles for value in (rect[0], rect[2])})
    area = 0.0
    for left, right in zip(xs, xs[1:], strict=False):
        if right - left <= EPSILON:
            continue
        intervals = sorted((bottom, top) for x0, bottom, x1, top in rectangles if x0 < right and x1 > left)
        covered = 0.0
        cursor_bottom = cursor_top = None
        for bottom, top in intervals:
            if cursor_bottom is None:
                cursor_bottom, cursor_top = bottom, top
            elif bottom > cursor_top + EPSILON:
                covered += cursor_top - cursor_bottom
                cursor_bottom, cursor_top = bottom, top
            else:
                cursor_top = max(cursor_top, top)
        if cursor_bottom is not None:
            covered += cursor_top - cursor_bottom
        area += (right - left) * covered
    return area


def support_contacts(item, items, tolerance: float = 0.0) -> tuple[float, list[SupportContact]]:
    if item.z <= EPSILON:
        return 1.0, []
    raw: list[tuple[object, float, float, tuple[float, float, float, float]]] = []
    for below in items:
        if below.unit_id == item.unit_id:
            continue
        gap = item.z - (below.z + below.height)
        if gap < -EPSILON or gap > tolerance + EPSILON:
            continue
        x0 = max(item.x, below.x)
        y0 = max(item.y, below.y)
        x1 = min(item.x + item.length, below.x + below.length)
        y1 = min(item.y + item.width, below.y + below.width)
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        if area > EPSILON:
            raw.append((below, area, max(0.0, gap), (x0, y0, x1, y1)))
    # A carton resting on a closer surface (e.g. a load board) does not also bear on
    # whatever lies under that surface within the height tolerance.
    raw.sort(key=lambda entry: entry[2])
    visible = []
    for entry in raw:
        x0, y0, x1, y1 = entry[3]
        closer = [
            (max(x0, r[0]), max(y0, r[1]), min(x1, r[2]), min(y1, r[3]))
            for other in visible
            if other[2] < entry[2] - EPSILON
            for r in [other[3]]
            if min(x1, r[2]) > max(x0, r[0]) and min(y1, r[3]) > max(y0, r[1])
        ]
        if closer and rectangle_union_area(closer) >= entry[1] - EPSILON:
            continue
        visible.append(entry)
    raw = visible
    union = rectangle_union_area([entry[3] for entry in raw])
    footprint = max(item.length * item.width, EPSILON)
    contacts = [
        SupportContact(
            supporting_unit_id=below.unit_id,
            contact_area=round(area, 4),
            contact_ratio=round(area / max(union, EPSILON), 6),
            height_gap=round(gap, 4),
            requires_dunnage=gap > EPSILON,
        )
        for below, area, gap, _ in raw
    ]
    return min(1.0, union / footprint), contacts


def support_ratio(item, items, tolerance: float = 0.0) -> float:
    return support_contacts(item, items, tolerance)[0]


def distributed_top_loads(
    items, tolerance: float, resolver=None
) -> tuple[dict[str, float], dict[tuple[str, str], float]]:
    """Propagate carried weight downward in proportion to direct contact area."""

    carried = {item.unit_id: 0.0 for item in items}
    transfers: dict[tuple[str, str], float] = {}
    for item in sorted(items, key=lambda value: (value.z + value.height, value.z), reverse=True):
        _, contacts = (resolver or support_contacts)(item, items, tolerance)
        total = item.weight + carried[item.unit_id]
        for contact in contacts:
            share = total * contact.contact_ratio
            carried[contact.supporting_unit_id] += share
            transfers[(item.unit_id, contact.supporting_unit_id)] = share
    return carried, transfers


class RulesValidator:
    """Independent post-pack validation; never trusts the placement heuristic."""

    def contacts_for(self, item, items, tolerance):
        return support_contacts(item, items, tolerance)

    def validate_pallet(self, pallet: PalletPlan, settings: PackingSettings) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        items = pallet.items
        boards = list(getattr(pallet, "load_boards", []) or [])
        # Load boards take part in geometry (support, collisions, load paths) but are
        # dunnage: they need no carton-style support and have no top-load limit.
        solids = [*items, *boards]
        carried, transfers = distributed_top_loads(solids, settings.support_height_tolerance, self.contacts_for)
        for board in boards:
            if (
                board.x < -settings.general_overhang_per_side - EPSILON
                or board.y < -settings.general_overhang_per_side - EPSILON
                or board.x + board.length > pallet.length + settings.general_overhang_per_side + EPSILON
                or board.y + board.width > pallet.width + settings.general_overhang_per_side + EPSILON
                or board.z + board.height > pallet.max_cargo_height + EPSILON
            ):
                issues.append(ValidationIssue(
                    code="OUT_OF_BOUNDS", severity=Severity.ERROR,
                    message="A load board exceeds the pallet bounds or configured height.",
                    pallet_number=pallet.pallet_number, unit_ids=[board.unit_id],
                ))
        unreviewed_top_load: list[str] = []
        edge_standing: list[str] = []
        for item in items:
            approved_overhang = max(
                settings.general_overhang_per_side,
                CSB42_RULE.overhang_per_side
                if _is_csb42_assembled(item) and _is_csb42_deck(pallet)
                else (
                    min(item.max_overhang_per_side, settings.approved_accessory_overhang_per_side)
                    if item.overhang_approved
                    or (item.rules_reviewed and item.packing_role != PackingRole.STANDARD)
                    else 0.0
                ),
            )
            if (
                item.x < -approved_overhang - EPSILON
                or item.y < -approved_overhang - EPSILON
                or item.z < -EPSILON
                or item.x + item.length > pallet.length + approved_overhang + EPSILON
                or item.y + item.width > pallet.width + approved_overhang + EPSILON
                or item.z + item.height > pallet.max_cargo_height + EPSILON
            ):
                issues.append(
                    ValidationIssue(
                        code="OUT_OF_BOUNDS",
                        severity=Severity.ERROR,
                        message=f"{item.original_sku} exceeds its approved pallet bounds or configured height.",
                        pallet_number=pallet.pallet_number,
                        unit_ids=[item.unit_id],
                    )
                )
            if _is_csb42_assembled(item):
                catalog_dimensions = item.assembled_dimensions
                expected_dimensions = (
                    (
                        CSB42_RULE.assembled_length,
                        CSB42_RULE.assembled_width,
                        float(catalog_dimensions[2]),
                    )
                    if catalog_dimensions is not None
                    else None
                )
                wrong_dimensions = expected_dimensions is None
                if expected_dimensions is not None:
                    wrong_dimensions = any(
                        abs(actual - expected) > EPSILON
                        for actual, expected in zip(
                            (item.length, item.width, item.height),
                            expected_dimensions,
                            strict=True,
                        )
                    )
                wrong_geometry = _is_csb42_deck(pallet) and any(
                    abs(actual - expected) > EPSILON
                    for actual, expected in zip(
                        (item.x, item.y, item.z),
                        (
                            (pallet.length - CSB42_RULE.assembled_length) / 2,
                            (pallet.width - CSB42_RULE.assembled_width) / 2,
                            0.0,
                        ),
                        strict=True,
                    )
                )
                if wrong_dimensions or wrong_geometry:
                    issues.append(
                        ValidationIssue(
                            code="CSB42_GEOMETRY_REQUIRED",
                            severity=Severity.ERROR,
                            message=(
                                "Assembled CSB42 must use its approved 42 × 42 footprint with the catalog "
                                "assembled height and be centered on a 48 × 40 standard deck."
                            ),
                            pallet_number=pallet.pallet_number,
                            unit_ids=[item.unit_id],
                        )
                    )
            if item.must_stay_upright and not item.rotation.endswith("H"):
                issues.append(
                    ValidationIssue(
                        code="UPRIGHT_ROTATION",
                        severity=Severity.ERROR,
                        message=f"{item.original_sku} was rotated onto its side.",
                        pallet_number=pallet.pallet_number,
                        unit_ids=[item.unit_id],
                    )
                )
            elif not item.rotation.endswith("H") and not item.allow_edge_standing:
                issues.append(
                    ValidationIssue(
                        code="EDGE_STANDING_NOT_APPROVED",
                        severity=Severity.ERROR,
                        message=f"{item.original_sku} is standing on an unapproved edge.",
                        pallet_number=pallet.pallet_number,
                        unit_ids=[item.unit_id],
                    )
                )
            elif not item.rotation.endswith("H"):
                edge_standing.append(item.unit_id)
            ratio, contacts = self.contacts_for(item, solids, settings.support_height_tolerance)
            item.support_ratio = ratio
            item.support_contacts = contacts
            reviewed_base_with_dunnage = (
                item.packing_role == PackingRole.BASE_PANEL
                and item.rules_reviewed
                and ratio + EPSILON >= 0.6
            )
            height_gap_dunnage = any(contact.requires_dunnage for contact in contacts)
            accessory_dunnage_exempt = (
                item.item_kind == ItemKind.ACCESSORY
                and not settings.require_accessory_dunnage
            )
            item.requires_dunnage = (
                not accessory_dunnage_exempt
                and (
                    item.requires_dunnage
                    or reviewed_base_with_dunnage and ratio + EPSILON < settings.minimum_support_ratio
                    or height_gap_dunnage
                )
            )
            if ratio + EPSILON < settings.minimum_support_ratio and not reviewed_base_with_dunnage:
                issues.append(
                    ValidationIssue(
                        code="INSUFFICIENT_SUPPORT",
                        severity=Severity.ERROR,
                        message=f"{item.original_sku} has only {ratio:.0%} bottom support.",
                        pallet_number=pallet.pallet_number,
                        unit_ids=[item.unit_id],
                    )
                )
            elif ratio + EPSILON < settings.minimum_support_ratio and reviewed_base_with_dunnage:
                issues.append(
                    ValidationIssue(
                        code="BASE_PANEL_DUNNAGE_REQUIRED",
                        severity=Severity.WARNING,
                        message=(
                            f"{item.original_sku} has {ratio:.0%} direct support; install continuous dunnage "
                            "under the unsupported base-panel area before adding cartons above it."
                        ),
                        pallet_number=pallet.pallet_number,
                        unit_ids=[item.unit_id],
                    )
                )
            if (
                height_gap_dunnage
                and settings.require_dunnage_for_height_gap
                and not accessory_dunnage_exempt
            ):
                max_gap = max(contact.height_gap for contact in contacts)
                issues.append(
                    ValidationIssue(
                        code="DUNNAGE_REQUIRED",
                        severity=Severity.WARNING,
                        message=(
                            f"{item.original_sku} bridges a {max_gap:g} in support gap; "
                            "install and verify dunnage."
                        ),
                        pallet_number=pallet.pallet_number,
                        unit_ids=[item.unit_id],
                    )
                )
            if approved_overhang > 0 and (
                item.x < -EPSILON
                or item.y < -EPSILON
                or item.x + item.length > pallet.length + EPSILON
                or item.y + item.width > pallet.width + EPSILON
            ):
                issues.append(
                    ValidationIssue(
                        code=(
                            "CABINET_OVERHANG_RESTRAINT_REQUIRED"
                            if item.item_kind == ItemKind.CABINET
                            else "APPROVED_OVERHANG"
                        ),
                        severity=Severity.WARNING,
                        message=(
                            f"{item.original_sku} uses approved "
                            f"{'cabinet' if item.item_kind == ItemKind.CABINET else 'accessory'} overhang; "
                            "verify restraint and quote dimensions."
                        ),
                        pallet_number=pallet.pallet_number,
                        unit_ids=[item.unit_id],
                    )
                )

        for index, first in enumerate(solids):
            for second in solids[index + 1 :]:
                if collides(first, second):
                    issues.append(
                        ValidationIssue(
                            code="COLLISION",
                            severity=Severity.ERROR,
                            message=f"{first.original_sku} overlaps {second.original_sku}.",
                            pallet_number=pallet.pallet_number,
                            unit_ids=[first.unit_id, second.unit_id],
                        )
                    )

        if pallet.total_weight > pallet.max_total_weight + EPSILON:
            issues.append(
                ValidationIssue(
                    code="OVERWEIGHT",
                    severity=Severity.ERROR,
                    message=(
                        f"Pallet is {pallet.total_weight - pallet.max_total_weight:.1f} lb over its "
                        f"{pallet.max_total_weight:.0f} lb limit."
                    ),
                    pallet_number=pallet.pallet_number,
                )
            )

        for lower in items:
            top_weight = carried[lower.unit_id]
            allowed_top = min(lower.max_weight_on_top, lower.load_bearing_strength)
            if top_weight > allowed_top + EPSILON and not lower.rules_reviewed:
                # Catalog defaults (1,000 lb) were never warehouse-reviewed and are
                # exceeded by every measured full pallet; report them for review.
                unreviewed_top_load.append(lower.unit_id)
            elif top_weight > allowed_top + EPSILON:
                issues.append(
                    ValidationIssue(
                        code="TOP_LOAD_EXCEEDED",
                        severity=Severity.ERROR,
                        message=(
                            f"{lower.original_sku} carries {top_weight:.1f} lb; "
                            f"its conservative top-load limit is {allowed_top:.1f} lb."
                        ),
                        pallet_number=pallet.pallet_number,
                        unit_ids=[lower.unit_id],
                    )
                )
            if not lower.stackable and top_weight > EPSILON:
                issues.append(
                    ValidationIssue(
                        code="NON_STACKABLE_LOAD",
                        severity=Severity.ERROR,
                        message=f"Non-stackable item {lower.original_sku} has an item above it.",
                        pallet_number=pallet.pallet_number,
                        unit_ids=[lower.unit_id],
                    )
                )
            heavy_above = [
                upper
                for upper in solids
                if lower.fragile
                and transfers.get((upper.unit_id, lower.unit_id), 0) > settings.fragile_heavy_threshold
                and not _compatible_ffd_stack(lower, upper)
                # A rigid load board spreads the load over the whole carton.
                and upper.item_key != "load-board"
            ]
            reviewed_fragile_base = (
                lower.fragile
                and lower.packing_role == PackingRole.BASE_PANEL
                and lower.rules_reviewed
            )
            if heavy_above and not reviewed_fragile_base:
                issues.append(
                    ValidationIssue(
                        code="HEAVY_ABOVE_FRAGILE",
                        severity=Severity.ERROR,
                        message=f"Fragile item {lower.original_sku} directly supports a heavy load.",
                        pallet_number=pallet.pallet_number,
                        unit_ids=[lower.unit_id, *[item.unit_id for item in heavy_above]],
                    )
                )
            elif heavy_above and reviewed_fragile_base:
                issues.append(
                    ValidationIssue(
                        code="REVIEWED_FRAGILE_BASE_LOAD",
                        severity=Severity.WARNING,
                        message=(
                            f"{lower.original_sku} is being used flat as a reviewed base and carries "
                            f"{top_weight:.1f} lb; verify its protective layer and {allowed_top:.1f} lb limit."
                        ),
                        pallet_number=pallet.pallet_number,
                        unit_ids=[lower.unit_id, *[item.unit_id for item in heavy_above]],
                    )
                )
            if lower.sku.upper().endswith("FFD") and lower.pair_group_id:
                boxes_above = [
                    upper for upper in items
                    if upper.sku.upper().endswith("BOX")
                    and transfers.get((upper.unit_id, lower.unit_id), 0) > EPSILON
                ]
                if boxes_above:
                    issues.append(ValidationIssue(
                        code="PAIR_BOX_ON_FFD",
                        severity=Severity.ERROR,
                        message=f"A BOX component may not be supported by {lower.original_sku} FFD.",
                        pallet_number=pallet.pallet_number,
                        unit_ids=[lower.unit_id, *[item.unit_id for item in boxes_above]],
                    ))

        if edge_standing:
            issues.append(
                ValidationIssue(
                    code="EDGE_STANDING_RESTRAINT_REQUIRED",
                    severity=Severity.WARNING,
                    message=(
                        f"{len(edge_standing)} carton(s) stand on edge to fill deck space; strap or wrap them "
                        "against the load so they cannot tip or slide."
                    ),
                    pallet_number=pallet.pallet_number,
                    unit_ids=edge_standing,
                )
            )
        if unreviewed_top_load:
            issues.append(
                ValidationIssue(
                    code="TOP_LOAD_UNREVIEWED",
                    severity=Severity.WARNING,
                    message=(
                        f"{len(unreviewed_top_load)} carton(s) carry more than their unreviewed default top-load "
                        "value. Confirm the bottom layers with the warehouse."
                    ),
                    pallet_number=pallet.pallet_number,
                    unit_ids=unreviewed_top_load,
                )
            )
        layer_groups: dict[str, list] = defaultdict(list)
        for item in items:
            layer_groups[item.item_key].append(item)
        for group in layer_groups.values():
            for item in group:
                if item.max_stack_layers is None:
                    continue
                layers = 1 + sum(
                    1
                    for lower in group
                    if lower.z + lower.height <= item.z + settings.support_height_tolerance + EPSILON
                    and overlap_area(lower, item) > EPSILON
                )
                if layers > item.max_stack_layers:
                    issues.append(
                        ValidationIssue(
                            code="MAX_STACK_LAYERS",
                            severity=Severity.ERROR,
                            message=f"{item.original_sku} exceeds its {item.max_stack_layers}-layer stack limit.",
                            pallet_number=pallet.pallet_number,
                            unit_ids=[item.unit_id],
                        )
                    )
        return issues

    def validate(self, pallets: list[PalletPlan], settings: PackingSettings) -> list[ValidationIssue]:
        issues = [issue for pallet in pallets for issue in self.validate_pallet(pallet, settings)]
        pair_groups: dict[str, list[tuple[PalletPlan, object]]] = defaultdict(list)
        for pallet in pallets:
            for item in pallet.items:
                if item.pair_group_id:
                    pair_groups[item.pair_group_id].append((pallet, item))
        for group_id, members in pair_groups.items():
            if len(members) != 2:
                issues.append(ValidationIssue(
                    code="PAIR_INCOMPLETE",
                    severity=Severity.ERROR,
                    message=f"Matched cabinet component pair {group_id} is incomplete in the packed plan.",
                    unit_ids=[item.unit_id for _, item in members],
                ))
                continue
            pallet_numbers = {pallet.pallet_number for pallet, _ in members}
            if len(pallet_numbers) != 1:
                issues.append(ValidationIssue(
                    code="PAIR_SEPARATED",
                    severity=Severity.ERROR,
                    message="A matched PC/OC BOX and FFD component were separated onto different pallets.",
                    unit_ids=[item.unit_id for _, item in members],
                ))
                continue
            box = next((item for _, item in members if item.sku.upper().endswith("BOX")), None)
            ffd = next((item for _, item in members if item.sku.upper().endswith("FFD")), None)
            if box and ffd and ffd.z + EPSILON < box.z + box.height:
                issues.append(ValidationIssue(
                    code="PAIR_COMPONENT_ORDER",
                    severity=Severity.ERROR,
                    message=f"{ffd.original_sku} must be above its matching BOX component.",
                    pallet_number=members[0][0].pallet_number,
                    unit_ids=[box.unit_id, ffd.unit_id],
                ))
        return issues
