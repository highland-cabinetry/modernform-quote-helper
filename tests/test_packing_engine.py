from app.models import ItemMaster, ManualOverride, PackingSettings, PackingState, RotationPreference
from app.packing import StackPlannerPackingEngine


def test_weight_totals_include_pallet_tare(combined_factory):
    item = combined_factory(quantity=2, weight=50, length=10, width=10, height=10)
    result = StackPlannerPackingEngine().pack("Weight", [item], PackingSettings())
    assert result.summary.total_item_weight == 100
    assert result.summary.total_pallets == 1
    assert result.pallets[0].pallet_type == "standard"
    assert result.pallets[0].total_weight == 145
    assert result.summary.total_freight_weight == 145


def test_all_placements_remain_inside_pallet_dimensions(combined_factory):
    item = combined_factory(quantity=12, length=12, width=10, height=8, weight=30)
    result = StackPlannerPackingEngine().pack("Bounds", [item], PackingSettings())
    assert not result.unpacked
    for pallet in result.pallets:
        for placed in pallet.items:
            assert placed.x >= 0 and placed.x + placed.length <= pallet.length
            assert placed.y >= 0 and placed.y + placed.width <= pallet.width
            assert placed.z >= 0 and placed.z + placed.height <= pallet.max_cargo_height


def test_max_total_height_includes_pallet_deck(combined_factory):
    tall = combined_factory(
        sku="TALL",
        height=43,
        length=20,
        width=20,
        weight=60,
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
    )
    result = StackPlannerPackingEngine().pack("Height", [tall], PackingSettings(flatpack_max_total_height=48))
    assert result.summary.packed_items == 0
    assert result.unpacked[0].reason_code == "OVERHEIGHT_ITEM"


def test_oversize_item_has_specific_unpacked_reason(combined_factory):
    oversize = combined_factory(
        sku="OVERSIZE",
        length=101,
        width=45,
        height=3,
        allowed_rotations=["LWH", "WLH"],
        must_stay_upright=True,
    )
    result = StackPlannerPackingEngine().pack("Oversize", [oversize], PackingSettings())
    assert result.summary.packed_items == 0
    assert result.unpacked[0].reason_code == "OVERSIZE_FOOTPRINT"


def test_upright_item_never_rotates_onto_side(combined_factory):
    upright = combined_factory(
        quantity=3,
        sku="UPRIGHT",
        length=20,
        width=12,
        height=18,
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
    )
    result = StackPlannerPackingEngine().pack("Upright", [upright], PackingSettings())
    assert all(item.rotation.endswith("H") for pallet in result.pallets for item in pallet.items)


def test_invalid_floating_manual_override_falls_back_to_automatic_packing(combined_factory):
    item = combined_factory()
    override = ManualOverride(unit_id="1-0001", pallet_number=1, x=0, y=0, z=8, rotation="LWH")
    result = StackPlannerPackingEngine().pack("Override", [item], PackingSettings(), overrides=[override])
    assert result.summary.packed_items == 1
    assert result.unpacked == []
    assert result.pallets[0].items[0].z == 0
    outcome = next(value for value in result.override_outcomes if value.kind == "exact_lock")
    assert outcome.status == "fallback"
    assert "support" in outcome.message.lower()


def test_manual_override_cannot_bypass_edge_standing_approval(combined_factory):
    item = combined_factory(
        must_stay_upright=False,
        allowed_rotations=["LWH", "LHW"],
        allow_edge_standing=False,
    )
    override = ManualOverride(unit_id="1-0001", pallet_number=1, x=0, y=0, z=0, rotation="LHW")
    result = StackPlannerPackingEngine().pack(
        "Override", [item], PackingSettings(flatpack_cartons_on_edge=False), overrides=[override]
    )
    assert result.summary.packed_items == 1
    assert result.unpacked == []
    assert result.pallets[0].items[0].rotation == "LWH"
    outcome = next(value for value in result.override_outcomes if value.kind == "exact_lock")
    assert outcome.status == "fallback"
    assert "not approved" in outcome.message.lower()


def test_rotation_preference_repositions_without_locking_coordinates(combined_factory):
    item = combined_factory(
        length=36,
        width=24,
        height=4,
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
    )
    result = StackPlannerPackingEngine().pack(
        "Rotation preference",
        [item],
        PackingSettings(),
        rotation_preferences=[RotationPreference(unit_id="1-0001", rotation="WLH")],
    )
    placed_item = result.pallets[0].items[0]
    assert placed_item.rotation == "WLH"
    assert not placed_item.locked
    assert result.unpacked == []
    assert any(
        outcome.kind == "rotation_preference" and outcome.status == "accepted"
        for outcome in result.override_outcomes
    )


def test_non_stackable_fragile_item_is_kept_clear_above(combined_factory):
    fragile = combined_factory(
        sku="GLASS",
        id=2,
        length=20,
        width=20,
        height=4,
        weight=10,
        fragile=True,
        stackable=False,
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
        max_stack_layers=1,
        max_weight_on_top=0,
        load_bearing_strength=0,
    )
    heavy = combined_factory(sku="HEAVY", id=3, length=20, width=20, height=10, weight=100)
    result = StackPlannerPackingEngine().pack("Fragile", [fragile, heavy], PackingSettings())
    glass = next(item for pallet in result.pallets for item in pallet.items if item.sku == "GLASS")
    assert not any(
        item.sku == "HEAVY" and item.z >= glass.z + glass.height for pallet in result.pallets for item in pallet.items
    )
    assert not any(issue.code == "HEAVY_ABOVE_FRAGILE" for issue in result.warnings)


def test_flatpack_and_assembled_states_use_their_own_dimensions(combined_factory):
    flatpack = combined_factory(
        id=10,
        original_sku="WHS-B09",
        length=36,
        width=24.8,
        height=3.8888889,
        assembled_length=36,
        assembled_width=10,
        assembled_height=25,
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
    )
    assembled = combined_factory(
        id=10,
        original_sku="WHS-B09",
        packing_state=PackingState.ASSEMBLED,
        length=36,
        width=24.8,
        height=3.8888889,
        assembled_length=36,
        assembled_width=10,
        assembled_height=25,
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
    )
    flat_result = StackPlannerPackingEngine().pack("Flat", [flatpack], PackingSettings())
    assembled_result = StackPlannerPackingEngine().pack("Assembled", [assembled], PackingSettings())
    flat_box = flat_result.pallets[0].items[0]
    assembled_box = assembled_result.pallets[0].items[0]
    assert (sorted((flat_box.length, flat_box.width)), flat_box.height) == ([24.8, 36], 3.8888889)
    assert (sorted((assembled_box.length, assembled_box.width)), assembled_box.height) == ([10, 36], 25)
    assert assembled_box.packing_state.value == "assembled"
    assert assembled_box.flatpack_dimensions == (36, 24.8, 3.8888889)
    assert assembled_box.assembled_dimensions == (36, 10, 25)


def test_missing_selected_dimension_set_is_unpacked_without_fallback(combined_factory):
    assembled = combined_factory(packing_state=PackingState.ASSEMBLED, length=10, width=10, height=5)
    result = StackPlannerPackingEngine().pack("Missing", [assembled], PackingSettings())
    assert result.summary.total_items == 1
    assert result.summary.packed_items == 0
    assert result.summary.total_item_weight == 25
    assert result.unpacked[0].reason_code == "MISSING_ASSEMBLED_DIMENSIONS"
    assert result.unpacked[0].packing_state.value == "assembled"


def test_legacy_item_dimensions_migrate_to_flatpack_only():
    item = ItemMaster(sku="LEGACY", length=10, width=8, height=4, weight=12)
    assert item.dimensions_for(PackingState.FLATPACK) == (10, 8, 4)
    assert item.dimensions_for(PackingState.ASSEMBLED) is None


def test_b18_rotates_ninety_degrees_to_use_open_deck_strip(combined_factory):
    blocker = combined_factory(
        id=1,
        sku="LOCKED-BASE",
        length=66,
        width=40,
        height=4,
        weight=200,
        must_stay_upright=True,
        allowed_rotations=["LWH"],
        preferred_pallet_type="long",
    )
    b18 = combined_factory(
        id=2,
        sku="B18",
        original_sku="WHS-B18",
        length=36,
        width=24.8,
        height=4.714,
        weight=61.9,
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
    )
    override = ManualOverride(
        unit_id="1-0001", pallet_number=1, pallet_type="long", x=0, y=0, z=0, rotation="LWH"
    )

    result = StackPlannerPackingEngine().pack(
        "B18 rotation", [blocker, b18], PackingSettings(), overrides=[override]
    )

    placed = next(item for pallet in result.pallets for item in pallet.items if item.sku == "B18")
    assert placed.rotation == "WLH"
    assert (placed.length, placed.width) == (24.8, 36)
    assert placed.z == 0
