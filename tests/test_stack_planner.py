"""Behaviour of the stack planner (production engine 3.x)."""

import json
from pathlib import Path

from app.models import (
    CombinedItem,
    ItemKind,
    ItemMaster,
    ManualOverride,
    PackingSettings,
    PackingState,
    Severity,
    SourceReference,
)
from app.packing import StackPlannerPackingEngine
from app.packing.validator import RulesValidator

FIXTURE = Path(__file__).parent / "fixtures" / "mixed_65_unit_warehouse.json"


def item(factory, sku, length, width, height, weight, quantity=1, item_id=None, **extra):
    defaults = dict(must_stay_upright=True, allowed_rotations=["LWH", "WLH"], max_weight_on_top=1000,
                    load_bearing_strength=1000, category="Base cabinet", item_kind=ItemKind.CABINET)
    defaults.update(extra)
    return factory(id=item_id or abs(hash(sku)) % 100000, sku=sku, length=length, width=width, height=height,
                   weight=weight, quantity=quantity, **defaults)


def errors(result):
    return [w for w in result.warnings if w.severity == Severity.ERROR]


def assert_clean(result):
    assert result.plan_status == "ready", [w.message for w in errors(result)]
    assert result.freight_ready
    assert not result.unpacked
    assert not result.accounting_errors
    settings = PackingSettings()
    assert not [i for i in RulesValidator().validate(result.pallets, settings) if i.severity == Severity.ERROR]


def test_small_order_goes_on_a_standard_pallet(combined_factory):
    result = StackPlannerPackingEngine().pack(
        "small", [item(combined_factory, "B18", 36, 24.8, 4.7, 62, quantity=2)], PackingSettings()
    )
    assert_clean(result)
    assert [p.pallet_type for p in result.pallets] == ["standard"]


def test_96_inch_items_force_a_long_pallet_and_share_it_with_cabinets(combined_factory):
    items = [
        item(combined_factory, "PNL 34.5x96", 96, 34.5, 0.75, 26, quantity=2, item_id=1,
             category="Accessory", item_kind=ItemKind.ACCESSORY),
        item(combined_factory, "B30", 36, 31.1, 4.7, 80, quantity=6, item_id=2),
        item(combined_factory, "TK8", 96, 4, 0.125, 2.3, quantity=4, item_id=3,
             category="Accessory", item_kind=ItemKind.ACCESSORY),
    ]
    result = StackPlannerPackingEngine().pack("long", items, PackingSettings())
    assert_clean(result)
    assert [p.pallet_type for p in result.pallets] == ["long"]


def test_flatpack_order_uses_fewest_pallets_without_runts(combined_factory):
    # 36 base cartons (~170 in of stacked thickness) need two long pallets at most.
    items = [item(combined_factory, "B30", 36, 31.1, 4.7, 80, quantity=36, item_id=1),
             item(combined_factory, "PNL", 96, 34.5, 0.75, 26, quantity=1, item_id=2,
                  category="Accessory", item_kind=ItemKind.ACCESSORY)]
    result = StackPlannerPackingEngine().pack("balanced", items, PackingSettings())
    assert_clean(result)
    heights = [p.freight_height for p in result.pallets]
    assert len(result.pallets) <= 2
    assert max(heights) <= 66 + 1e-6
    assert min(heights) >= 0.5 * max(heights), heights


def test_height_limits_depend_on_packing_state(combined_factory):
    flat = item(combined_factory, "B30", 36, 31.1, 4.7, 80, quantity=30, item_id=1)
    result = StackPlannerPackingEngine().pack("flat", [flat], PackingSettings())
    assert_clean(result)
    assert all(p.freight_height <= 66 + 1e-6 and p.max_total_height == 66 for p in result.pallets)
    assembled = item(combined_factory, "B30A", 36, 31, 25, 100, quantity=9, item_id=2,
                     packing_state=PackingState.ASSEMBLED, assembled_length=36, assembled_width=31,
                     assembled_height=25)
    result = StackPlannerPackingEngine().pack("assembled", [assembled], PackingSettings())
    assert_clean(result)
    assert all(p.max_total_height == 80 for p in result.pallets)
    # Three assembled bases high (6 + 3 x 25 = 81 in) exceeds 80; two high is the build.
    assert all(p.freight_height <= 80 + 1e-6 for p in result.pallets)


def test_face_frame_cartons_ride_on_top(combined_factory):
    items = [
        item(combined_factory, "B30", 36, 31.1, 4.7, 80, quantity=4, item_id=1),
        item(combined_factory, "B30FFD", 36, 31.1, 1.8, 12.3, quantity=4, item_id=2, fragile=True,
             max_weight_on_top=100, load_bearing_strength=100, category="Face Frame and Door",
             item_kind=ItemKind.CABINET_COMPONENT),
    ]
    result = StackPlannerPackingEngine().pack("doors", items, PackingSettings())
    assert_clean(result)
    placed = [i for p in result.pallets for i in p.items]
    lowest_door = min(i.z for i in placed if i.fragile)
    assert all(i.z < lowest_door for i in placed if not i.fragile)


def test_pantry_box_and_doors_stay_together_with_doors_above(combined_factory):
    items = [
        item(combined_factory, "PC189024BOX", 91.6, 24.4, 2.8, 114, item_id=1, category="Tall Cabinet"),
        item(combined_factory, "PC189024FFD", 87.2, 18.9, 1.8, 30, item_id=2, fragile=True,
             max_weight_on_top=100, load_bearing_strength=100, category="Face Frame and Door",
             item_kind=ItemKind.CABINET_COMPONENT),
        item(combined_factory, "B36", 34, 37, 5.1, 100, quantity=6, item_id=3),
    ]
    result = StackPlannerPackingEngine().pack("pantry", items, PackingSettings())
    assert_clean(result)
    assert len(result.pallets) == 1
    box = next(i for i in result.pallets[0].items if i.sku.endswith("BOX"))
    ffd = next(i for i in result.pallets[0].items if i.sku.endswith("FFD"))
    assert ffd.z >= box.z + box.height - 1e-6
    # Cabinets above the door carton rest on a load board, never on the doors.
    assert result.pallets[0].load_boards
    assert result.pallets[0].tare_weight > PackingSettings().long_pallet.tare_weight


def test_load_board_weight_is_tare_not_an_order_item(combined_factory):
    items = [
        item(combined_factory, "PC189024BOX", 91.6, 24.4, 2.8, 114, item_id=1, category="Tall Cabinet"),
        item(combined_factory, "B36", 34, 37, 5.1, 100, quantity=3, item_id=3),
    ]
    settings = PackingSettings(load_board_weight=40, long_items_position="bottom")
    result = StackPlannerPackingEngine().pack("board", items, settings)
    assert_clean(result)
    pallet = result.pallets[0]
    boards = len(pallet.load_boards)
    assert boards >= 1
    assert pallet.tare_weight == settings.long_pallet.tare_weight + 40 * boards
    assert result.summary.total_items == 4
    assert any("load board" in action for action in pallet.required_actions)


def test_overhang_is_only_used_when_needed(combined_factory):
    result = StackPlannerPackingEngine().pack(
        "fits", [item(combined_factory, "VB15", 36, 22, 3.6, 53, quantity=6)], PackingSettings()
    )
    assert_clean(result)
    assert all(v == 0 for p in result.pallets for v in p.overhang.values())
    wide = StackPlannerPackingEngine().pack(
        "wide", [item(combined_factory, "W4242", 43.5, 42.9, 3.9, 87)], PackingSettings()
    )
    assert_clean(wide)
    assert wide.pallets[0].load_width <= 44 + 1e-6


def test_mixed_65_unit_fixture_matches_the_warehouse_target():
    data = json.loads(FIXTURE.read_text())
    settings = PackingSettings.model_validate(data["settings"])
    items = []
    for index, row in enumerate(data["items"], start=1):
        row = dict(row)
        quantity = row.pop("quantity")
        state = PackingState(row.pop("packing_state"))
        master = ItemMaster.model_validate(row)
        items.append(CombinedItem(
            key=str(index), master_item=master, packing_state=state, quantity=quantity,
            sources=[SourceReference(source_ref="SYNTHETIC", original_sku=master.sku, quantity=quantity)],
            original_skus=[master.sku],
        ))
    result = StackPlannerPackingEngine().pack("mixed 65", items, settings)
    assert result.freight_ready, [w.message for w in errors(result)]
    assert result.summary.packed_items == 65
    # Historical V2 used three pallets; the warehouse target is one long + one standard.
    assert len(result.pallets) <= 2


def test_plans_are_deterministic(combined_factory):
    items = [item(combined_factory, "B30", 36, 31.1, 4.7, 80, quantity=12, item_id=1),
             item(combined_factory, "W3030", 31.7, 31.1, 3.9, 50, quantity=9, item_id=2, category="Wall cabinet")]
    first = StackPlannerPackingEngine().pack("same", items, PackingSettings())
    second = StackPlannerPackingEngine().pack("same", items, PackingSettings())
    shape = lambda r: [(p.pallet_type, [(i.unit_id, i.x, i.y, i.z, i.rotation) for i in p.items])  # noqa: E731
                       for p in r.pallets]
    assert shape(first) == shape(second)


def test_valid_lock_is_applied_and_invalid_lock_falls_back(combined_factory):
    cartons = item(combined_factory, "B30", 36, 31.1, 4.7, 80, quantity=2, item_id=7)
    good = ManualOverride(unit_id="7-0002", pallet_number=1, x=0, y=0, z=0, rotation="LWH")
    base = StackPlannerPackingEngine().pack("auto", [cartons], PackingSettings())
    occupied = {(i.x, i.y, i.z) for i in base.pallets[0].items if i.unit_id == "7-0001"}
    result = StackPlannerPackingEngine().pack("lock", [cartons], PackingSettings(), overrides=[good])
    outcome = result.override_outcomes[0]
    if (0, 0, 0) in occupied:
        assert outcome.status == "fallback"
    else:
        assert outcome.status == "accepted"
    assert_clean(result)
    floating = ManualOverride(unit_id="7-0002", pallet_number=1, x=0, y=0, z=30, rotation="LWH")
    result = StackPlannerPackingEngine().pack("float", [cartons], PackingSettings(), overrides=[floating])
    assert result.override_outcomes[0].status == "fallback"
    assert_clean(result)


def test_rotation_preference_is_honoured_when_it_fits(combined_factory):
    cartons = item(combined_factory, "B24", 36, 25.2, 4.7, 71, quantity=1, item_id=8)
    from app.models import RotationPreference
    result = StackPlannerPackingEngine().pack(
        "pref", [cartons], PackingSettings(), rotation_preferences=[RotationPreference(unit_id="8-0001",
                                                                                     rotation="WLH")]
    )
    assert_clean(result)
    assert result.pallets[0].items[0].rotation == "WLH"


def test_deck_is_tiled_with_the_carton_type_that_fits_best(combined_factory):
    # One 31" carton would leave a 17" strip on a 48" deck; 25" cartons fit two across.
    items = [
        item(combined_factory, "VSB30", 36, 31.1, 3.6, 54, item_id=1, category="Vanity base cabinet"),
        item(combined_factory, "VSB24", 36, 25.2, 3.6, 48, quantity=8, item_id=2, category="Vanity base cabinet"),
    ]
    settings = PackingSettings(flatpack_cartons_on_edge=False)
    result = StackPlannerPackingEngine().pack("tiling", items, settings)
    assert_clean(result)
    assert [p.pallet_type for p in result.pallets] == ["standard"]
    bases = [i for i in result.pallets[0].items if i.z == 0]
    assert len(bases) >= 2
    assert result.pallets[0].freight_height <= 6 + 5 * 3.6 + 1e-6


def test_flatpack_cabinet_cartons_stand_on_edge_in_narrow_strips(combined_factory):
    # A 31" pile on a 48" deck leaves a 17" strip that no flat carton fits.
    items = [item(combined_factory, "B30", 36, 31.1, 4.7, 80, quantity=8, item_id=1)]
    flat = StackPlannerPackingEngine().pack("flat", items, PackingSettings(flatpack_cartons_on_edge=False))
    edge = StackPlannerPackingEngine().pack("edge", items, PackingSettings())
    assert_clean(flat)
    assert_clean(edge)
    standing = [i for p in edge.pallets for i in p.items if not i.rotation.endswith("H")]
    assert standing and all(i.allow_edge_standing and i.item_kind == ItemKind.CABINET for i in standing)
    assert [p.pallet_type for p in edge.pallets] == ["standard"]
    assert max(p.freight_height for p in edge.pallets) < max(p.freight_height for p in flat.pallets)
    assert any(w.code == "EDGE_STANDING_RESTRAINT_REQUIRED" for w in edge.warnings)


def test_door_cartons_and_accessories_never_stand_on_edge(combined_factory):
    items = [
        item(combined_factory, "B30", 36, 31.1, 4.7, 80, quantity=6, item_id=1),
        item(combined_factory, "B24FFD", 36, 25.2, 1.8, 10, quantity=12, item_id=2, fragile=True,
             max_weight_on_top=100, load_bearing_strength=100, category="Face Frame and Door",
             item_kind=ItemKind.CABINET_COMPONENT),
        item(combined_factory, "WF330", 30, 3, 0.75, 1.5, quantity=6, item_id=3,
             category="Accessory", item_kind=ItemKind.ACCESSORY),
    ]
    result = StackPlannerPackingEngine().pack("no edge", items, PackingSettings())
    assert_clean(result)
    assert all(i.rotation.endswith("H") for p in result.pallets for i in p.items if i.sku != "B30")


def test_default_pallet_weight_limits_are_1200_standard_and_1600_long():
    settings = PackingSettings()
    assert settings.standard_pallet.max_total_weight == 1200
    assert settings.long_pallet.max_total_weight == 1600
    assert settings.truckload_standard_max_weight == 2400
    assert settings.truckload_long_max_weight == 1600


def truckload_settings():
    from app.models import PackingRequest
    from app.services.shipments import effective_settings

    request = PackingRequest.model_construct(settings=PackingSettings(), optimizer="truckload")
    return effective_settings(request)


def test_dedicated_truck_optimizer_uses_less_floor_and_heavier_short_pallets(combined_factory):
    from app.services.shipments import truck_floor

    items = [item(combined_factory, "B24", 24.8, 23.5, 4.7, 70, quantity=40, item_id=1)]
    standard = StackPlannerPackingEngine().pack("ltl", items, PackingSettings())
    assert_clean(standard)
    assert [p.pallet_type for p in standard.pallets] == ["long", "long"]

    settings = truckload_settings()
    assert settings.standard_pallet.max_total_weight == 2400
    engine = StackPlannerPackingEngine()
    engine.objective = "truckload"
    truck = engine.pack("truck", items, settings)
    assert truck.plan_status == "ready" and not truck.unpacked
    assert not [i for i in RulesValidator().validate(truck.pallets, settings) if i.severity == Severity.ERROR]
    assert [p.pallet_type for p in truck.pallets] == ["standard", "standard"]
    assert max(p.total_weight for p in truck.pallets) > 1200
    assert all(p.total_weight <= 2400 for p in truck.pallets)
    assert truck_floor(truck)["linear_inches"] == 40 < truck_floor(standard)["linear_inches"] == 96
    assert truck_floor(truck)["fits"] == "26 ft box truck"


def test_dedicated_truck_optimizer_may_add_pallets_to_shorten_the_floor(combined_factory):
    items = [item(combined_factory, "W3030", 31, 13, 4.5, 40, quantity=60, item_id=1)]
    standard = StackPlannerPackingEngine().pack("ltl", items, PackingSettings())
    engine = StackPlannerPackingEngine()
    engine.objective = "truckload"
    truck = engine.pack("truck", items, truckload_settings())
    assert truck.plan_status == "ready" and not truck.unpacked
    floor = StackPlannerPackingEngine.floor_inches
    assert floor([p.pallet_type for p in truck.pallets]) < floor([p.pallet_type for p in standard.pallets])


def test_dedicated_truck_request_reports_truck_floor_end_to_end():
    import io

    from app.models import PackingRequest, PackingSlipLine
    from app.packing_worker import SnapshotRepository, run
    from app.services.reports import packing_result_xlsx
    from app.services.shipments import build_shipment
    from openpyxl import load_workbook

    master = ItemMaster(id=24, sku="B24", flatpack_length=24.8, flatpack_width=23.5, flatpack_height=4.7,
                        weight=70, item_kind="cabinet", category="Base cabinet")
    request = PackingRequest(
        shipment_name="bulk", save_result=False, optimizer="truckload",
        lines=[PackingSlipLine(original_sku="B24", packing_state=PackingState.FLATPACK, quantity=40,
                               source_ref="TEST")],
    )
    snapshot = {"master": [master.model_dump(mode="json")], "aliases": {}}
    result = build_shipment(request, SnapshotRepository(snapshot))
    assert result.freight_ready, result.accounting_errors
    assert result.optimizer == "truckload"
    assert result.truck_load["linear_inches"] == 40
    assert result.truck_load["fits"] == "26 ft box truck"
    sheet = load_workbook(io.BytesIO(packing_result_xlsx(result)))["Shipment Summary"]
    labels = [row[0] for row in sheet.iter_rows(values_only=True)]
    assert "Optimizer" in labels and "Truck floor" in labels

    published = {}
    run({"request": request.model_dump(mode="json"), **snapshot}, published.update)
    assert published["outcome_category"] == "valid", published.get("validation_errors")
    assert published["candidate"]["truck_load"]["linear_inches"] == 40
