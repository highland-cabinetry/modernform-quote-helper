from __future__ import annotations

from ..database import Repository
from ..models import (
    MatchStatus,
    PackingRequest,
    PackingResult,
    Severity,
    UnpackedItem,
    ValidationIssue,
)
from ..packing import PackingEngine, StackPlannerPackingEngine
from .sku_matching import combine_matches, match_lines


def request_catalog(request: PackingRequest, repository) -> tuple[str, str]:
    """(catalog, rule_set) for a request: the company's, or the default catalog with the edition's rules."""

    fixed = getattr(repository, "snapshot_rules", None)
    if fixed:
        return fixed
    if hasattr(repository, "get_company"):
        try:
            company = repository.get_company(request.company_id)
            return company.config.catalog, company.config.rule_set
        except (KeyError, RuntimeError):
            if request.company_id:
                raise ValueError("The company for this request no longer exists") from None
    from ..edition import current as current_edition

    return "default", current_edition().rule_set


def match_request(request: PackingRequest, repository: Repository):
    catalog, rule_set = request_catalog(request, repository)
    if hasattr(repository, "catalog_items"):
        items = repository.catalog_items(catalog)
        aliases = repository.catalog_alias_targets(catalog)
    else:
        items, aliases = repository.list_items(), repository.alias_targets()
    return match_lines(
        request.lines,
        items,
        ignored_prefixes=request.settings.sku_prefixes_to_ignore,
        fuzzy_threshold=request.settings.fuzzy_match_threshold,
        confirmed_aliases=aliases,
        rule_set=rule_set,
    )


TRUCKS = (
    # name, usable floor length (in), typical payload (lb)
    ("26 ft box truck", 300.0, 10000.0),
    ("53 ft dry van", 624.0, 44000.0),
)


def effective_settings(request: PackingRequest):
    """Settings the engine plans with: the dedicated-truck optimizer uses its own pallet weights."""
    if request.optimizer != "truckload":
        return request.settings
    settings = request.settings
    return settings.model_copy(update={
        "standard_pallet": settings.standard_pallet.model_copy(
            update={"max_total_weight": settings.truckload_standard_max_weight}),
        "long_pallet": settings.long_pallet.model_copy(
            update={"max_total_weight": settings.truckload_long_max_weight}),
    })


def truck_floor(result: PackingResult) -> dict:
    """Floor length of a dedicated truck, loading pallets two across and never stacked.

    See StackPlannerPackingEngine.floor_inches for the loading pattern.
    """
    shorts = sum(p.pallet_type == "standard" for p in result.pallets)
    longs = sum(p.pallet_type == "long" for p in result.pallets)
    linear = StackPlannerPackingEngine.floor_inches([p.pallet_type for p in result.pallets])
    weight = round(sum(p.total_weight for p in result.pallets), 1)
    fits = next((name for name, length, payload in TRUCKS if linear <= length and weight <= payload),
                "more than one 53 ft dry van")
    return {
        "linear_inches": linear,
        "linear_feet": round(linear / 12, 1),
        "standard_pallets": shorts,
        "long_pallets": longs,
        "total_weight": weight,
        "fits": fits,
        "assumption": ("Pallets two across, not stacked: 96 x 40 lengthwise in two lanes, 48 x 40 pairs "
                       "turned sideways or two beside an odd 96 x 40; about 96 in of inside width."),
    }


def build_shipment(
    request: PackingRequest,
    repository: Repository,
    engine: PackingEngine | None = None,
) -> PackingResult:
    matches = match_request(request, repository)
    combined, review = combine_matches(matches, allow_fuzzy=request.allow_fuzzy_matches)
    packer = engine or StackPlannerPackingEngine()
    packer.objective = request.optimizer
    result = packer.pack(
        request.shipment_name,
        combined,
        effective_settings(request),
        overrides=request.overrides,
        rotation_preferences=request.rotation_preferences,
        split_bundle_ids=request.split_bundle_ids,
    )
    result.match_review = review
    result.shipping_info = request.shipping_info
    result.optimizer = request.optimizer
    if request.optimizer == "truckload":
        result.truck_load = truck_floor(result)

    unresolved = [
        match
        for match in matches
        if match.match_status == MatchStatus.UNMATCHED
        or (match.match_status == MatchStatus.FUZZY and not request.allow_fuzzy_matches)
    ]
    for match in unresolved:
        reason_code = "FUZZY_MATCH_REVIEW" if match.match_status == MatchStatus.FUZZY else "UNMATCHED_SKU"
        reason = match.review_message or "Item requires manual review before packing."
        master_dimensions = (
            match.matched_item.dimensions_for(match.line.packing_state) if match.matched_item else None
        )
        authored_dimensions = (
            (match.line.length, match.line.width, match.line.height)
            if all(value is not None for value in (match.line.length, match.line.width, match.line.height))
            else None
        )
        dimensions = authored_dimensions or master_dimensions or (0, 0, 0)
        for index in range(match.line.quantity):
            if result.expected_unit_ids:
                result.expected_unit_ids.append(f"unresolved:{match.line.id}:{index + 1:04d}")
            result.unpacked.append(
                UnpackedItem(
                    unit_id=f"unresolved:{match.line.id}:{index + 1:04d}",
                    sku=match.matched_item.sku if match.matched_item else match.line.original_sku,
                    original_sku=match.line.original_sku,
                    packing_state=match.line.packing_state,
                    description=match.line.description,
                    source_refs=[match.line.source_ref],
                    weight=match.line.weight or (match.matched_item.weight if match.matched_item else 0),
                    dimensions=dimensions,
                    reason_code=reason_code,
                    reason=reason,
                )
            )
        result.warnings.append(
            ValidationIssue(
                code=reason_code,
                severity=Severity.WARNING if match.match_status == MatchStatus.FUZZY else Severity.ERROR,
                message=f"{match.line.original_sku} × {match.line.quantity}: {reason}",
                unit_ids=[match.line.id],
            )
        )

    for match in review:
        if match.match_status == MatchStatus.MANUAL:
            result.warnings.append(
                ValidationIssue(
                    code="MANUAL_ITEM_DATA",
                    severity=Severity.WARNING,
                    message=f"{match.line.original_sku}: manual dimensions and weight require verification.",
                    unit_ids=[match.line.id],
                )
            )
        elif match.match_status == MatchStatus.FUZZY and request.allow_fuzzy_matches:
            result.warnings.append(
                ValidationIssue(
                    code="FUZZY_MATCH_USED",
                    severity=Severity.WARNING,
                    message=(
                        f"{match.line.original_sku} was packed as {match.matched_item.sku} at "
                        f"{match.confidence:.1f}% confidence. Confirm before release."
                    ),
                    unit_ids=[match.line.id],
                )
            )

    unreviewed = [
        match
        for match in matches
        if match.matched_item
        and match.matched_item.id is not None
        and not match.matched_item.rules_reviewed
        and match.match_status != MatchStatus.UNMATCHED
    ]
    if unreviewed:
        skus = sorted({match.matched_item.sku for match in unreviewed if match.matched_item})
        preview = ", ".join(skus[:8])
        suffix = f" and {len(skus) - 8} more" if len(skus) > 8 else ""
        result.warnings.append(
            ValidationIssue(
                code="ITEM_RULES_UNREVIEWED",
                severity=Severity.WARNING,
                message=(
                    f"Handling/load-bearing defaults have not been warehouse-reviewed for {preview}{suffix}. "
                    "Verify the physical load before release."
                ),
                unit_ids=[match.line.id for match in unreviewed],
            )
        )
    unresolved_quantity = sum(match.line.quantity for match in unresolved)
    unresolved_known_weight = sum(
        (match.line.weight or (match.matched_item.weight if match.matched_item else 0)) * match.line.quantity
        for match in unresolved
    )
    result.summary.total_items += unresolved_quantity
    result.summary.unpacked_items = len(result.unpacked)
    result.summary.total_item_weight = round(result.summary.total_item_weight + unresolved_known_weight, 2)
    result.plan_status = "blocked" if result.unpacked else result.plan_status
    result.human_review_required = bool(result.unpacked or result.warnings)

    if result.summary.total_items != sum(line.quantity for line in request.lines):
        result.warnings.append(ValidationIssue(code="QUANTITY_RECONCILIATION", severity=Severity.ERROR,
            message="Source quantities do not match the accounted shipping units."))
        result.plan_status = "blocked"
    if result.accounting_errors:
        result.warnings.append(ValidationIssue(code="QUANTITY_RECONCILIATION", severity=Severity.ERROR,
            message=" ".join(result.accounting_errors)))
        result.plan_status = "blocked"
    result.human_review_required = bool(result.unpacked or result.warnings)

    if request.company_id and hasattr(repository, "save_company_settings"):
        repository.save_company_settings(repository.get_company(request.company_id), request.settings)
    else:
        repository.save_settings(request.settings)
    if request.save_result:
        repository.save_shipment(request.model_dump(mode="json"), result)
        if hasattr(repository, "save_quote"):
            from .freight import record_packed_quote

            record_packed_quote(repository, request, result)
    return result
