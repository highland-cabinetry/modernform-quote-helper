"""Isolated production packing run. Reads an immutable snapshot, writes atomic progress.

The stack planner usually finishes in well under a second, so a "quick" and a
"refine" job run the same deterministic search; the job/progress contract is kept
so the Builder screen and saved-job flow are unchanged.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

from .models import MatchStatus, PackingRequest, PackingResult, Severity, ValidationIssue
from .packing import StackPlannerPackingEngine
from .packing.common import _expand_units, _orientation, apply_edge_permission
from .services.shipments import build_shipment, effective_settings, match_request
from .services.sku_matching import combine_matches


class SnapshotRepository:
    """Read-only inputs captured when the job started; persistence is a no-op."""

    def __init__(self, snapshot):
        from .models import ItemMaster

        self.items = [ItemMaster.model_validate(v) for v in snapshot["master"]]
        self.aliases = {k: ItemMaster.model_validate(v) for k, v in snapshot["aliases"].items()}
        self.snapshot_rules = (snapshot.get("catalog", "default"), snapshot.get("rule_set", "highland"))

    def catalog_items(self, _catalog):
        return self.items

    def catalog_alias_targets(self, _catalog):
        return self.aliases

    def list_items(self):
        return self.items

    def alias_targets(self):
        return self.aliases

    def save_settings(self, _settings):
        pass

    def save_shipment(self, *_args):
        raise RuntimeError("The packing worker never saves shipments directly")


def run(snapshot, publish):
    started_all = snapshot.get("started_monotonic", time.monotonic())
    request = PackingRequest.model_validate(snapshot["request"])
    request.save_result = False
    repo = SnapshotRepository(snapshot)
    matching_started = time.perf_counter()
    matches = match_request(request, repo)
    combined, _ = combine_matches(matches, allow_fuzzy=request.allow_fuzzy_matches)
    units, unavailable = _expand_units(combined, set(request.split_bundle_ids))
    planning_settings = effective_settings(request)
    apply_edge_permission(units, planning_settings)
    expected = {unit.unit_id: unit for unit in units}

    def source_lines(unit):
        return [
            m.line.id
            for m in matches
            if m.line.original_sku == unit.original_sku
            and m.line.source_ref in unit.source_refs
            and m.line.packing_state == unit.packing_state
        ]

    unresolved = [
        m for m in matches
        if m.match_status == MatchStatus.UNMATCHED
        or (m.match_status == MatchStatus.FUZZY and not request.allow_fuzzy_matches)
    ]
    blockers = [
        {
            "category": "input_blocked",
            "reason_code": "FUZZY_MATCH_REVIEW" if m.match_status == MatchStatus.FUZZY else "UNMATCHED_SKU",
            "line_ids": [m.line.id],
            "unit_ids": [],
            "sku": m.line.original_sku,
            "quantity": m.line.quantity,
            "message": m.review_message or "Item match needs review.",
            "suggested_correction": (
                "Review the source row and confirm its exact SKU, or explicitly exclude a non-freight service row."
            ),
        }
        for m in unresolved
    ]
    blockers.extend(
        {
            "category": "input_blocked",
            "reason_code": u.reason_code,
            "line_ids": source_lines(u),
            "unit_ids": [u.unit_id],
            "sku": u.original_sku,
            "quantity": 1,
            "message": u.reason,
            "suggested_correction": "Supply verified dimensions for this packing state.",
        }
        for u in unavailable
    )
    probe = StackPlannerPackingEngine()
    probe.settings = planning_settings
    probe._rotation_preferences = {}
    for unit in units:
        reason = probe.cannot_fit_reason(unit)
        if reason:
            code, message = reason
            blockers.append(
                {
                    "category": "physically_blocked",
                    "reason_code": code,
                    "line_ids": source_lines(unit),
                    "unit_ids": [unit.unit_id],
                    "sku": unit.original_sku,
                    "quantity": 1,
                    "message": f"{unit.length:g} × {unit.width:g} × {unit.height:g} in: {message}",
                    "suggested_correction": (
                        "Verify carton measurements and pallet limits, or raise the overhang/height settings "
                        "if the warehouse ships this carton that way."
                    ),
                }
            )
    state = {
        "candidate": None,
        "comparison_estimate": None,
        "outcome_category": "searching",
        "blockers": blockers,
        "timings": [],
        "matching_seconds": time.perf_counter() - matching_started,
        "matching_decisions": [
            {
                "line_id": m.line.id,
                "sku": m.line.original_sku,
                "quantity": m.line.quantity,
                "status": m.match_status.value,
                "matched_sku": m.matched_item.sku if m.matched_item else None,
                "message": m.review_message,
            }
            for m in matches
        ],
        "carton_accounting": {
            "input_quantity": sum(m.line.quantity for m in matches),
            "expanded_quantity": len(units),
            "unresolved_quantity": sum(m.line.quantity for m in unresolved) + len(unavailable),
            "unit_ids": list(expected),
        },
        "search_progress": {},
        "message": "Checking shipment inputs",
    }

    def emit(**updates):
        state.update(updates)
        publish(state)

    if blockers:
        category = "input_blocked" if any(b["category"] == "input_blocked" for b in blockers) else "physically_blocked"
        emit(
            outcome_category=category,
            termination_reason="preflight",
            message="Resolve shipment inputs before packing."
            if category == "input_blocked"
            else "A carton cannot fit under the current pallet limits.",
        )
        return
    emit(message="Inputs checked; planning pallets")
    engine = StackPlannerPackingEngine()
    started = time.perf_counter()
    result = build_shipment(request, repo, engine)
    seconds = time.perf_counter() - started
    validation_started = time.perf_counter()
    errors = [
        v for v in engine.validator.validate(result.pallets, planning_settings) if v.severity == Severity.ERROR
    ]
    placed = [item for pallet in result.pallets for item in pallet.items]
    exact = Counter(item.unit_id for item in placed) == Counter(expected.keys()) and all(
        item.rotation in expected[item.unit_id].allowed_rotations
        and all(
            abs(a - b) < 1e-6
            for a, b in zip(
                (item.length, item.width, item.height, item.weight),
                (*_orientation(expected[item.unit_id], item.rotation), expected[item.unit_id].weight),
                strict=True,
            )
        )
        for item in placed
    )
    if not exact:
        errors.append(
            ValidationIssue(
                code="QUANTITY_OR_GEOMETRY_MISMATCH",
                severity=Severity.ERROR,
                message="The plan does not exactly reconcile the expected carton identities and dimensions.",
            )
        )
    accepted = {v.unit_id for v in result.override_outcomes if v.kind == "exact_lock" and v.status == "accepted"}
    locations = {i.unit_id: (p.pallet_number, i.x, i.y, i.z, i.rotation) for p in result.pallets for i in p.items}
    locks = all(
        locations.get(v.unit_id) == (v.pallet_number, v.x, v.y, v.z, v.rotation)
        for v in request.overrides
        if v.unit_id in accepted
    )
    complete = (exact and locks and result.freight_ready and not result.unpacked and not errors
                and result.summary.packed_items == result.summary.total_items)
    state["timings"].append({
        "strategy": f"{engine.name} {engine.version}",
        "seconds": seconds,
        "final_validation_seconds": time.perf_counter() - validation_started,
        "state": "complete",
        "outcome_category": "valid" if complete else "physically_blocked",
        "unpacked": [
            {"unit_id": u.unit_id, "sku": u.original_sku, "reason_code": u.reason_code, "message": u.reason}
            for u in result.unpacked
        ],
        "validation_errors": [e.model_dump(mode="json") for e in errors],
    })
    if complete:
        state["first_valid_seconds"] = time.monotonic() - started_all
        emit(
            candidate=result.model_dump(mode="json"),
            diagnostic=None,
            validation_errors=[],
            blockers=[],
            outcome_category="valid",
            termination_reason="strategies_exhausted",
            message="Validated plan ready",
        )
        return
    seed = PackingResult.model_validate(snapshot["seed"]) if snapshot.get("seed") else None
    emit(
        candidate=seed.model_dump(mode="json") if seed else None,
        diagnostic=result.model_dump(mode="json"),
        validation_errors=[e.model_dump(mode="json") for e in errors],
        blockers=[
            {
                "category": "physically_blocked",
                "reason_code": u.reason_code,
                "unit_ids": [u.unit_id],
                "line_ids": source_lines(u),
                "sku": u.original_sku,
                "quantity": 1,
                "message": u.reason,
                "suggested_correction": "Inspect the affected carton, its measurements and the pallet limits.",
            }
            for u in result.unpacked
        ],
        outcome_category="valid" if seed else "physically_blocked",
        termination_reason="strategies_exhausted",
        message="No complete plan under the current rules. Review the affected cartons.",
    )


def publish_atomic(destination: Path, value: dict) -> None:
    """Keep complete JSON visible when a Windows reader briefly locks the file."""
    temporary = destination.with_suffix(".next")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    for attempt in range(5):
        try:
            temporary.replace(destination)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.01 * (attempt + 1))


if __name__ == "__main__":
    source, destination = map(Path, sys.argv[1:3])

    last = {}

    def publish(value):
        last.update(value)
        publish_atomic(destination, last)

    try:
        run(json.loads(source.read_text(encoding="utf-8")), publish)
    except Exception as exc:
        publish(
            {
                "error": str(exc),
                "outcome_category": "software_error",
                "message": "Packing worker failed; inspect the recorded diagnostics.",
            }
        )
        raise
