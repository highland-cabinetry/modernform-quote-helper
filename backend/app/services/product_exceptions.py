"""Central product identities and approved packing exceptions.

Product-specific facts live here so matching, packing, accounting, and
independent validation cannot silently drift.  Catalog-authored measurements
remain authoritative unless a rule explicitly names the field it overrides.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..models import ItemKind, ItemMaster, PackingState

IDENTITY_SEPARATOR_RE = re.compile(r"[^A-Z0-9]+")
STATUS_PLACEHOLDER_RE = re.compile(
    r"^\s*BACK(?:[\s._/\-]+)?ORDER(?:ED)?(?=$|[\s._/()\-:])"
    r"(?:[\s._/()\-:]+[A-Z0-9][A-Z0-9\s._/()\-:]*)?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PlacementException:
    canonical_sku: str
    assembled_length: float
    assembled_width: float
    standard_deck: tuple[float, float]
    overhang_per_side: float
    centered_on_standard_deck: bool
    provenance: str


@dataclass(frozen=True, slots=True)
class CuratedProductRule:
    canonical_sku: str
    synthetic_id: int
    dimensions: tuple[float, float, float]
    weight: float
    approval_date: str
    provenance: str


CSB42_RULE = PlacementException(
    canonical_sku="CSB42",
    assembled_length=42.0,
    assembled_width=42.0,
    standard_deck=(48.0, 40.0),
    overhang_per_side=1.0,
    centered_on_standard_deck=True,
    provenance=(
        "User-approved CSB42 assembled footprint and centered standard-deck placement; "
        "assembled height and shipping weight remain catalog-authored."
    ),
)

TRASH_KIT_RULE = CuratedProductRule(
    canonical_sku="TRASH KIT",
    synthetic_id=-77154,
    dimensions=(18.0, 14.0, 3.0),
    weight=18.0,
    approval_date="2026-09-15",
    provenance=(
        "Curated trash kit default: 18 x 14 x 3 in, 18 lb shipping weight excluding assembled B18; "
        "user approved 2026-09-15."
    ),
)


def identity_key(value: str | None) -> str:
    """Return a punctuation-insensitive identity key without changing source text."""

    return IDENTITY_SEPARATOR_RE.sub("", str(value or "").upper().strip())


def is_status_placeholder(value: str | None) -> bool:
    """Recognize back-order status labels, including separated/style variants."""

    return bool(STATUS_PLACEHOLDER_RE.fullmatch(str(value or "")))


def is_csb42(value: Any) -> bool:
    return identity_key(str(getattr(value, "sku", value) or "")) == identity_key(CSB42_RULE.canonical_sku)


def effective_dimensions(item: Any, state: PackingState) -> tuple[float, float, float] | None:
    """Return approved effective dimensions while preserving catalog height.

    CSB42's footprint is approved at 42 x 42, but its height is never inferred.
    A missing catalog assembled height therefore remains unresolved.
    """

    if state == PackingState.ASSEMBLED and is_csb42(item):
        height = getattr(item, "assembled_height", None)
        if height is None:
            return None
        return (CSB42_RULE.assembled_length, CSB42_RULE.assembled_width, float(height))
    return item.dimensions_for(state)


def apply_product_exceptions(item: ItemMaster) -> ItemMaster:
    """Return an isolated effective catalog item for matching and downstream use."""

    effective = deepcopy(item)
    if not is_csb42(effective):
        return effective
    dimensions = effective_dimensions(effective, PackingState.ASSEMBLED)
    if dimensions is None:
        # Keep the state wholly absent rather than manufacturing a partial set.
        effective.assembled_length = None
        effective.assembled_width = None
        effective.assembled_height = None
    else:
        effective.assembled_length, effective.assembled_width, effective.assembled_height = dimensions
    return effective


def is_trash_kit_identity(
    *,
    sku: str | None = None,
    original_sku: str | None = None,
    description: str | None = None,
) -> bool:
    """Recognize only explicit trash-kit identities or an exact status description."""

    canonical = identity_key(TRASH_KIT_RULE.canonical_sku)
    if identity_key(sku) == canonical or identity_key(original_sku) == canonical:
        return True
    authored = original_sku or sku
    return is_status_placeholder(authored) and identity_key(description) == canonical


def curated_trash_item(*, description: str | None = None, weight: float | None = None) -> ItemMaster:
    """Build the provenance-bearing shipment-local product approved for TRASH KIT."""

    length, width, height = TRASH_KIT_RULE.dimensions
    return ItemMaster(
        id=TRASH_KIT_RULE.synthetic_id,
        sku=TRASH_KIT_RULE.canonical_sku,
        description=(description or TRASH_KIT_RULE.provenance).strip(),
        manufacturer_dimensions=f"{length:g} x {width:g} x {height:g} in (curated shipping carton)",
        flatpack_length=length,
        flatpack_width=width,
        flatpack_height=height,
        assembled_length=length,
        assembled_width=width,
        assembled_height=height,
        weight=float(weight if weight is not None else TRASH_KIT_RULE.weight),
        category="Accessory",
        item_kind=ItemKind.ACCESSORY,
        stackable=False,
        fragile=False,
        must_stay_upright=False,
        no_upside_down=True,
        allowed_rotations=["LWH", "WLH"],
        max_stack_layers=1,
        max_weight_on_top=0,
        load_bearing_strength=0,
        preferred_pallet_type="standard",
        packing_role="standard",
        bundle_max_units=1,
        bundle_approved=False,
        rules_reviewed=False,
    )


def has_curated_trash_dimensions(values: tuple[float | None, float | None, float | None]) -> bool:
    return all(
        actual is not None and abs(float(actual) - expected) <= 1e-6
        for actual, expected in zip(values, TRASH_KIT_RULE.dimensions, strict=True)
    )
