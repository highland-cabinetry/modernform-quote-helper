from app.models import ItemKind, ManualOverride, PackingRole, PackingSettings, PackingState, PlacedItem
from app.packing import StackPlannerPackingEngine
from app.packing.common import _expand_units
from app.packing.validator import RulesValidator
from test_validator import pallet

CATALOG_CSB42_HEIGHT = 31.25


def so77154_csb42(combined_factory):
    return combined_factory(
        id=77154,
        sku="CSB42",
        original_sku="GRS-CSB42",
        packing_state=PackingState.ASSEMBLED,
        length=45,
        width=45,
        height=CATALOG_CSB42_HEIGHT,
        assembled_length=45,
        assembled_width=45,
        assembled_height=CATALOG_CSB42_HEIGHT,
        weight=83.114174,
        category="Base cabinet",
        item_kind=ItemKind.CABINET,
        packing_role=PackingRole.STANDARD,
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
    )


def test_so77154_csb42_expansion_uses_approved_dimensions_and_retains_weight(combined_factory):
    units, unavailable = _expand_units([so77154_csb42(combined_factory)])

    assert unavailable == []
    assert len(units) == 1
    assert (units[0].length, units[0].width, units[0].height) == (42, 42, CATALOG_CSB42_HEIGHT)
    assert units[0].assembled_dimensions == (42, 42, CATALOG_CSB42_HEIGHT)
    assert units[0].weight == 83.114174


def test_so77154_csb42_is_centered_on_standard_deck_with_width_overhang(combined_factory):
    result = StackPlannerPackingEngine().pack(
        "SO77154",
        [so77154_csb42(combined_factory)],
        PackingSettings(),
    )

    assert result.plan_status == "ready"
    assert result.unpacked == []
    assert result.summary.total_item_weight == 83.11
    assert result.summary.total_freight_weight == 128.11
    assert result.summary.total_pallets == 1
    plan = result.pallets[0]
    item = plan.items[0]
    assert plan.pallet_type == "standard"
    assert (item.x, item.y, item.z) == (3, -1, 0)
    assert (item.length, item.width, item.height) == (42, 42, CATALOG_CSB42_HEIGHT)
    assert plan.overhang == {"left": 0.0, "right": 0.0, "front": 1.0, "back": 1.0}
    assert (plan.load_length, plan.load_width) == (48, 42)


def test_so77154_validator_rejects_wrong_overhang_or_centering():
    item = PlacedItem(
        unit_id="csb42-1",
        item_key="csb42",
        sku="CSB42",
        original_sku="GRS-CSB42",
        packing_state=PackingState.ASSEMBLED,
        description="Corner sink base",
        category="Base cabinet",
        item_kind=ItemKind.CABINET,
        source_refs=["SO77154"],
        x=2,
        y=-2,
        z=0,
        length=42,
        width=42,
        height=CATALOG_CSB42_HEIGHT,
        assembled_dimensions=(42, 42, CATALOG_CSB42_HEIGHT),
        rotation="LWH",
        weight=83.114174,
        fragile=False,
        stackable=True,
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
        max_stack_layers=None,
        max_weight_on_top=1000,
        load_bearing_strength=1000,
        color="#000000",
    )

    issues = RulesValidator().validate_pallet(pallet([item]), PackingSettings())
    assert any(issue.code == "CSB42_GEOMETRY_REQUIRED" for issue in issues)
    strict = RulesValidator().validate_pallet(pallet([item]), PackingSettings(general_overhang_per_side=0))
    assert any(issue.code == "OUT_OF_BOUNDS" for issue in strict)


def test_so77154_csb42_long_deck_overhang_follows_the_general_allowance():
    item = PlacedItem(
        unit_id="csb42-long-1",
        item_key="csb42",
        sku="CSB42",
        original_sku="GRS-CSB42",
        packing_state=PackingState.ASSEMBLED,
        description="Corner sink base",
        category="Base cabinet",
        item_kind=ItemKind.CABINET,
        source_refs=["SO77154"],
        x=27,
        y=-1,
        z=0,
        length=42,
        width=42,
        height=CATALOG_CSB42_HEIGHT,
        assembled_dimensions=(42, 42, CATALOG_CSB42_HEIGHT),
        rotation="LWH",
        weight=83.114174,
        fragile=False,
        stackable=True,
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
        max_stack_layers=None,
        max_weight_on_top=1000,
        load_bearing_strength=1000,
        color="#000000",
    )
    long_plan = pallet([item]).model_copy(
        update={
            "pallet_type": "long",
            "display_name": "Long 96 × 40",
            "length": 96,
            "tare_weight": 80,
            "total_weight": 163.114174,
        }
    )

    issues = RulesValidator().validate_pallet(long_plan, PackingSettings())
    assert not any(issue.code == "OUT_OF_BOUNDS" for issue in issues)
    strict = RulesValidator().validate_pallet(long_plan, PackingSettings(general_overhang_per_side=0))
    assert any(issue.code == "OUT_OF_BOUNDS" for issue in strict)


def test_general_overhang_is_limited_and_reported_for_other_skus(combined_factory):
    other = combined_factory(
        id=77155,
        sku="OTHER42",
        original_sku="GRS-OTHER42",
        packing_state=PackingState.ASSEMBLED,
        length=42,
        width=42,
        height=34,
        assembled_length=42,
        assembled_width=42,
        assembled_height=34,
        item_kind=ItemKind.CABINET,
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
    )

    result = StackPlannerPackingEngine().pack("Other", [other], PackingSettings())
    assert result.plan_status == "ready"
    assert result.pallets[0].load_width == 42
    assert "Protect approved overhang" in " ".join(result.pallets[0].required_actions)
    strict = StackPlannerPackingEngine().pack("Other", [other], PackingSettings(general_overhang_per_side=0))
    assert strict.plan_status == "blocked"
    assert strict.unpacked[0].reason_code == "OVERSIZE_FOOTPRINT"


def test_long_override_is_never_downgraded_or_moved(combined_factory):
    item = combined_factory(
        id=77156,
        sku="LONG-LOCKED",
        original_sku="LONG-LOCKED",
        length=60,
        width=10,
        height=10,
        weight=83.114174,
        preferred_pallet_type="long",
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
    )
    override = ManualOverride(
        unit_id="77156-0001",
        pallet_number=1,
        pallet_type="long",
        x=0,
        y=0,
        z=0,
        rotation="LWH",
    )

    result = StackPlannerPackingEngine().pack(
        "SO77154 locked long",
        [item],
        PackingSettings(),
        overrides=[override],
    )

    assert result.plan_status == "ready"
    assert result.override_outcomes[0].status == "accepted"
    assert result.pallets[0].pallet_type == "long"
    assert (result.pallets[0].items[0].x, result.pallets[0].items[0].y) == (0, 0)


def test_progress_publish_retries_transient_windows_reader_lock(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from app.packing_worker import publish_atomic
    real_replace = Path.replace
    attempts = []
    def transient(path, target):
        attempts.append(target)
        if len(attempts) < 3:
            raise PermissionError("Sharing violation")
        return real_replace(path, target)
    monkeypatch.setattr(Path, "replace", transient)
    target = tmp_path / "progress.json"
    publish_atomic(target, {"candidate": {"complete": True}})
    assert json.loads(target.read_text())["candidate"]["complete"]
    assert len(attempts) == 3


