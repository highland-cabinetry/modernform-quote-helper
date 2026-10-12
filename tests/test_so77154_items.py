from types import SimpleNamespace

from app.models import ItemMaster, MatchStatus, PackingSlipLine, PackingState
from app.services.item_accounting import (
    TRASH_KIT_CAPACITY_CODE,
    TRASH_KIT_NO_HOST_CODE,
    prepare_units,
)
from app.services.product_exceptions import CSB42_RULE, apply_product_exceptions
from app.services.sku_matching import (
    TRASH_KIT_CURATED_ID,
    TRASH_KIT_HEIGHT,
    TRASH_KIT_LENGTH,
    TRASH_KIT_WEIGHT,
    TRASH_KIT_WIDTH,
    match_lines,
)


def _line(**overrides):
    values = dict(
        source_ref="SO77154 · page 1",
        original_sku="Back-Order(Shaker)",
        quantity=1,
        packing_state=PackingState.ASSEMBLED,
        description="TRASH KIT",
    )
    values.update(overrides)
    return PackingSlipLine(**values)


def _master(sku, *, item_id=1, weight=61.9, assembled=(36, 24.8, 25)):
    return ItemMaster(
        id=item_id,
        sku=sku,
        description=sku,
        flatpack_length=36,
        flatpack_width=24.8,
        flatpack_height=4.7,
        assembled_length=assembled[0],
        assembled_width=assembled[1],
        assembled_height=assembled[2],
        weight=weight,
        category="Cabinetry",
    )


def _unit(*, unit_id, sku, original_sku=None, source_ref="SO77154 · page 1", weight=18.0):
    return SimpleNamespace(
        unit_id=unit_id,
        item_key=unit_id,
        sku=sku,
        original_sku=original_sku or sku,
        packing_state=PackingState.ASSEMBLED,
        description="TRASH KIT" if "TRASH" in sku else sku,
        source_refs=[source_ref],
        length=18.0 if "TRASH" in sku else 36.0,
        width=14.0 if "TRASH" in sku else 24.8,
        height=3.0 if "TRASH" in sku else 25.0,
        weight=weight if "TRASH" in sku else 61.9,
        contained_items=[],
    )


def test_so77154_trash_description_never_fuzzy_matches_b42():
    b42 = _master("B42", item_id=42)
    matched = match_lines(
        [_line()],
        [b42],
        ignored_prefixes=["WHS"],
        fuzzy_threshold=1,
        confirmed_aliases={"BACKORDERSHAKER": b42},
    )[0]

    assert matched.match_status == MatchStatus.CONFIRMED_ALIAS
    assert matched.matched_item is not None
    assert matched.matched_item.sku == "TRASH KIT"
    assert matched.matched_item.id == TRASH_KIT_CURATED_ID
    assert matched.matched_item.weight == TRASH_KIT_WEIGHT
    assert matched.matched_item.dimensions_for(PackingState.FLATPACK) == (
        TRASH_KIT_LENGTH,
        TRASH_KIT_WIDTH,
        TRASH_KIT_HEIGHT,
    )


def test_b18_description_note_does_not_turn_a_cabinet_into_trash():
    b18 = _master("B18", item_id=18)
    matched = match_lines(
        [_line(original_sku="B18", description="B18 cabinet with trash kit option")],
        [b18],
        ignored_prefixes=[],
        fuzzy_threshold=1,
    )[0]

    assert matched.match_status == MatchStatus.EXACT
    assert matched.matched_item is not None
    assert matched.matched_item.sku == "B18"


def test_genuine_exact_trash_master_weight_is_preserved():
    trash = _master("TRASH KIT", item_id=7, weight=18, assembled=(18, 14, 3))
    trash.flatpack_length, trash.flatpack_width, trash.flatpack_height = 18, 14, 3
    matched = match_lines(
        [_line()], [trash], ignored_prefixes=[], fuzzy_threshold=90
    )[0]

    assert matched.match_status == MatchStatus.CONFIRMED_ALIAS
    assert matched.matched_item is not None
    assert matched.matched_item.id == 7
    assert matched.matched_item.weight == 18
    assert matched.review_message and "resolved" in matched.review_message


def test_conflicting_exact_trash_master_is_blocked_for_review():
    trash = _master("TRASH KIT", item_id=7, weight=22.5, assembled=(18, 14, 3))
    matched = match_lines(
        [_line()], [trash], ignored_prefixes=[], fuzzy_threshold=90
    )[0]

    assert matched.match_status == MatchStatus.UNMATCHED
    assert matched.matched_item is None
    assert matched.review_message and "differs" in matched.review_message


def test_source_authored_approved_trash_dimensions_build_manual_master():
    matched = match_lines(
        [_line(original_sku="TRASH KIT", length=18, width=14, height=3, weight=18)],
        [],
        ignored_prefixes=[],
        fuzzy_threshold=90,
    )[0]

    assert matched.match_status == MatchStatus.MANUAL
    assert matched.matched_item is not None
    assert matched.matched_item.dimensions_for(PackingState.FLATPACK) == (18, 14, 3)
    assert matched.matched_item.dimensions_for(PackingState.ASSEMBLED) == (18, 14, 3)
    assert matched.matched_item.weight == 18


def test_unverified_trash_without_weight_is_actionable_unmatched():
    line = _line(original_sku="TRASH ACCESSORY", description="Waste accessory")
    matched = match_lines([line], [_master("B42", item_id=42)], ignored_prefixes=[], fuzzy_threshold=1)[0]

    assert matched.match_status == MatchStatus.UNMATCHED
    assert matched.matched_item is None
    assert matched.review_message


def test_apply_product_exceptions_isolated_csb42_preview_correction():
    original = _master("CSB42", item_id=42, assembled=(36, 24.8, 31.25), weight=120)
    corrected = apply_product_exceptions(original)

    assert corrected is not original
    assert corrected.dimensions_for(PackingState.ASSEMBLED) == (
        CSB42_RULE.assembled_length,
        CSB42_RULE.assembled_width,
        31.25,
    )
    assert corrected.weight == original.weight
    assert original.dimensions_for(PackingState.ASSEMBLED) == (36, 24.8, 31.25)


def test_assembled_trash_is_contained_once_and_weight_is_idempotent():
    host = _unit(unit_id="b18-1", sku="B18", weight=61.9)
    trash = _unit(unit_id="trash-1", sku="TRASH KIT", original_sku="Back-Order(Shaker)", weight=18)
    physical, unavailable = prepare_units([host, trash])

    assert physical == [host]
    assert unavailable == []
    assert host.weight == 79.9
    assert len(host.contained_items) == 1
    assert host.contained_items[0].host_base_weight == 61.9

    physical_again, unavailable_again = prepare_units([host, trash])
    assert physical_again == [host]
    assert unavailable_again == []
    assert host.weight == 79.9
    assert len(host.contained_items) == 1


def test_trash_prefers_same_source_scope_and_enforces_one_per_host():
    other_host = _unit(unit_id="b18-other", sku="B18", source_ref="SO77153 · page 1", weight=60)
    same_host = _unit(unit_id="b18-same", sku="B18", source_ref="SO77154 · page 2", weight=62)
    trash_one = _unit(unit_id="trash-1", sku="TRASH KIT", source_ref="SO77154 · page 1")
    trash_two = _unit(unit_id="trash-2", sku="TRASH KIT", source_ref="SO77154 · page 1")

    physical, unavailable = prepare_units([other_host, same_host, trash_one, trash_two])

    assert physical == [other_host, same_host]
    assert [item.host_unit_id for item in same_host.contained_items] == ["b18-same"]
    assert [item.host_unit_id for item in other_host.contained_items] == ["b18-other"]
    assert unavailable == []


def test_trash_capacity_is_one_kit_per_host():
    host = _unit(unit_id="b18-only", sku="B18")
    trash_one = _unit(unit_id="trash-1", sku="TRASH KIT")
    trash_two = _unit(unit_id="trash-2", sku="TRASH KIT")

    _, unavailable = prepare_units([host, trash_one, trash_two])

    assert len(host.contained_items) == 1
    assert len(unavailable) == 1
    assert unavailable[0].reason_code == TRASH_KIT_CAPACITY_CODE


def test_assembled_trash_without_b18_is_unpacked_and_flatpack_stays_physical():
    trash = _unit(unit_id="trash-1", sku="TRASH KIT", weight=18)
    flatpack = _unit(unit_id="trash-flat", sku="TRASH KIT", weight=18)
    flatpack.packing_state = PackingState.FLATPACK
    physical, unavailable = prepare_units([trash, flatpack])

    assert physical == [flatpack]
    assert len(unavailable) == 1
    assert unavailable[0].reason_code == TRASH_KIT_NO_HOST_CODE


def test_real_products_with_trash_kit_notes_remain_products():
    for sku in ["3DB12", "WBC3042", "WF330", "B18"]:
        master = _master(sku)
        result = match_lines([_line(original_sku=sku, description="Cabinet with TRASH KIT option")],
                             [master], ignored_prefixes=[], fuzzy_threshold=90)[0]
        assert result.match_status == MatchStatus.EXACT
        assert result.matched_item.sku == sku


def test_exact_kit_master_with_wrong_carton_dimensions_is_blocked():
    master = _master("TRASH KIT", weight=18, assembled=(18,14,3))
    result = match_lines([_line(packing_state=PackingState.FLATPACK)], [master],
                         ignored_prefixes=[], fuzzy_threshold=90)[0]
    assert result.match_status == MatchStatus.UNMATCHED
    assert "dimensions" in result.review_message
