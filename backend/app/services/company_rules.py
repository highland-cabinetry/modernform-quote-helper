"""Company rule sets: the one place that says which business exceptions apply to whom.

The facts themselves live in ``product_exceptions.py`` (CSB42 footprint, trash-kit
carton, back-order placeholders) and in each company's packing settings (SKU prefixes
ignored during lookup). This module names those exceptions per rule set, so matching,
the settings screen and the tests all read the same list, and a new company does not
inherit Highland's product rules by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .product_exceptions import CSB42_RULE, TRASH_KIT_RULE


@dataclass(frozen=True, slots=True)
class RuleDescription:
    code: str
    title: str
    detail: str
    provenance: str = ""


@dataclass(frozen=True, slots=True)
class RuleSet:
    name: str
    label: str
    product_exceptions: bool  # CSB42 effective footprint, curated trash kit
    status_placeholders: bool = True  # BACK ORDER rows resolved from their description
    rules: tuple[RuleDescription, ...] = field(default_factory=tuple)
    # Door-style prefixes such as NS- or WS- are dropped when the rest is exactly one catalog SKU.
    style_prefixes: bool = False
    # QuickBooks shortens long item codes ('NS-PC309024...'); resolve them from the catalog and the
    # line's description, and only when exactly one catalog product fits.
    truncated_skus: bool = False
    # 'Trash Kit Shaker' and similar named trash kits are the curated TRASH KIT carton.
    trash_kit_variants: bool = False


GENERIC_RULES = (
    RuleDescription(
        "PREFIXES",
        "Ignored SKU prefixes",
        "Prefixes listed in the company's packing settings (for example MB- or WHS-) are removed only while "
        "looking up the catalog. The SKU printed on the document is kept everywhere else.",
    ),
    RuleDescription(
        "BACK_ORDER",
        "Back-order lines",
        "A row whose SKU is only a status such as 'BACK ORDER' is matched from its description when the "
        "description names exactly one catalog SKU or exact catalog description; otherwise it is held for review.",
    ),
    RuleDescription(
        "NO_INVENTED_DATA",
        "No invented dimensions",
        "Unmatched products are never given guessed sizes or weights. They block a freight-ready plan until a "
        "catalog match or complete manual dimensions and weight are supplied.",
    ),
)

RULE_SETS: dict[str, RuleSet] = {
    "highland": RuleSet(
        name="highland",
        label="Highland Cabinetry",
        product_exceptions=True,
        rules=GENERIC_RULES + (
            RuleDescription(
                "CSB42",
                "CSB42 assembled footprint",
                f"Assembled CSB42 is planned as {CSB42_RULE.assembled_length:g} × {CSB42_RULE.assembled_width:g} in, "
                f"centered on a 48 × 40 deck with up to {CSB42_RULE.overhang_per_side:g} in overhang per side. "
                "Height and weight still come from the catalog; a missing assembled height stays unresolved.",
                CSB42_RULE.provenance,
            ),
            RuleDescription(
                "TRASH_KIT",
                "Trash kit carton",
                f"TRASH KIT ships as {' × '.join(f'{v:g}' for v in TRASH_KIT_RULE.dimensions)} in, "
                f"{TRASH_KIT_RULE.weight:g} lb. An assembled trash kit rides inside an assembled B18.",
                TRASH_KIT_RULE.provenance,
            ),
            RuleDescription(
                "DIMENSION_SETS",
                "Cabinet, carton and pallet dimensions are separate",
                "Catalog flatpack/assembled sizes are shipping-carton sizes. Manufacturer cabinet sizes are kept "
                "for reference only. Pallet deck sizes and tare come from packing settings.",
            ),
        ),
    ),
    "modernform": RuleSet(
        name="modernform",
        label="ModernForm Cabinets & Closet",
        product_exceptions=True,
        style_prefixes=True,
        truncated_skus=True,
        trash_kit_variants=True,
        rules=GENERIC_RULES + (
            RuleDescription(
                "STYLE_PREFIX",
                "Door-style prefixes",
                "Item codes such as NS-3DB30 or WS-Sample Door match the catalog SKU after the door-style "
                "prefix (3DB30, Sample door) when exactly one catalog product has that SKU. The printed code is "
                "kept on the order.",
            ),
            RuleDescription(
                "TRUNCATED_SKU",
                "Shortened packing-slip codes",
                "Packing slips shorten long codes ('NS-PC309024...'). The line is matched only when exactly one "
                "catalog product starts with the printed part and fits the line's description (for example "
                "'Box Only' or 'Face Frame and Door'); otherwise it is held for review. These matches are "
                "marked for review.",
            ),
            RuleDescription(
                "TRASH_KIT",
                "Trash kit carton",
                f"TRASH KIT and named trash kits such as 'Trash Kit Shaker' ship as "
                f"{' × '.join(f'{v:g}' for v in TRASH_KIT_RULE.dimensions)} in, {TRASH_KIT_RULE.weight:g} lb "
                "(marked for review).",
                TRASH_KIT_RULE.provenance,
            ),
            RuleDescription(
                "CSB42",
                "CSB42 assembled footprint",
                f"Assembled CSB42 is planned as {CSB42_RULE.assembled_length:g} × {CSB42_RULE.assembled_width:g} in, "
                f"centered on a 48 × 40 deck with up to {CSB42_RULE.overhang_per_side:g} in overhang per side.",
                CSB42_RULE.provenance,
            ),
        ),
    ),
    "none": RuleSet(name="none", label="No product exceptions", product_exceptions=False, rules=GENERIC_RULES),
}


def rule_set(name: str | None) -> RuleSet:
    return RULE_SETS.get(name or "none", RULE_SETS["none"])


def describe_rule_sets() -> list[dict]:
    return [
        {
            "name": value.name,
            "label": value.label,
            "rules": [
                {"code": rule.code, "title": rule.title, "detail": rule.detail, "provenance": rule.provenance}
                for rule in value.rules
            ],
        }
        for value in RULE_SETS.values()
    ]
