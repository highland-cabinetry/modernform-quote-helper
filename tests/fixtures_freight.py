"""Synthetic documents, catalogs and app wiring for the freight-domain tests.

Everything here is generated at test time: Highland-style PDFs are built with
reportlab tables (ruled grid lines so pdfplumber extracts them as tables), a second
company's documents are plain text lines drawn on a canvas, and spreadsheets are
built with csv/openpyxl. Nothing reads operational data or the live database.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import pytest
from app import main
from app.database import Repository
from app.freight_models import Company, CompanyConfig, TextLayout
from app.models import ItemMaster
from fastapi.testclient import TestClient
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# --------------------------------------------------------------------------- catalogs


def catalog_item(
    sku: str,
    *,
    length: float = 30,
    width: float = 24,
    height: float = 4,
    weight: float = 40,
    assembled: tuple[float, float, float] | None = None,
    **extra,
) -> ItemMaster:
    """A small flatpack carton (and optionally an assembled size) that packs quickly."""

    values = dict(
        sku=sku,
        description=extra.pop("description", f"{sku} test cabinet"),
        flatpack_length=length,
        flatpack_width=width,
        flatpack_height=height,
        weight=weight,
        category=extra.pop("category", "Base cabinet"),
        rules_reviewed=extra.pop("rules_reviewed", True),
    )
    if assembled:
        values.update(assembled_length=assembled[0], assembled_width=assembled[1], assembled_height=assembled[2])
    values.update(extra)
    return ItemMaster(**values)


DEFAULT_SKUS = ("B30", "B18", "W3030", "W1530", "SB36", "DB24")


def seed_catalog(repo: Repository, items: Iterable[ItemMaster] | None = None,
                 catalog: str = "default") -> list[ItemMaster]:
    values = list(items) if items is not None else [catalog_item(sku) for sku in DEFAULT_SKUS]
    repo.catalog_upsert(catalog, values, reason="test seed")
    return values


def make_repo(tmp_path, name: str = "freight.sqlite3", *, seed: bool = True) -> Repository:
    repo = Repository(tmp_path / name, tmp_path / "backups")
    repo.initialize()
    if seed:
        seed_catalog(repo)
    return repo


# --------------------------------------------------------------------------- Highland PDFs


@dataclass
class HighlandDoc:
    """One Highland invoice / packing slip / sales order page."""

    invoice: str | None = "66557"
    items: Sequence[Sequence[str]] = (("2", "WHS-B30", "Base cabinet 30", "2"),)
    kind: str = "Invoice"  # "Invoice", "Packing Slip" or "Sales Order"
    date: str = "8/17/2026"
    ship_to: str = "Jane Customer\n123 Canyon Rd\nSanta Fe, NM 87501"
    assembly: str = "NO"
    po: str = "PO-4471"
    rep: str = "JD"
    customer: str = "Acme Kitchens LLC\n1 Supply Way"
    sales_order: str | None = None
    extra_text: Sequence[str] = field(default_factory=tuple)
    item_headers: Sequence[str] = ("Qty", "Product ID", "Description", "Ship Qty")
    brand: str = "Highland Cabinetry"


def _grid(data, repeat_header: bool = False) -> Table:
    # repeatRows: a long item table continues on the next page under a repeated header row.
    table = Table([list(row) for row in data], repeatRows=1 if repeat_header else 0)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    return table


def highland_pdf(*documents: HighlandDoc) -> bytes:
    """A PDF with one page per document (a combined PDF when several are given)."""

    styles = getSampleStyleSheet()
    buffer = io.BytesIO()
    story: list = []
    for index, doc in enumerate(documents or (HighlandDoc(),)):
        if index:
            story.append(PageBreak())
        story.append(Paragraph(doc.brand, styles["Title"]))
        story.append(Paragraph(doc.kind, styles["Heading2"]))
        if doc.sales_order:
            story.append(Paragraph(doc.sales_order, styles["Normal"]))
        for line in doc.extra_text:
            story.append(Paragraph(line, styles["Normal"]))
        if doc.invoice is not None:
            story += [_grid([["Date", "Invoice #"], [doc.date, doc.invoice]]), Spacer(1, 6)]
        else:
            story += [_grid([["Date"], [doc.date]]), Spacer(1, 6)]
        story += [
            _grid([["Customer Name / Address"], [doc.customer]]), Spacer(1, 6),
            _grid([["Ship To"], [doc.ship_to]]), Spacer(1, 6),
            _grid([["Ship Via", "Assembly"], ["Freight", doc.assembly]]), Spacer(1, 6),
            _grid([["P.O. No.", "Sales Rep"], [doc.po, doc.rep]]), Spacer(1, 6),
            _grid([list(doc.item_headers), *[list(row) for row in doc.items]], repeat_header=True),
        ]
    SimpleDocTemplate(buffer, pagesize=letter).build(story)
    return buffer.getvalue()


# --------------------------------------------------------------------------- text-layout PDFs (second company)


ACME_LAYOUT = TextLayout(
    name="Acme order acknowledgement",
    detect_pattern=r"ACME MILLWORK",
    order_pattern=r"Order No[.:]?\s*(?P<order>AC-?\d+)",
    line_pattern=r"^\s*(?P<quantity>\d+)\s+x\s+(?P<sku>[A-Z0-9-]+)\s+(?P<description>.+)$",
    po_pattern=r"Customer PO[:]?\s*(?P<value>\S+)",
    date_pattern=r"Order Date[:]?\s*(?P<value>[0-9/]+)",
    customer_pattern=r"Sold To[:]?\s*(?P<value>.+)$",
    ship_to_zip_pattern=r"Deliver To[:]?.*?(?P<zip>\d{5})",
    assembled_pattern=r"ASSEMBLED",
)


def text_pdf(*pages: Sequence[str]) -> bytes:
    """Plain text lines (no ruled tables) drawn with the canvas, one list per page."""

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    for lines in pages:
        y = 740
        for line in lines:
            pdf.drawString(54, y, line)
            y -= 16
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def acme_page(order: str = "AC-5001", lines: Sequence[str] = ("2 x AC-BASE30 Base thirty", "1 x AC-WALL15 Wall"),
              *, assembled: bool = False) -> list[str]:
    return [
        "ACME MILLWORK",
        "Order Acknowledgement",
        f"Order No: {order}",
        "Order Date: 9/01/2026",
        "Customer PO: ZX-9",
        "Sold To: Desert Homes",
        "Deliver To: 55 Mesa Dr, Tucson, AZ 85701",
        *(["*** ASSEMBLED ***"] if assembled else []),
        *lines,
        "Thank you for your order",
    ]


def scanned_pdf() -> bytes:
    """A PDF page with only vector artwork: no text layer and no ruled tables."""

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.circle(300, 400, 120, stroke=1, fill=1)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


# --------------------------------------------------------------------------- spreadsheets and archives


def csv_bytes(rows: Sequence[Sequence[object]]) -> bytes:
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    return stream.getvalue().encode("utf-8")


def xlsx_bytes(rows: Sequence[Sequence[object]], sheet: str = "Order") -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    for row in rows:
        worksheet.append(list(row))
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def zip_bytes(members: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return stream.getvalue()


# --------------------------------------------------------------------------- companies


def acme_company(repo: Repository, *, catalog: str = "acme", rule_set: str = "none",
                 adapters: Sequence[str] = ("text_layout", "table"), **config) -> Company:
    company = Company(
        code="ACME", name="Acme Millwork",
        config=CompanyConfig(catalog=catalog, rule_set=rule_set, adapters=list(adapters),
                             text_layouts=[ACME_LAYOUT], **config),
    )
    return repo.save_company(company)


# --------------------------------------------------------------------------- API wiring


@pytest.fixture
def freight_repo(tmp_path) -> Repository:
    return make_repo(tmp_path)


@pytest.fixture
def api(freight_repo, monkeypatch):
    """A TestClient bound to an isolated, initialised repository (lifespan runs the backfill)."""

    monkeypatch.setattr(main, "repository", freight_repo)
    with TestClient(main.app) as client:
        client.repo = freight_repo  # type: ignore[attr-defined]
        yield client


def make_order(api, number: str, *lines: tuple[str, int], postal_code: str = "87501") -> dict:
    response = api.post("/api/orders", json={
        "order_number": number,
        "ship_to": {"postal_code": postal_code},
        "lines": [{"original_sku": sku, "quantity": qty, "source_ref": f"{number}.pdf"} for sku, qty in lines],
    })
    assert response.status_code == 200, response.text
    return response.json()


def pack(api, order_ids: list[str], **extra) -> tuple[dict, dict]:
    """Builder flow: order lines → pallet plan (saved) → the quote recorded for it."""

    selection = api.post("/api/orders/packing-lines", json={"order_ids": order_ids})
    assert selection.status_code == 200, selection.text
    selected = selection.json()
    request = {"shipment_name": selected["suggested_name"], "lines": selected["lines"],
               "settings": selected["settings"], "company_id": selected["company_id"], "order_ids": order_ids, **extra}
    result = api.post("/api/shipments/pack", json=request)
    assert result.status_code == 200, result.text
    plan = result.json()
    quote = api.get("/api/quotes/for-source", params={"packing_result_id": plan["id"]}).json()
    assert quote is not None
    return plan, quote


def upload(client: TestClient, *files: tuple[str, bytes], company_id: str | None = None) -> dict:
    data = {"company_id": company_id} if company_id else None
    response = client.post(
        "/api/imports",
        files=[("files", (name, content, "application/octet-stream")) for name, content in files],
        data=data,
    )
    assert response.status_code == 200, response.text
    return response.json()
