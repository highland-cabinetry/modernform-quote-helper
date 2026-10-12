from __future__ import annotations

import pytest
from app.database import Repository
from app.models import ItemKind, ItemMaster, MatchStatus, PackingRequest, PackingSlipLine, PackingState
from app.packing_worker import SnapshotRepository
from app.services.broker_handoff import quote_rows
from app.services.product_exceptions import CSB42_RULE, is_status_placeholder
from app.services.reports import packing_result_csv
from app.services.shipments import build_shipment
from app.services.sku_matching import combine_matches, match_lines


def _master(
    sku: str,
    item_id: int,
    *,
    description: str,
    manufacturer_sku: str | None = None,
    assembled_height: float | None = 31.25,
    weight: float = 137.5,
) -> ItemMaster:
    values = dict(
        id=item_id,
        sku=sku,
        manufacturer_sku=manufacturer_sku,
        description=description,
        flatpack_length=36,
        flatpack_width=24,
        flatpack_height=5,
        weight=weight,
        category="Base cabinet",
        item_kind=ItemKind.CABINET,
        must_stay_upright=True,
        allowed_rotations=["LWH", "WLH"],
        rules_reviewed=True,
    )
    if assembled_height is not None:
        values.update(assembled_length=45, assembled_width=45, assembled_height=assembled_height)
    return ItemMaster(**values)


def _match(lines: list[PackingSlipLine], master: list[ItemMaster], aliases=None):
    return match_lines(
        lines,
        master,
        ignored_prefixes=[],
        fuzzy_threshold=1,
        confirmed_aliases=aliases,
    )


@pytest.mark.parametrize(
    "value",
    ["BACK-ORDER", "back order", "BACKORDERED", "Back-Order(Shaker)", "BACKORDERED - Slim Shaker"],
)
def test_status_placeholder_variants_are_detected(value):
    assert is_status_placeholder(value)


def test_status_placeholder_resolves_unique_whole_token_or_exact_catalog_description():
    csb42 = _master(
        "CSB42",
        42,
        description="Corner sink base cabinet",
        manufacturer_sku="M-CSB42",
    )
    b42 = _master("B42", 43, description="Standard base 42")
    lines = [
        PackingSlipLine(original_sku="BACK-ORDER", description="Delayed CSB42 cabinet"),
        PackingSlipLine(original_sku="BACK ORDER (Shaker)", description="M-CSB42"),
        PackingSlipLine(original_sku="BACKORDERED", description="Corner sink base cabinet"),
    ]

    matches = _match(lines, [csb42, b42])

    assert all(match.match_status == MatchStatus.CONFIRMED_ALIAS for match in matches)
    assert all(match.matched_item and match.matched_item.sku == "CSB42" for match in matches)
    assert all(match.matched_item.weight == 137.5 for match in matches if match.matched_item)
    assert all(
        match.matched_item.dimensions_for(PackingState.ASSEMBLED) == (42, 42, 31.25)
        for match in matches
        if match.matched_item
    )
    assert [(match.line.original_sku, match.line.description) for match in matches] == [
        (line.original_sku, line.description) for line in lines
    ]


def test_status_placeholder_unknown_and_ambiguous_descriptions_fail_closed():
    csb42 = _master("CSB42", 42, description="Corner sink base cabinet")
    b42 = _master("B42", 43, description="Standard base 42")
    unknown = PackingSlipLine(original_sku="BACK ORDER", description="Trash Kitchen organizer")
    ambiguous = PackingSlipLine(original_sku="BACK-ORDER", description="CSB42 with B42")

    unknown_match, ambiguous_match = _match([unknown, ambiguous], [csb42, b42])

    assert unknown_match.match_status == MatchStatus.UNMATCHED
    assert "does not identify" in unknown_match.review_message
    assert ambiguous_match.match_status == MatchStatus.UNMATCHED
    assert "ambiguous" in ambiguous_match.review_message
    assert "B42" in ambiguous_match.review_message and "CSB42" in ambiguous_match.review_message


def test_status_trash_phrase_participates_in_ambiguity_without_matching_trash_kitchen():
    b42 = _master("B42", 43, description="Standard base 42")
    ambiguous = PackingSlipLine(original_sku="BACK-ORDER", description="TRASH KIT / B42")
    explicit_b42 = PackingSlipLine(original_sku="BACK ORDER", description="Trash Kitchen B42")

    ambiguous_match, b42_match = _match([ambiguous, explicit_b42], [b42])

    assert ambiguous_match.match_status == MatchStatus.UNMATCHED
    assert "ambiguous" in ambiguous_match.review_message
    assert "B42" in ambiguous_match.review_message and "TRASH KIT" in ambiguous_match.review_message
    assert b42_match.match_status == MatchStatus.CONFIRMED_ALIAS
    assert b42_match.matched_item and b42_match.matched_item.sku == "B42"


def test_status_placeholder_ignores_persistent_alias_and_uses_description_identity():
    csb42 = _master("CSB42", 42, description="Corner sink base cabinet")
    b42 = _master("B42", 43, description="Standard base 42")
    line = PackingSlipLine(original_sku="Back-Order(Shaker)", description="B42")

    match = _match([line], [csb42, b42], {"BACKORDERSHAKER": csb42})[0]

    assert match.match_status == MatchStatus.CONFIRMED_ALIAS
    assert match.matched_item.sku == "B42"


def test_status_placeholder_cannot_be_saved_as_alias(tmp_path):
    repository = Repository(tmp_path / "aliases.sqlite3")
    repository.initialize()
    target = _master("B42", 43, description="Standard base 42")
    target.id = None
    repository.upsert_items([target])

    with pytest.raises(ValueError, match="status placeholders"):
        repository.save_sku_alias("Back-Order(Shaker)", target.id, ignored_prefixes=[])


def test_csb42_without_catalog_assembled_height_remains_unpackable():
    csb42 = _master("CSB42", 42, description="Corner sink base cabinet", assembled_height=None)
    request = PackingRequest(
        shipment_name="missing catalog height",
        save_result=False,
        lines=[
            PackingSlipLine(
                source_ref="slip-a.csv",
                original_sku="BACK-ORDER",
                description="CSB42",
                packing_state=PackingState.ASSEMBLED,
            )
        ],
    )

    result = build_shipment(
        request,
        SnapshotRepository({"master": [csb42.model_dump(mode="json")], "aliases": {}}),
    )

    assert result.plan_status == "blocked"
    assert result.unpacked[0].reason_code == "MISSING_ASSEMBLED_DIMENSIONS"
    assert result.unpacked[0].weight == 137.5


def test_repository_csb42_catalog_height_and_weight_drive_effective_packing(tmp_path):
    repository = Repository(tmp_path / "csb42-sentinel.sqlite3")
    repository.initialize()
    csb42 = _master("CSB42", 42, description="Corner sink base cabinet", assembled_height=31.25, weight=137.5)
    csb42.id = None
    repository.upsert_items([csb42])
    stored = repository.list_items()[0]
    assert stored.dimensions_for(PackingState.ASSEMBLED) == (45, 45, 31.25)
    assert stored.weight == 137.5
    request = PackingRequest(
        shipment_name="persisted CSB42",
        save_result=False,
        lines=[
            PackingSlipLine(
                source_ref="persisted.csv",
                original_sku="CSB42",
                description="Corner sink base cabinet",
                packing_state=PackingState.ASSEMBLED,
            )
        ],
    )

    result = build_shipment(request, repository)

    assert result.plan_status == "ready"
    placed = result.pallets[0].items[0]
    assert (placed.length, placed.width, placed.height) == (42, 42, 31.25)
    assert placed.assembled_dimensions == (42, 42, 31.25)
    assert placed.weight == 137.5
    assert result.summary.total_item_weight == 137.5


def test_effective_csb42_data_propagates_across_multiple_slips_results_and_quote_outputs():
    csb42 = _master("CSB42", 42, description="Corner sink base cabinet", assembled_height=31.25, weight=137.5)
    lines = [
        PackingSlipLine(
            source_ref="slip-a.csv",
            original_sku="BACK-ORDER",
            description="CSB42",
            packing_state=PackingState.ASSEMBLED,
        ),
        PackingSlipLine(
            source_ref="slip-b.csv",
            original_sku="BACKORDERED (Shaker)",
            description="Corner sink base cabinet",
            packing_state=PackingState.ASSEMBLED,
        ),
    ]
    matches = _match(lines, [csb42])
    combined, review = combine_matches(matches, allow_fuzzy=False)
    assert len(combined) == 1
    assert combined[0].quantity == 2
    assert {source.source_ref for source in combined[0].sources} == {"slip-a.csv", "slip-b.csv"}
    assert len(review) == 2

    request = PackingRequest(shipment_name="effective CSB42", save_result=False, lines=lines)
    result = build_shipment(
        request,
        SnapshotRepository({"master": [csb42.model_dump(mode="json")], "aliases": {}}),
    )
    placed = [item for pallet in result.pallets for item in pallet.items]
    assert result.summary.total_items == result.summary.packed_items == 2
    assert len(placed) == 2
    assert all((item.length, item.width, item.height) == (42, 42, 31.25) for item in placed)
    assert all(item.weight == 137.5 and item.item_kind == ItemKind.CABINET for item in placed)
    assert result.summary.total_item_weight == 275
    assert sum(pallet.item_weight for pallet in result.pallets) == 275
    rows = quote_rows(result)
    assert len(rows) == result.summary.total_pallets
    assert [row["weight"] for row in rows] == [int(pallet.total_weight + 0.5) for pallet in result.pallets]
    assert all(row["width"] == 42 for row in rows)
    report = packing_result_csv(result).decode("utf-8-sig")
    assert "31.25" in report and CSB42_RULE.provenance.split(";")[0] not in report


def test_physically_different_manual_rows_do_not_combine_under_one_identity():
    lines = [
        PackingSlipLine(
            source_ref="slip-a.csv",
            original_sku="UNKNOWN",
            length=10,
            width=10,
            height=10,
            weight=10,
        ),
        PackingSlipLine(
            source_ref="slip-b.csv",
            original_sku="UNKNOWN",
            length=10,
            width=10,
            height=10,
            weight=10,
        ),
        PackingSlipLine(
            source_ref="slip-c.csv",
            original_sku="UNKNOWN",
            length=20,
            width=20,
            height=20,
            weight=20,
        ),
    ]

    matches = _match(lines, [])
    combined, _ = combine_matches(matches, allow_fuzzy=False)

    assert len(combined) == 2
    assert {item.master_item.weight for item in combined} == {10, 20}
    identical = next(item for item in combined if item.master_item.weight == 10)
    assert identical.quantity == 2
    assert {source.source_ref for source in identical.sources} == {"slip-a.csv", "slip-b.csv"}
