"""Company rule sets gate the centralized product exceptions.

The exception facts themselves (CSB42 footprint, curated trash kit, back-order
placeholders) are covered in tests/test_product_exceptions.py; these tests check that
each company gets exactly the rules its ``rule_set`` names, through matching, packing,
import review and the settings API.
"""

from __future__ import annotations

import pytest
from app.freight_models import TextLayout
from app.models import ItemKind, MatchStatus, PackingRequest, PackingSlipLine, PackingState
from app.services.company_rules import RULE_SETS, describe_rule_sets, rule_set
from app.services.order_import import OrderImporter
from app.services.shipments import build_shipment, match_request
from app.services.sku_matching import match_lines
from fixtures_freight import acme_company, catalog_item, csv_bytes, make_repo, seed_catalog
from pydantic import ValidationError

PREFIXES = ["MB", "WHS"]


def csb42(assembled_height: float | None = 31.25):
    return catalog_item("CSB42", description="Corner sink base cabinet", length=36, width=24, height=5, weight=137.5,
                        assembled=(45, 45, assembled_height) if assembled_height else None,
                        item_kind=ItemKind.CABINET, must_stay_upright=True, id=42)


def match(lines, items, rules: str):
    return match_lines(lines, items, ignored_prefixes=PREFIXES, fuzzy_threshold=88, confirmed_aliases={},
                       rule_set=rules)


# --------------------------------------------------------------------------- generic rules (every rule set)


@pytest.mark.parametrize("rules", ["highland", "none"])
def test_ignored_prefixes_affect_lookup_only_and_preserve_the_authored_sku(rules):
    b30 = catalog_item("B30", id=1)
    lines = [PackingSlipLine(original_sku=sku) for sku in ("MB-B30", "WHS-B30", "whs b30", "MB-WHS-B30", "MBB30")]
    matches = match(lines, [b30], rules)
    statuses = [m.match_status for m in matches]
    assert statuses[:4] == [MatchStatus.EXACT] * 4
    # A prefix only counts when it is separated from the SKU; MBB30 is not silently treated as B30.
    assert statuses[4] != MatchStatus.EXACT
    assert [m.line.original_sku for m in matches] == ["MB-B30", "WHS-B30", "whs b30", "MB-WHS-B30", "MBB30"]
    assert all(m.matched_item.sku == "B30" for m in matches[:4])


def test_prefixed_sku_keeps_its_original_text_through_packing(tmp_path):
    repo = make_repo(tmp_path)
    request = PackingRequest(shipment_name="prefixes", save_result=False,
                             settings=repo.get_settings().model_copy(update={"sku_prefixes_to_ignore": PREFIXES}),
                             lines=[PackingSlipLine(source_ref="slip.csv", original_sku="WHS-B30", quantity=2)])
    result = build_shipment(request, repo)
    placed = [item for pallet in result.pallets for item in pallet.items]
    assert result.freight_ready
    assert {(item.sku, item.original_sku) for item in placed} == {("B30", "WHS-B30")}


@pytest.mark.parametrize("rules", ["highland", "none"])
def test_back_order_placeholder_resolves_from_description_in_every_rule_set(rules):
    b30 = catalog_item("B30", id=1, description="Base 30 shaker")
    matches = match([PackingSlipLine(original_sku="BACK ORDER", description="B30 delayed"),
                     PackingSlipLine(original_sku="BACKORDERED", description="Base 30 shaker"),
                     PackingSlipLine(original_sku="Back-Order", description="something else")], [b30], rules)
    assert [m.match_status for m in matches] == [MatchStatus.CONFIRMED_ALIAS, MatchStatus.CONFIRMED_ALIAS,
                                                 MatchStatus.UNMATCHED]
    assert [m.line.original_sku for m in matches] == ["BACK ORDER", "BACKORDERED", "Back-Order"]


# --------------------------------------------------------------------------- Highland-only product exceptions


def test_csb42_assembled_footprint_applies_only_with_the_highland_rule_set():
    line = PackingSlipLine(original_sku="CSB42", packing_state=PackingState.ASSEMBLED)
    [highland] = match([line], [csb42()], "highland")
    [plain] = match([line], [csb42()], "none")
    assert highland.matched_item.dimensions_for(PackingState.ASSEMBLED) == (42, 42, 31.25)
    assert plain.matched_item.dimensions_for(PackingState.ASSEMBLED) == (45, 45, 31.25)
    # Height and weight are always the catalog's.
    assert highland.matched_item.weight == plain.matched_item.weight == 137.5


@pytest.mark.parametrize("rules", ["highland", "none"])
def test_csb42_without_catalog_assembled_height_stays_unresolved(rules):
    [matched] = match([PackingSlipLine(original_sku="CSB42", packing_state=PackingState.ASSEMBLED)],
                      [csb42(assembled_height=None)], rules)
    assert matched.matched_item.dimensions_for(PackingState.ASSEMBLED) is None


def test_trash_kit_curated_carton_applies_only_with_the_highland_rule_set():
    line = PackingSlipLine(original_sku="TRASH KIT", description="Trash kit")
    [highland] = match([line], [], "highland")
    [plain] = match([line], [], "none")
    assert highland.match_status == MatchStatus.EXACT
    assert highland.matched_item.dimensions_for(PackingState.FLATPACK) == (18, 14, 3)
    assert highland.matched_item.weight == 18
    assert plain.match_status == MatchStatus.UNMATCHED and plain.matched_item is None


def test_trash_like_rows_fail_closed_only_under_highland_rules():
    pullout = catalog_item("TRASHCAN2", id=7)
    line = PackingSlipLine(original_sku="TRASHCAN1", description="Trash can pullout")
    [highland] = match([line], [pullout], "highland")
    [plain] = match([line], [pullout], "none")
    assert highland.match_status == MatchStatus.UNMATCHED
    assert "trash item" in highland.review_message
    assert plain.match_status == MatchStatus.FUZZY  # ordinary review path, no Highland special case


# --------------------------------------------------------------------------- companies select their rule set


def test_packing_uses_the_company_catalog_and_rule_set(tmp_path):
    repo = make_repo(tmp_path)
    acme = acme_company(repo, catalog="acme", rule_set="none")
    seed_catalog(repo, [csb42()], catalog="acme")
    seed_catalog(repo, [csb42()])  # same SKU in the Highland catalog
    line = PackingSlipLine(source_ref="slip", original_sku="CSB42", packing_state=PackingState.ASSEMBLED)

    [acme_match] = match_request(PackingRequest(lines=[line], company_id=acme.id, save_result=False), repo)
    assert acme_match.matched_item.dimensions_for(PackingState.ASSEMBLED) == (45, 45, 31.25)

    highland = build_shipment(PackingRequest(lines=[line], save_result=False,
                                             company_id=repo.default_company().id), repo)
    [placed] = [item for pallet in highland.pallets for item in pallet.items]
    assert (placed.length, placed.width, placed.height) == (42, 42, 31.25)

    # Switching Acme to the Highland rule set turns the exception on for its catalog too.
    acme.config.rule_set = "highland"
    repo.save_company(acme)
    [switched] = match_request(PackingRequest(lines=[line], company_id=acme.id, save_result=False), repo)
    assert switched.matched_item.dimensions_for(PackingState.ASSEMBLED) == (42, 42, 31.25)


def test_import_review_follows_the_company_rule_set(tmp_path):
    repo = make_repo(tmp_path)
    acme = acme_company(repo, rule_set="none")
    seed_catalog(repo, [catalog_item("AC-BASE30")], catalog="acme")
    slip = csv_bytes([["SKU", "Description", "Qty"], ["TRASH KIT", "Trash kit", "1"], ["B30", "Base", "1"]])
    [highland_order] = OrderImporter(repo, repo.default_company()).import_files([("SO77500.csv", slip)])["orders"]
    [acme_order] = OrderImporter(repo, acme).import_files([("SO77500.csv", slip)])["orders"]
    assert highland_order["issues"] == [] and highland_order["status"] == "ready"
    assert acme_order["status"] == "needs_review"
    assert "2 SKU(s) not in the catalog: B30, TRASH KIT" in acme_order["issues"]


def test_unknown_company_on_a_request_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(ValueError, match="no longer exists"):
        match_request(PackingRequest(lines=[PackingSlipLine(original_sku="B30")], company_id="missing"), repo)


# --------------------------------------------------------------------------- rule-set catalog and configuration


def test_describe_rule_sets_lists_every_rule_set_and_its_rules():
    described = {value["name"]: value for value in describe_rule_sets()}
    assert set(described) == set(RULE_SETS) == {"highland", "none"}
    highland_codes = {rule["code"] for rule in described["highland"]["rules"]}
    none_codes = {rule["code"] for rule in described["none"]["rules"]}
    assert {"PREFIXES", "BACK_ORDER", "NO_INVENTED_DATA", "CSB42", "TRASH_KIT"} <= highland_codes
    assert none_codes == {"PREFIXES", "BACK_ORDER", "NO_INVENTED_DATA"}
    csb = next(rule for rule in described["highland"]["rules"] if rule["code"] == "CSB42")
    assert "42 × 42" in csb["detail"] and csb["provenance"]
    assert rule_set("highland").product_exceptions and not rule_set("none").product_exceptions
    assert rule_set("unknown-set").name == "none" and rule_set(None).name == "none"


def test_import_formats_endpoint_and_company_config_validation(api):
    formats = api.get("/api/import-formats").json()
    assert {a["name"] for a in formats["adapters"]} == {"highland_pdf", "text_layout", "table"}
    assert {r["name"] for r in formats["rule_sets"]} == {"highland", "none"}

    companies = api.get("/api/companies").json()
    [default] = [c for c in companies if c["is_default"]]
    assert default["code"] == "HCI" and default["config"]["rule_set"] == "highland"
    assert default["config"]["bare_number_prefix"] == "INV"

    bad_adapter = api.post("/api/companies", json={"code": "ZED", "name": "Zed",
                                                   "config": {"adapters": ["ocr_magic"]}})
    assert bad_adapter.status_code == 422
    bad_rules = api.post("/api/companies", json={"code": "ZED", "name": "Zed", "config": {"rule_set": "acme"}})
    assert bad_rules.status_code == 422
    created = api.post("/api/companies", json={"code": "zed", "name": "Zed"})
    assert created.status_code == 200 and created.json()["code"] == "ZED"
    assert created.json()["config"]["rule_set"] == "none"  # new companies never inherit Highland rules
    assert api.post("/api/companies", json={"code": "ZED", "name": "Other"}).status_code == 409


def test_text_layout_patterns_must_define_required_groups():
    with pytest.raises(ValidationError, match="named group"):
        TextLayout(name="x", detect_pattern="X", order_pattern=r"Order (\d+)",
                   line_pattern=r"(?P<sku>\S+) (?P<quantity>\d+)")
    with pytest.raises(ValidationError, match="not a valid regular expression"):
        TextLayout(name="x", detect_pattern="(", order_pattern=r"(?P<order>\d+)",
                   line_pattern=r"(?P<sku>\S+) (?P<quantity>\d+)")
