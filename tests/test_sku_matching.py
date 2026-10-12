from app.models import ItemMaster, PackingSlipLine
from app.services.sku_matching import combine_matches, match_lines, normalize_sku

PREFIXES = ["MB", "WHS", "ESS", "GRS", "MSL", "MTS", "ONB", "SBS", "SDW", "SSG"]


def make_master(sku: str, item_id: int = 1) -> ItemMaster:
    return ItemMaster(
        id=item_id,
        sku=sku,
        description="Cabinet",
        length=30,
        width=24,
        height=6,
        weight=60,
    )


def test_sku_normalization_ignores_known_prefixes_only():
    assert normalize_sku("  WHS-B30 ", PREFIXES) == "B30"
    assert normalize_sku("MB_WHS-B30", PREFIXES) == "B30"
    assert normalize_sku("CUSTOM-B30", PREFIXES) == "CUSTOMB30"
    assert normalize_sku("MB30", PREFIXES) == "MB30"


def test_exact_normalized_match_wins_before_fuzzy():
    lines = [PackingSlipLine(source_ref="SO-1", original_sku="WHS-B30", quantity=1)]
    matches = match_lines(
        lines, [make_master("B30"), make_master("B36", 2)], ignored_prefixes=PREFIXES, fuzzy_threshold=50
    )
    assert matches[0].match_status.value == "exact"
    assert matches[0].matched_item.sku == "B30"
    assert matches[0].confidence == 100


def test_fuzzy_match_is_flagged_for_review():
    lines = [PackingSlipLine(source_ref="SO-1", original_sku="B3O", quantity=1)]
    matches = match_lines(lines, [make_master("B30")], ignored_prefixes=PREFIXES, fuzzy_threshold=60)
    assert matches[0].match_status.value == "fuzzy_review"
    assert "Review required" in matches[0].review_message


def test_duplicate_skus_combine_and_preserve_sources_and_originals():
    lines = [
        PackingSlipLine(source_ref="SO-1", original_sku="WHS-B30", quantity=2),
        PackingSlipLine(source_ref="SO-2", original_sku="ESS-B30", quantity=1),
    ]
    matches = match_lines(lines, [make_master("B30")], ignored_prefixes=PREFIXES, fuzzy_threshold=88)
    combined, review = combine_matches(matches, allow_fuzzy=False)
    assert not review
    assert len(combined) == 1
    assert combined[0].quantity == 3
    assert {source.source_ref for source in combined[0].sources} == {"SO-1", "SO-2"}
    assert set(combined[0].original_skus) == {"WHS-B30", "ESS-B30"}


def test_all_color_prefixes_and_manufacturer_sku_are_exact_aliases():
    master = make_master("B30")
    master.manufacturer_sku = "MFG-B30"
    lines = [PackingSlipLine(source_ref="SO", original_sku=f"{prefix}-B30") for prefix in PREFIXES[1:]]
    lines.append(PackingSlipLine(source_ref="SO", original_sku="MFG-B30"))
    matches = match_lines(lines, [master], ignored_prefixes=PREFIXES, fuzzy_threshold=99)
    assert all(match.match_status.value == "exact" for match in matches)


def test_same_sku_with_different_packing_states_stays_separate():
    lines = [
        PackingSlipLine(source_ref="SO-1", original_sku="WHS-B30", packing_state="flatpack"),
        PackingSlipLine(source_ref="SO-2", original_sku="WHS-B30", packing_state="assembled"),
    ]
    matches = match_lines(lines, [make_master("B30")], ignored_prefixes=PREFIXES, fuzzy_threshold=88)
    combined, _ = combine_matches(matches, allow_fuzzy=False)
    assert len(combined) == 2
    assert {item.packing_state.value for item in combined} == {"flatpack", "assembled"}
