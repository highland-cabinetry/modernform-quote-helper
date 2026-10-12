from app.models import PackingSettings, PalletPlan, PlacedItem, Severity
from app.packing.validator import RulesValidator


def placed(unit_id: str, *, x=0, y=0, z=0, weight=20, fragile=False, stackable=True, max_weight_on_top=None,
           rules_reviewed=False):
    return PlacedItem(
        unit_id=unit_id,
        item_key=unit_id,
        sku=unit_id,
        original_sku=unit_id,
        description="Test",
        category="Test",
        source_refs=["TEST"],
        x=x,
        y=y,
        z=z,
        length=10,
        width=10,
        height=10,
        rotation="LWH",
        weight=weight,
        fragile=fragile,
        stackable=stackable,
        must_stay_upright=False,
        allowed_rotations=["LWH", "WLH", "LHW", "HLW", "WHL", "HWL"],
        max_stack_layers=8,
        max_weight_on_top=max_weight_on_top if max_weight_on_top is not None else (0 if fragile else 500),
        load_bearing_strength=0 if fragile else 500,
        color="#000000",
        rules_reviewed=rules_reviewed,
    )


def pallet(items):
    return PalletPlan(
        pallet_number=1,
        pallet_type="standard",
        display_name="Standard",
        length=48,
        width=40,
        max_total_height=48,
        deck_height=6,
        max_cargo_height=42,
        stack_height=max(item.z + item.height for item in items),
        freight_height=6 + max(item.z + item.height for item in items),
        item_weight=sum(item.weight for item in items),
        tare_weight=45,
        total_weight=45 + sum(item.weight for item in items),
        max_total_weight=2500,
        used_volume_percent=5,
        items=items,
        item_quantities={item.sku: 1 for item in items},
        center_of_gravity=(10, 10, 10),
    )


def test_collision_detection_reports_both_units():
    plan = pallet([placed("A"), placed("B", x=5, y=5)])
    issues = RulesValidator().validate_pallet(plan, PackingSettings())
    collision = next(issue for issue in issues if issue.code == "COLLISION")
    assert set(collision.unit_ids) == {"A", "B"}


def test_floating_box_fails_support_validation():
    plan = pallet([placed("A"), placed("B", x=20, y=20, z=10)])
    issues = RulesValidator().validate_pallet(plan, PackingSettings())
    assert any(issue.code == "INSUFFICIENT_SUPPORT" and issue.unit_ids == ["B"] for issue in issues)


def test_load_fill_uses_current_load_envelope_and_excludes_deck_height():
    item = placed("A")
    item.height = 5
    plan = pallet([item])
    plan.load_length = 20
    plan.load_width = 10
    plan.stack_height = 10

    assert plan.used_volume_percent == 5
    assert plan.load_fill_percent == 25


def test_load_fill_is_zero_when_load_envelope_has_no_height():
    plan = pallet([placed("A")])
    plan.items = []
    plan.load_length = 48
    plan.load_width = 40
    plan.stack_height = 0

    assert plan.load_fill_percent == 0


def test_heavy_above_fragile_is_an_error():
    plan = pallet([placed("GLASS", fragile=True, stackable=False), placed("HEAVY", z=10, weight=100)])
    issues = RulesValidator().validate_pallet(plan, PackingSettings())
    assert any(issue.code == "HEAVY_ABOVE_FRAGILE" for issue in issues)


def test_reviewed_top_load_limit_is_an_error_and_unreviewed_default_is_flagged():
    reviewed = pallet([placed("BASE", max_weight_on_top=50, rules_reviewed=True), placed("TOP", z=10, weight=100)])
    issues = RulesValidator().validate_pallet(reviewed, PackingSettings())
    assert any(i.code == "TOP_LOAD_EXCEEDED" and i.severity == Severity.ERROR for i in issues)
    default = pallet([placed("BASE", max_weight_on_top=50), placed("TOP", z=10, weight=100)])
    issues = RulesValidator().validate_pallet(default, PackingSettings())
    assert not any(i.code == "TOP_LOAD_EXCEEDED" for i in issues)
    assert any(i.code == "TOP_LOAD_UNREVIEWED" and i.severity == Severity.WARNING for i in issues)
