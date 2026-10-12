"""Document adapters: Highland PDFs, configured text layouts, spreadsheets and file handling."""

from __future__ import annotations

import pytest
from app.freight_models import ColumnMapping, CompanyConfig
from app.models import PackingState
from app.services import order_import
from app.services.adapters import ADAPTERS, ImportContext, choose_adapter
from app.services.order_import import OrderImporter
from fixtures_freight import (
    ACME_LAYOUT,
    HighlandDoc,
    acme_company,
    acme_page,
    catalog_item,
    csv_bytes,
    highland_pdf,
    make_repo,
    scanned_pdf,
    seed_catalog,
    text_pdf,
    xlsx_bytes,
    zip_bytes,
)

HIGHLAND = CompanyConfig(catalog="default", rule_set="highland", adapters=["highland_pdf", "text_layout", "table"],
                         document_markers=["Highland Cabinetry"], bare_number_prefix="INV")


def parse(adapter: str, filename: str, content: bytes, config: CompanyConfig = HIGHLAND):
    return ADAPTERS[adapter].parse(ImportContext(filename=filename, content=content, config=config))


def importer(tmp_path, company=None):
    repo = make_repo(tmp_path)
    return repo, OrderImporter(repo, company or repo.default_company())


def by_file(result: dict) -> dict[str, dict]:
    return {row["file"]: row for row in result["files"]}


# --------------------------------------------------------------------------- Highland PDFs


def test_highland_invoice_header_fields_and_lines():
    content = highland_pdf(HighlandDoc(
        invoice="66557", sales_order="SO75567", assembly="YES", po="PO-4471", rep="JD",
        customer="Acme Kitchens LLC\n1 Supply Way", ship_to="Jane Customer\n123 Canyon Rd\nSanta Fe, NM 87501",
        items=[("2", "WHS-B30", "Base cabinet 30", "2"), ("7", "MB-W3030", "Wall cabinet", "3")],
    ))
    context = ImportContext(filename="66557-I.pdf", content=content, config=HIGHLAND)
    adapter, score = choose_adapter(context, HIGHLAND.adapters)
    assert adapter.name == "highland_pdf" and score >= 0.9

    outcome = adapter.parse(context)
    assert outcome.status == "ok"
    [order] = outcome.orders
    assert order.order_number == "INV66557"
    assert order.document_type == "invoice"
    assert "SO75567" in order.references
    assert (order.order_date, order.po_number, order.sales_rep) == ("8/17/2026", "PO-4471", "JD")
    assert order.customer_name == "Acme Kitchens LLC"
    assert order.ship_to == {"city": "Santa Fe", "state": "NM", "postal_code": "87501",
                             "street": "123 Canyon Rd", "name": "Jane Customer"}
    assert order.packing_state == PackingState.ASSEMBLED
    # Original SKUs are kept exactly as printed; Ship Qty wins over the ordered Qty.
    assert [(line.original_sku, line.quantity) for line in order.lines] == [("WHS-B30", 2), ("MB-W3030", 3)]
    assert {line.packing_state for line in order.lines} == {PackingState.ASSEMBLED}
    assert all(line.source_ref.startswith("66557-I.pdf · page 1") for line in order.lines)
    assert order.pages == [1]


def test_highland_combined_pdf_becomes_one_order_per_document():
    content = highland_pdf(
        HighlandDoc(invoice="66557", assembly="YES", items=[("2", "B30", "Base", "2")]),
        HighlandDoc(invoice="66571", kind="Packing Slip", ship_to="Ranch\n9 Dune St\nOdessa, TX 79763",
                    items=[("1", "W3030", "Wall", "1"), ("1", "B18", "Base", "1")], po="PO-2", rep="MK"),
        HighlandDoc(invoice=None, kind="Sales Order", sales_order="SO76325", ship_to="Lodge\nBozeman, MT 59714",
                    items=[("4", "DB24", "Drawer base", "")]),
    )
    outcome = parse("highland_pdf", "combined.pdf", content)
    assert [o.order_number for o in outcome.orders] == ["INV66557", "INV66571", "SO76325"]
    assert [o.document_type for o in outcome.orders] == ["invoice", "packing_slip", "sales_order"]
    assert [o.pages for o in outcome.orders] == [[1], [2], [3]]
    assert [o.ship_to["postal_code"] for o in outcome.orders] == ["87501", "79763", "59714"]
    assert [o.packing_state for o in outcome.orders] == [PackingState.ASSEMBLED, PackingState.FLATPACK,
                                                         PackingState.FLATPACK]
    assert [o.po_number for o in outcome.orders][:2] == ["PO-4471", "PO-2"]
    # Lines stay with the document they were printed on.
    assert [[(line.original_sku, line.quantity) for line in o.lines] for o in outcome.orders] == [
        [("B30", 2)], [("W3030", 1), ("B18", 1)], [("DB24", 4)]]


def test_combined_pdf_import_creates_three_orders_from_one_file(tmp_path):
    repo, imp = importer(tmp_path)
    content = highland_pdf(*(HighlandDoc(invoice=str(n), items=[("1", "B30", "Base", "1")])
                             for n in (81001, 81002, 81003)))
    result = imp.import_files([("three.pdf", content)])
    assert result["summary"]["created"] == 3
    [row] = result["files"]
    assert row["status"] == "imported" and row["adapter"] == "highland_pdf"
    assert len(row["order_ids"]) == 3
    stored = repo.get_orders(row["order_ids"])
    assert [o.order_number for o in stored] == ["INV81001", "INV81002", "INV81003"]
    assert {o.source_file_id for o in stored} == {repo.get_source_file(stored[0].source_file_id).id}
    assert all(o.status == "ready" for o in stored)


def test_non_physical_and_unreadable_rows_are_reported_with_reasons_not_dropped():
    content = highland_pdf(HighlandDoc(items=[
        ("2", "B30", "Base", "2"),
        ("1", "Freight", "Freight charge", "1"),
        ("1", "Discount", "Dealer discount", "1"),
        ("1", "Tax NM", "Gross receipts tax", "1"),
        ("", "SBS", "****** Signature Blue Shaker ******", ""),
        ("", "B18", "Quantity missing", ""),
        ("1.5", "W3030", "Fractional", ""),
        ("7", "DB24", "Partially shipped", "2"),
    ]))
    [order] = parse("highland_pdf", "fees.pdf", content).orders
    assert [(line.original_sku, line.quantity) for line in order.lines] == [("B30", 2), ("DB24", 2)]
    reasons = {row["original_sku"]: row["reason_code"] for row in order.ignored}
    assert reasons == {
        "Freight": "NON_PHYSICAL_ROW", "Discount": "NON_PHYSICAL_ROW", "Tax NM": "NON_PHYSICAL_ROW",
        "SBS": "SECTION_HEADING", "B18": "MISSING_QUANTITY", "W3030": "INVALID_QUANTITY",
    }
    assert all(row["reason"] and row["source_ref"].startswith("fees.pdf · page 1") for row in order.ignored)


def test_ignored_rows_surface_in_the_import_result_and_order_issues(tmp_path):
    _, imp = importer(tmp_path)
    content = highland_pdf(HighlandDoc(invoice="66001",
                                       items=[("2", "B30", "Base", "2"), ("1", "Freight", "Fee", "1")]))
    result = imp.import_files([("fee.pdf", content)])
    [row] = result["files"]
    assert [r["original_sku"] for r in row["ignored_rows"]] == ["Freight"]
    [order] = result["orders"]
    # Fee/freight rows are expected on invoices: reported per file, but they don't force review.
    assert not any("not imported" in issue for issue in order["issues"])
    assert order["status"] == "ready"


def test_rows_without_a_usable_quantity_put_the_order_in_review(tmp_path):
    _, imp = importer(tmp_path)
    content = highland_pdf(HighlandDoc(invoice="66002",
                                       items=[("2", "B30", "Base", "2"), ("", "B18", "Quantity missing", "")]))
    [order] = imp.import_files([("missing.pdf", content)])["orders"]
    assert any("no usable quantity" in issue and "B18" in issue for issue in order["issues"])
    assert order["status"] == "needs_review"


# --------------------------------------------------------------------------- configured text layout (second company)


def test_text_layout_adapter_reads_second_company_pdf(tmp_path):
    config = CompanyConfig(catalog="acme", rule_set="none", adapters=["text_layout", "table"],
                           text_layouts=[ACME_LAYOUT])
    content = text_pdf(
        acme_page("AC-5001", ["2 x AC-BASE30 Base thirty", "1 x FREIGHT-CHG Freight to site", "0 x AC-WALL15 Wall"]),
        acme_page("AC-5002", ["3 x AC-WALL15 Wall fifteen"], assembled=True),
    )
    context = ImportContext(filename="acme.pdf", content=content, config=config)
    adapter, score = choose_adapter(context, config.adapters)
    assert adapter.name == "text_layout" and score == pytest.approx(0.9)
    assert ADAPTERS["highland_pdf"].score(context) == 0

    outcome = adapter.parse(context)
    first, second = outcome.orders
    assert (first.order_number, second.order_number) == ("AC5001", "AC5002")
    assert first.document_type == "layout:Acme order acknowledgement"
    assert (first.po_number, first.order_date, first.customer_name) == ("ZX-9", "9/01/2026", "Desert Homes")
    assert first.ship_to["postal_code"] == "85701"
    assert [(line.original_sku, line.quantity, line.packing_state) for line in first.lines] == [
        ("AC-BASE30", 2, PackingState.FLATPACK)]
    assert {row["original_sku"]: row["reason_code"] for row in first.ignored} == {
        "FREIGHT-CHG": "NON_PHYSICAL_ROW", "AC-WALL15": "INVALID_QUANTITY"}
    assert second.packing_state == PackingState.ASSEMBLED
    assert [(line.original_sku, line.quantity, line.packing_state) for line in second.lines] == [
        ("AC-WALL15", 3, PackingState.ASSEMBLED)]


def test_text_layout_pdf_is_unsupported_for_a_company_without_that_layout(tmp_path):
    _, imp = importer(tmp_path)  # Highland: no text layouts configured
    result = imp.import_files([("acme.pdf", text_pdf(acme_page()))])
    [row] = result["files"]
    assert row["status"] == "unsupported"
    assert "not recognised" in row["messages"][0]
    assert result["summary"]["created"] == 0


def test_text_layout_import_matches_against_the_company_catalog(tmp_path):
    repo = make_repo(tmp_path)
    acme = acme_company(repo)
    seed_catalog(repo, [catalog_item("AC-BASE30"), catalog_item("AC-WALL15")], catalog="acme")
    result = OrderImporter(repo, acme).import_files([("acme.pdf", text_pdf(
        acme_page("AC-7001"), acme_page("AC-7002", ["1 x B30 Highland base"])))])
    orders = {o["order_number"]: o for o in result["orders"]}
    assert orders["AC7001"]["status"] == "ready" and orders["AC7001"]["issues"] == []
    # B30 exists only in the default (Highland) catalog, so the Acme order flags it.
    assert orders["AC7002"]["status"] == "needs_review"
    assert any("not in the catalog: B30" in issue for issue in orders["AC7002"]["issues"])
    assert {o["company_id"] for o in result["orders"]} == {acme.id}


# --------------------------------------------------------------------------- table adapter


def test_table_adapter_uses_company_column_mapping_and_groups_by_order_column():
    config = CompanyConfig(
        adapters=["table"], bare_number_prefix="AC",
        column_mapping=ColumnMapping(sku=["Article"], quantity=["Menge"], order_number=["Auftrag"],
                                     description=["Item"]),
    )
    content = csv_bytes([
        ["Auftrag", "Article", "Item", "Menge"],
        ["5001", "AC-BASE30", "Base thirty", "2"],
        ["5002", "AC-WALL15", "Wall", "1"],
        ["5001", "AC-WALL15", "Wall", "4"],
        ["5002", "Shipping", "Delivery fee", "1"],
    ])
    outcome = parse("table", "export.csv", content, config)
    assert outcome.status == "ok"
    orders = {o.order_number: o for o in outcome.orders}
    assert set(orders) == {"AC5001", "AC5002"}
    assert [(line.original_sku, line.quantity, line.description) for line in orders["AC5001"].lines] == [
        ("AC-BASE30", 2, "Base thirty"), ("AC-WALL15", 4, "Wall")]
    assert [(line.original_sku, line.quantity) for line in orders["AC5002"].lines] == [("AC-WALL15", 1)]
    # The fee row belongs to order 5002, so only that order is flagged for it.
    assert orders["AC5001"].ignored == []
    assert [(row["original_sku"], row["reason_code"], row["order_number"]) for row in orders["AC5002"].ignored] == [
        ("Shipping", "NON_PHYSICAL_ROW", "5002")]
    assert all(o.document_type == "spreadsheet" for o in outcome.orders)

    # Without the company mapping the same headers are not recognised at all.
    plain = parse("table", "export.csv", content, CompanyConfig(adapters=["table"]))
    assert plain.status == "unsupported" and not plain.orders
    assert "Column names" in plain.messages[0]


def test_table_adapter_xlsx_packing_state_column_and_filename_reference():
    content = xlsx_bytes([
        ["Item Number", "Description", "Qty", "Assembly"],
        ["B30", "Base", 2, "assembled"],
        ["W3030", "Wall", 1, ""],
    ])
    [order] = parse("table", "SO77001 picks.xlsx", content).orders
    assert order.order_number == "SO77001"
    assert [(line.original_sku, line.quantity, line.packing_state) for line in order.lines] == [
        ("B30", 2, PackingState.ASSEMBLED), ("W3030", 1, PackingState.FLATPACK)]
    assert order.lines[0].source_ref == "SO77001 picks.xlsx · Order"


def test_table_without_order_number_is_imported_for_review_with_a_placeholder_number(tmp_path):
    _, imp = importer(tmp_path)
    result = imp.import_files([("loose-lines.csv", csv_bytes([["SKU", "Qty"], ["B30", "1"]]))])
    [order] = result["orders"]
    assert order["order_number"].startswith("PENDING-")
    assert order["status"] == "needs_review"
    assert any("No order number" in issue for issue in order["issues"])


def test_generic_table_pdf_without_highland_markers_uses_the_table_adapter(tmp_path):
    content = highland_pdf(HighlandDoc(brand="Plain Supplier", kind="Pick list", invoice=None,
                                       item_headers=("SKU", "Description", "Qty"),
                                       items=[("B30", "Base", "2")]))
    context = ImportContext(filename="INV68001.pdf", content=content, config=HIGHLAND)
    adapter, _ = choose_adapter(context, HIGHLAND.adapters)
    assert adapter.name == "table"
    [order] = adapter.parse(context).orders
    assert order.order_number == "INV68001" and order.document_type == "pdf_table"
    assert [(line.original_sku, line.quantity) for line in order.lines] == [("B30", 2)]


# --------------------------------------------------------------------------- unreadable and unsupported files


def test_scanned_malformed_and_unsupported_files_are_reported_per_file(tmp_path):
    repo, imp = importer(tmp_path)
    result = imp.import_files([
        ("scan.pdf", scanned_pdf()),
        ("broken.pdf", b"%PDF-1.7\nthis is not really a pdf"),
        ("notes.docx", b"PK\x03\x04 not supported"),
        ("good.csv", csv_bytes([["SKU", "Qty"], ["B30", "1"]])),
    ])
    rows = by_file(result)
    assert rows["scan.pdf"]["status"] == "unsupported"
    assert "no text layer" in rows["scan.pdf"]["messages"][0]
    assert rows["broken.pdf"]["status"] == "failed"
    assert "could not be read" in rows["broken.pdf"]["messages"][0]
    assert rows["notes.docx"]["status"] == "unsupported"
    assert rows["good.csv"]["status"] == "imported"
    assert result["summary"]["failed"] == 3
    assert result["summary"]["created"] == 1
    # Rejected files are not stored and create no orders.
    assert [o["order_number"] for o in repo.list_orders()] == [result["orders"][0]["order_number"]]


def test_adapter_crash_is_contained_to_its_file(tmp_path, monkeypatch):
    _, imp = importer(tmp_path)

    def boom(self, context):
        raise RuntimeError("adapter bug")

    monkeypatch.setattr(type(ADAPTERS["table"]), "parse", boom)
    result = imp.import_files([("a.csv", csv_bytes([["SKU", "Qty"], ["B30", "1"]])),
                               ("b.pdf", highland_pdf(HighlandDoc(invoice="66900")))])
    rows = by_file(result)
    assert rows["a.csv"]["status"] == "failed" and "RuntimeError" in rows["a.csv"]["messages"][0]
    assert rows["b.pdf"]["status"] == "imported"


# --------------------------------------------------------------------------- ZIP archives and limits


def test_zip_with_several_documents_is_expanded_one_level(tmp_path):
    repo, imp = importer(tmp_path)
    archive = zip_bytes({
        "slips/66601.pdf": highland_pdf(HighlandDoc(invoice="66601", items=[("1", "B30", "Base", "1")])),
        "slips/SO77002.csv": csv_bytes([["SKU", "Qty"], ["W3030", "2"]]),
        "slips/readme.docx": b"not a slip",
        "slips/.DS_Store": b"hidden",
        "__MACOSX/slips/._66601.pdf": b"resource fork",
    })
    result = imp.import_files([("batch.zip", archive)])
    rows = by_file(result)
    assert set(rows) == {"batch/66601.pdf", "batch/SO77002.csv", "batch.zip/slips/readme.docx"}
    assert rows["batch.zip/slips/readme.docx"]["status"] == "unsupported"
    assert {o["order_number"] for o in result["orders"]} == {"INV66601", "SO77002"}
    assert result["summary"]["created"] == 2


def test_damaged_zip_and_zip_limits(tmp_path, monkeypatch):
    _, imp = importer(tmp_path)
    too_many = zip_bytes({f"f{i}.csv": b"SKU,Qty\nB30,1\n" for i in range(order_import.MAX_ZIP_MEMBERS + 1)})
    monkeypatch.setattr(order_import, "MAX_ZIP_UNCOMPRESSED", 4096)
    bomb = zip_bytes({"big.csv": b"SKU,Qty\n" + b"B30,1\n" * 2000})  # ~12 KB that compresses to almost nothing
    result = imp.import_files([("damaged.zip", b"PK\x03\x04garbage"), ("many.zip", too_many), ("bomb.zip", bomb)])
    rows = by_file(result)
    assert rows["damaged.zip"]["status"] == "failed" and "damaged" in rows["damaged.zip"]["messages"][0]
    assert rows["many.zip"]["status"] == "failed" and "more than" in rows["many.zip"]["messages"][0]
    assert rows["bomb.zip"]["status"] == "failed" and "expands" in rows["bomb.zip"]["messages"][0]
    assert result["summary"]["created"] == 0


def test_oversized_file_and_batch_limits(tmp_path, monkeypatch):
    _, imp = importer(tmp_path)
    monkeypatch.setattr(order_import, "MAX_FILE_BYTES", 64)
    result = imp.import_files([("big.csv", b"SKU,Qty\n" + b"B30,1\n" * 50)])
    assert result["files"][0]["status"] == "failed" and "larger than" in result["files"][0]["messages"][0]

    monkeypatch.setattr(order_import, "MAX_FILES_PER_BATCH", 2)
    with pytest.raises(ValueError, match="at most 2 files"):
        imp.import_files([(f"{n}.csv", b"SKU,Qty\nB30,1\n") for n in range(3)])


def test_long_invoice_continues_onto_a_second_page():
    items = [(str(n % 3 + 1), f"B{n:02d}", f"Base {n}", str(n % 3 + 1)) for n in range(1, 41)]
    content = highland_pdf(HighlandDoc(invoice="66990", items=items),
                           HighlandDoc(invoice="66991", items=[("1", "W3030", "Wall", "1")]))
    outcome = parse("highland_pdf", "long.pdf", content)
    first, second = outcome.orders
    assert first.order_number == "INV66990" and len(first.pages) == 2
    assert [(line.original_sku, line.quantity) for line in first.lines] == [(sku, int(q)) for q, sku, _, _ in items]
    assert {line.source_ref.split(" · ")[1].split(",")[0] for line in first.lines} == {"page 1", "page 2"}
    assert second.order_number == "INV66991" and second.pages == [3]
