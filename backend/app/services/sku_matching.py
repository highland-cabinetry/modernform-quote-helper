from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from hashlib import sha1

from ..models import (
    CombinedItem,
    ItemMaster,
    MatchedLine,
    MatchStatus,
    PackingSlipLine,
    PackingState,
    SourceReference,
)
from .product_exceptions import (
    TRASH_KIT_RULE,
    apply_product_exceptions,
    curated_trash_item,
    has_curated_trash_dimensions,
    identity_key,
    is_status_placeholder,
    is_trash_kit_identity,
)

SEPARATOR_RE = re.compile(r"[^A-Z0-9]+")

# Compatibility exports for callers/tests written before the registry existed.
TRASH_KIT_SKU = TRASH_KIT_RULE.canonical_sku
TRASH_KIT_CURATED_ID = TRASH_KIT_RULE.synthetic_id
TRASH_KIT_LENGTH, TRASH_KIT_WIDTH, TRASH_KIT_HEIGHT = TRASH_KIT_RULE.dimensions
TRASH_KIT_WEIGHT = TRASH_KIT_RULE.weight
TRASH_KIT_APPROVAL_DATE = TRASH_KIT_RULE.approval_date
TRASH_KIT_CURATED_NOTE = TRASH_KIT_RULE.provenance


def _normalized_description(value: str | None) -> str:
    return normalize_sku(str(value or ""))


def _trash_like_fields(*, sku: str, description: str) -> bool:
    """Identify a likely trash row that must not fall through to cabinet fuzzy matching."""

    candidate_key = normalize_sku(sku)
    if re.fullmatch(r"(?:[A-Z]{2,5})?B\d{1,3}[A-Z0-9]*", candidate_key):
        return False
    return "TRASH" in normalize_sku(sku) or _normalized_description(description) == "TRASHKIT"


def _approved_trash_dimensions(line: PackingSlipLine) -> bool:
    return has_curated_trash_dimensions((line.length, line.width, line.height))


def _curated_trash_item() -> ItemMaster:
    return curated_trash_item()


def _manual_trash_item(line: PackingSlipLine) -> ItemMaster:
    return curated_trash_item(description=line.description or TRASH_KIT_SKU, weight=line.weight)


def normalize_sku(sku: str, ignored_prefixes: list[str] | None = None) -> str:
    """Normalize a SKU for lookup while leaving the user's authored SKU untouched elsewhere."""

    authored = sku.upper().strip()
    prefixes = sorted(
        {SEPARATOR_RE.sub("", prefix.upper()) for prefix in (ignored_prefixes or []) if prefix},
        key=len,
        reverse=True,
    )
    changed = True
    while changed and authored:
        changed = False
        for prefix in prefixes:
            without_prefix, replacements = re.subn(
                rf"^{re.escape(prefix)}[\s._/\-]+",
                "",
                authored,
                count=1,
            )
            if replacements:
                authored = without_prefix
                changed = True
                break
    return SEPARATOR_RE.sub("", authored)


def _similarity(left: str, right: str) -> float:
    return round(SequenceMatcher(None, left, right).ratio() * 100, 1)


def _identity_tokens(value: str | None) -> tuple[str, ...]:
    return tuple(re.findall(r"[A-Z0-9]+", str(value or "").upper()))


def _contains_whole_identity(description: str, identity: str | None) -> bool:
    description_tokens = _identity_tokens(description)
    identity_tokens = _identity_tokens(identity)
    if not identity_tokens or len(identity_tokens) > len(description_tokens):
        return False
    width = len(identity_tokens)
    return any(
        description_tokens[index : index + width] == identity_tokens
        for index in range(len(description_tokens) - width + 1)
    )


def _description_candidates(
    description: str, item_master: list[ItemMaster], *, curated_trash: bool = True
) -> list[ItemMaster]:
    """Resolve only exact catalog descriptions and whole-token product identities."""

    normalized_description = identity_key(description)
    candidates: dict[str, ItemMaster] = {}
    for item in item_master:
        identity_match = _contains_whole_identity(description, item.sku) or _contains_whole_identity(
            description, item.manufacturer_sku
        )
        description_match = bool(item.description) and identity_key(item.description) == normalized_description
        if not identity_match and not description_match:
            continue
        key = f"id:{item.id}" if item.id is not None else f"sku:{identity_key(item.sku)}"
        candidates.setdefault(key, item)

    trash_key = identity_key(TRASH_KIT_SKU)
    has_catalog_trash = any(identity_key(item.sku) == trash_key for item in item_master)
    if curated_trash and _contains_whole_identity(description, TRASH_KIT_SKU) and not has_catalog_trash:
        candidates.setdefault(f"sku:{trash_key}", _curated_trash_item())
    return list(candidates.values())



# --------------------------------------------------------------------------- Modernform rule set helpers

STYLE_PREFIX_RE = re.compile(r"^\s*([A-Z]{1,5})\s*-\s*(\S.*)$", re.IGNORECASE)
TRUNCATION_RE = re.compile(r"(?:\.{2,}|\u2026)\s*$")
MIN_TRUNCATED_KEY = 4
# Wording on invoices/packing slips and in the catalog that names the same component.
DESCRIPTION_SYNONYMS = (
    (re.compile(r"FACEFRAMEANDLOWERDOORS?"), "FFDLOWER"),
    (re.compile(r"FACEFRAMES?(?:AND|&)?DOORS?"), "FFD"),
    (re.compile(r"BOXONLY"), "BOX"),
    (re.compile(r"DRAWERS?RIGHT"), "DR"),
    (re.compile(r"DRAWERS?LEFT"), "DL"),
)


def style_prefix_remainder(sku: str) -> str | None:
    """'NS-3DB30' -> '3DB30'; None when the code has no door-style prefix."""

    match = STYLE_PREFIX_RE.match(str(sku or ""))
    return match.group(2).strip() if match else None


def is_truncated_sku(sku: str) -> bool:
    return bool(TRUNCATION_RE.search(str(sku or "")))


def _without_truncation(sku: str) -> str:
    return TRUNCATION_RE.sub("", str(sku or "")).strip()


def _description_key(text: str | None) -> str:
    key = identity_key(text)
    for pattern, replacement in DESCRIPTION_SYNONYMS:
        key = pattern.sub(replacement, key)
    return key


def is_trash_kit_variant(sku: str, description: str = "") -> bool:
    """'Trash Kit Shaker', 'Trash Kit Sha...', 'NS-Trash Kit' (not 'TRASH CAN 18')."""

    for value in (_without_truncation(style_prefix_remainder(sku) or sku), description):
        if identity_key(value).startswith("TRASHKIT"):
            return True
    return False


def resolve_truncated(
    line: PackingSlipLine,
    exact_index: dict[str, list[ItemMaster]],
    ignored_prefixes: list[str],
) -> tuple[ItemMaster | None, list[ItemMaster], str]:
    """Resolve a shortened code such as 'NS-PC309024...' from the catalog and the line's description.

    Returns (item, candidates, how). ``item`` is set only when exactly one catalog product starts
    with the printed part and, if several do, the description clearly names one of them.
    """

    printed = _without_truncation(line.original_sku)
    keys = [normalize_sku(printed, ignored_prefixes)]
    remainder = style_prefix_remainder(printed)
    if remainder:
        keys.insert(0, normalize_sku(remainder, ignored_prefixes))
    for key in keys:
        if len(key) < MIN_TRUNCATED_KEY:
            continue
        candidates: dict[int | str, tuple[str, ItemMaster]] = {}
        for candidate_key, items in exact_index.items():
            if candidate_key.startswith(key):
                for item in items:
                    candidates.setdefault(item.id if item.id is not None else item.sku, (candidate_key, item))
        if not candidates:
            continue
        found = list(candidates.values())
        if len(found) == 1:
            return found[0][1], [found[0][1]], "the only catalog product starting with the printed code"
        line_description = _description_key(line.description)
        scored: list[tuple[float, bool, ItemMaster]] = []
        for candidate_key, item in found:
            item_description = _description_key(item.description)
            exact = bool(item_description) and item_description == line_description
            score = 100.0 if exact else _similarity(line_description, item_description)
            rest = candidate_key[len(key):]
            named = bool(rest) and rest in line_description
            if named:
                score += 20
            scored.append((score, exact or named, item))
        scored.sort(key=lambda entry: entry[0], reverse=True)
        best, runner_up = scored[0], scored[1]
        if best[1] and best[0] >= 70 and best[0] - runner_up[0] >= 10:
            return best[2], [entry[2] for entry in scored], f"the description '{line.description}'"
        return None, [entry[2] for entry in scored], ""
    return None, [], ""


def _manual_identity_fingerprint(match: MatchedLine) -> str:
    """Identify equal manual products without merging physically different rows."""

    master = match.matched_item
    if master is None:  # pragma: no cover - guarded by combine_matches
        return match.normalized_sku
    physical_facts = master.model_dump(
        mode="json",
        exclude={"id", "sku", "manufacturer_sku", "description", "manufacturer_dimensions", "active"},
    )
    payload = {
        "normalized_sku": match.normalized_sku,
        "packing_state": match.line.packing_state.value,
        "physical_facts": physical_facts,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha1(encoded).hexdigest()


def _match_trash_line(
    line: PackingSlipLine,
    normalized: str,
    trash_candidates: list[ItemMaster],
    *,
    resolved_from_status: bool = False,
) -> MatchedLine:
    if len(trash_candidates) > 1:
        return MatchedLine(
            line=line,
            normalized_sku=normalized,
            match_status=MatchStatus.UNMATCHED,
            confidence=0,
            review_message="More than one item master row has the TRASH KIT identity; choose the correct item.",
        )
    if len(trash_candidates) == 1:
        existing = trash_candidates[0]
        conflict = ""
        if abs(existing.weight - TRASH_KIT_WEIGHT) > 1e-6:
            conflict += (
                " Existing TRASH KIT master weight differs from the curated 18 lb approval; "
                "the existing master was preserved and needs review."
            )
        dimensions = existing.dimensions_for(PackingState.FLATPACK)
        if not dimensions or any(
            abs(actual - expected) > 1e-6
            for actual, expected in zip(
                dimensions,
                (TRASH_KIT_LENGTH, TRASH_KIT_WIDTH, TRASH_KIT_HEIGHT),
                strict=True,
            )
        ):
            conflict += " Existing TRASH KIT flatpack dimensions differ from 18 x 14 x 3 in; review the master."
        if conflict:
            return MatchedLine(
                line=line,
                normalized_sku=normalized,
                match_status=MatchStatus.UNMATCHED,
                confidence=0,
                review_message=conflict,
            )
        return MatchedLine(
            line=line,
            normalized_sku=normalized,
            match_status=MatchStatus.CONFIRMED_ALIAS if resolved_from_status else MatchStatus.EXACT,
            confidence=100,
            matched_item=existing,
            review_message=(
                f"Status placeholder {line.original_sku!r} resolved from its exact description to TRASH KIT; "
                "verify before release."
                if resolved_from_status
                else None
            ),
        )
    if _approved_trash_dimensions(line) and line.weight == TRASH_KIT_WEIGHT:
        return MatchedLine(
            line=line,
            normalized_sku=normalized,
            match_status=MatchStatus.MANUAL,
            confidence=100,
            matched_item=_manual_trash_item(line),
            review_message="Source-authored trash kit dimensions and weight used; verify before release.",
        )
    if line.weight is None and _approved_trash_dimensions(line):
        reason = "Trash kit weight is required for source-authored dimensions."
    elif any(value is not None for value in (line.length, line.width, line.height, line.weight)):
        reason = "Trash kit source dimensions must be 18 x 14 x 3 in and include the approved 18 lb weight."
    else:
        return MatchedLine(
            line=line,
            normalized_sku=normalized,
            match_status=MatchStatus.EXACT,
            confidence=100,
            matched_item=_curated_trash_item(),
            review_message=TRASH_KIT_CURATED_NOTE,
        )
    return MatchedLine(
        line=line,
        normalized_sku=normalized,
        match_status=MatchStatus.UNMATCHED,
        confidence=0,
        review_message=reason,
    )


def match_lines(
    lines: list[PackingSlipLine],
    item_master: list[ItemMaster],
    *,
    ignored_prefixes: list[str],
    fuzzy_threshold: float,
    confirmed_aliases: dict[str, ItemMaster] | None = None,
    rule_set: str = "highland",
) -> list[MatchedLine]:
    """Match packing-slip lines to catalog items.

    ``rule_set`` selects the company's product exceptions (see company_rules.py). The
    default keeps Highland's approved CSB42 and trash-kit behaviour.
    """
    from .company_rules import rule_set as resolve_rule_set

    rules = resolve_rule_set(rule_set)
    exceptions = rules.product_exceptions
    if exceptions:
        item_master = [apply_product_exceptions(item) for item in item_master]
        confirmed_aliases = {
            normalized: apply_product_exceptions(item) for normalized, item in (confirmed_aliases or {}).items()
        }
    else:
        confirmed_aliases = dict(confirmed_aliases or {})
    exact_index: dict[str, list[ItemMaster]] = {}
    for item in item_master:
        aliases = [item.sku]
        if item.manufacturer_sku:
            aliases.append(item.manufacturer_sku)
        for alias in aliases:
            key = normalize_sku(alias, ignored_prefixes)
            candidates = exact_index.setdefault(key, [])
            if not any(candidate.id == item.id and candidate.sku == item.sku for candidate in candidates):
                candidates.append(item)

    matches: list[MatchedLine] = []
    for line in lines:
        normalized = normalize_sku(line.original_sku, ignored_prefixes)

        # Status placeholders are not product identities.  Resolve them only
        # through an exact, unique identity in the untouched description.
        if rules.status_placeholders and is_status_placeholder(line.original_sku):
            candidates = _description_candidates(line.description, item_master, curated_trash=exceptions)
            if exceptions and len(candidates) == 1 and identity_key(candidates[0].sku) == identity_key(TRASH_KIT_SKU):
                matches.append(_match_trash_line(line, normalized, candidates, resolved_from_status=True))
            elif len(candidates) == 1:
                product = candidates[0]
                matches.append(
                    MatchedLine(
                        line=line,
                        normalized_sku=normalized,
                        match_status=MatchStatus.CONFIRMED_ALIAS,
                        confidence=100,
                        matched_item=product,
                        review_message=(
                            f"Status placeholder {line.original_sku!r} resolved from its description to "
                            f"catalog product {product.sku}; verify before release."
                        ),
                    )
                )
            elif candidates:
                labels = ", ".join(sorted({candidate.sku for candidate in candidates}))
                matches.append(
                    MatchedLine(
                        line=line,
                        normalized_sku=normalized,
                        match_status=MatchStatus.UNMATCHED,
                        confidence=0,
                        review_message=(
                            f"Status placeholder description is ambiguous among {labels}. "
                            "Include exactly one whole catalog SKU/manufacturer SKU or one exact catalog description."
                        ),
                    )
                )
            else:
                matches.append(
                    MatchedLine(
                        line=line,
                        normalized_sku=normalized,
                        match_status=MatchStatus.UNMATCHED,
                        confidence=0,
                        review_message=(
                            "Status placeholder does not identify a catalog product. Include one whole catalog "
                            "SKU/manufacturer SKU or the exact catalog description."
                        ),
                    )
                )
            continue

        if exceptions and is_trash_kit_identity(sku=line.original_sku, original_sku=line.original_sku):
            trash_candidates = exact_index.get(normalize_sku(TRASH_KIT_SKU), [])
            matches.append(_match_trash_line(line, normalized, trash_candidates))
            continue

        if exceptions and rules.trash_kit_variants and is_trash_kit_variant(line.original_sku, line.description):
            trash_candidates = exact_index.get(normalize_sku(TRASH_KIT_SKU), [])
            trash = _match_trash_line(line, normalized, trash_candidates)
            if trash.matched_item is not None and trash.match_status == MatchStatus.EXACT:
                trash.match_status = MatchStatus.CONFIRMED_ALIAS
                trash.review_message = (
                    f"{line.original_sku}"
                    + (f" ({line.description})" if line.description and identity_key(line.description)
                       != identity_key(line.original_sku) else "")
                    + " planned as the TRASH KIT carton; verify before release."
                )
            matches.append(trash)
            continue

        # Any other likely trash row is blocked before the generic fuzzy/manual
        # paths.  This is what prevents an unverified description from becoming
        # a false B42 (or another cabinet) match.
        if exceptions and _trash_like_fields(sku=line.original_sku, description=line.description):
            matches.append(
                MatchedLine(
                    line=line,
                    normalized_sku=normalized,
                    match_status=MatchStatus.UNMATCHED,
                    confidence=0,
                    review_message=(
                        "This row appears to be a trash item but does not explicitly identify a TRASH KIT "
                        "or provide approved 18 x 14 x 3 in dimensions and a positive weight."
                    ),
                )
            )
            continue

        exact_candidates = exact_index.get(normalized, [])
        if len(exact_candidates) == 1:
            matches.append(
                MatchedLine(
                    line=line,
                    normalized_sku=normalized,
                    match_status=MatchStatus.EXACT,
                    confidence=100,
                    matched_item=exact_candidates[0],
                )
            )
            continue
        if len(exact_candidates) > 1:
            matches.append(
                MatchedLine(
                    line=line,
                    normalized_sku=normalized,
                    match_status=MatchStatus.UNMATCHED,
                    confidence=0,
                    review_message="More than one item master row has this normalized SKU; choose the correct item.",
                )
            )
            continue

        if rules.style_prefixes and not is_truncated_sku(line.original_sku):
            remainder = style_prefix_remainder(line.original_sku)
            styled = exact_index.get(normalize_sku(remainder, ignored_prefixes), []) if remainder else []
            if len(styled) == 1:
                matches.append(
                    MatchedLine(
                        line=line,
                        normalized_sku=normalized,
                        match_status=MatchStatus.EXACT,
                        confidence=100,
                        matched_item=styled[0],
                    )
                )
                continue

        if rules.truncated_skus and is_truncated_sku(line.original_sku):
            resolved, candidates, how = resolve_truncated(line, exact_index, ignored_prefixes)
            if resolved is not None:
                matches.append(
                    MatchedLine(
                        line=line,
                        normalized_sku=normalized,
                        match_status=MatchStatus.CONFIRMED_ALIAS,
                        confidence=100,
                        matched_item=resolved,
                        review_message=(
                            f"Shortened code {line.original_sku!r} matched to {resolved.sku} from {how}; "
                            "verify before release."
                        ),
                    )
                )
                continue
            if candidates:
                labels = ", ".join(item.sku for item in candidates[:6])
                matches.append(
                    MatchedLine(
                        line=line,
                        normalized_sku=normalized,
                        match_status=MatchStatus.UNMATCHED,
                        confidence=0,
                        review_message=(
                            f"Shortened code {line.original_sku!r} fits more than one catalog product ({labels}). "
                            "Enter the full item code (it is on the invoice)."
                        ),
                    )
                )
                continue

        confirmed = confirmed_aliases.get(normalized)
        if confirmed is None and rules.style_prefixes:
            remainder = style_prefix_remainder(line.original_sku)
            if remainder:
                confirmed = confirmed_aliases.get(normalize_sku(remainder, ignored_prefixes))
        if confirmed:
            matches.append(
                MatchedLine(
                    line=line,
                    normalized_sku=normalized,
                    match_status=MatchStatus.CONFIRMED_ALIAS,
                    confidence=100,
                    matched_item=confirmed,
                    review_message=f"Confirmed alias: {line.original_sku} maps to {confirmed.sku}.",
                )
            )
            continue

        best: tuple[float, ItemMaster] | None = None
        for candidate_key, candidates in exact_index.items():
            score = _similarity(normalized, candidate_key)
            if best is None or score > best[0]:
                best = (score, candidates[0])
        if best and best[0] >= fuzzy_threshold:
            matches.append(
                MatchedLine(
                    line=line,
                    normalized_sku=normalized,
                    match_status=MatchStatus.FUZZY,
                    confidence=best[0],
                    matched_item=best[1],
                    review_message=f"Review required: {line.original_sku} was roughly matched to {best[1].sku}.",
                )
            )
        elif all(value is not None for value in (line.length, line.width, line.height, line.weight)):
            ad_hoc = ItemMaster(
                sku=line.original_sku,
                description=line.description or "Manual item",
                **{
                    f"{line.packing_state.value}_length": float(line.length),
                    f"{line.packing_state.value}_width": float(line.width),
                    f"{line.packing_state.value}_height": float(line.height),
                },
                weight=float(line.weight),
                category=line.category or "Manual / Unclassified",
                stackable=False,
                fragile=False,
                must_stay_upright=True,
                allowed_rotations=["LWH", "WLH"],
                max_stack_layers=1,
                max_weight_on_top=0,
                load_bearing_strength=0,
            )
            matches.append(
                MatchedLine(
                    line=line,
                    normalized_sku=normalized,
                    match_status=MatchStatus.MANUAL,
                    confidence=100,
                    matched_item=ad_hoc,
                    review_message="Manual dimensions used; verify the product and packaging before release.",
                )
            )
        else:
            matches.append(
                MatchedLine(
                    line=line,
                    normalized_sku=normalized,
                    match_status=MatchStatus.UNMATCHED,
                    confidence=0,
                    review_message="No item master match and no complete manual dimensions/weight were supplied.",
                )
            )
    return matches


def combine_matches(matches: list[MatchedLine], *, allow_fuzzy: bool) -> tuple[list[CombinedItem], list[MatchedLine]]:
    combined: dict[str, CombinedItem] = {}
    review: list[MatchedLine] = []
    for match in matches:
        if match.match_status in {MatchStatus.FUZZY, MatchStatus.UNMATCHED, MatchStatus.MANUAL} or (
            match.match_status == MatchStatus.CONFIRMED_ALIAS and match.review_message
        ):
            review.append(match)
        if not match.matched_item:
            continue
        if match.match_status == MatchStatus.FUZZY and not allow_fuzzy:
            continue

        identity = (
            str(match.matched_item.id)
            if match.matched_item.id is not None
            else f"manual:{_manual_identity_fingerprint(match)}"
        )
        key = f"{identity}:{match.line.packing_state.value}"
        source = SourceReference(
            source_ref=match.line.source_ref,
            original_sku=match.line.original_sku,
            quantity=match.line.quantity,
            line_number=match.line.line_number,
        )
        if key not in combined:
            combined[key] = CombinedItem(
                key=key,
                master_item=match.matched_item,
                packing_state=match.line.packing_state,
                quantity=match.line.quantity,
                sources=[source],
                original_skus=[match.line.original_sku],
                match_status=match.match_status,
                match_confidence=match.confidence,
            )
        else:
            current = combined[key]
            current.quantity += match.line.quantity
            current.sources.append(source)
            if match.line.original_sku not in current.original_skus:
                current.original_skus.append(match.line.original_sku)
            if match.confidence < current.match_confidence:
                current.match_confidence = match.confidence
            if match.match_status != MatchStatus.EXACT:
                current.match_status = match.match_status
    return list(combined.values()), review
