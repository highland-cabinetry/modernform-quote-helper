"""Highland Cabinetry invoice, packing-slip and sales-order PDFs.

Reuses the proven V2 PDF reading (document boundaries in combined PDFs, packing state
from the Assembly box, ship-to ZIP and accessorial cues) and adds the order header
fields the legacy desktop app extracted: date, P.O., rep, ready date and customer.
Lines are kept with the document they came from, so a PDF containing three invoices
becomes three orders.
"""

from __future__ import annotations

import re
from typing import Any

from ...models import PackingState
from ..imports import _header_key, _pdf_documents, _table_value
from .base import ImportContext, ParsedOrder, ParseOutcome, parse_address
from .table import header_aliases, rows_to_lines

TEXT_LINE = re.compile(r"^\s*(\d+)\s+([A-Z0-9][A-Z0-9._/\-]{2,})\s+(.+)$", re.IGNORECASE)
CUSTOMER_HEADERS = ("Customer Name / Address", "Bill To", "Name / Address", "Sold To")


def _first_table_value(tables: list, *headers: str) -> str:
    for header in headers:
        value = _table_value(tables, header)
        if value:
            return value
    return ""


def _text_value_after(text: str, *labels: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    for label in labels:
        target = label.upper()
        for index, line in enumerate(lines):
            if line.upper() == target and index + 1 < len(lines):
                return lines[index + 1]
            if line.upper().startswith(target + ":"):
                return line.split(":", 1)[1].strip()
    return ""


def _block_after_header(tables: list, header: str) -> str:
    target = _header_key(header)
    for table in tables:
        if table and table[0] and _header_key(table[0][0]) == target:
            return "\n".join(str(cell or "") for row in table[1:] for cell in row if cell)
    return ""


class HighlandPdfAdapter:
    name = "highland_pdf"
    label = "Highland Cabinetry PDF"
    description = "Highland invoices, packing slips and sales orders, including PDFs combining several documents."

    def score(self, context: ImportContext) -> float:
        pdf = context.pdf()
        if not pdf or not pdf.pages:
            return 0.0
        text = pdf.first_page_text
        if re.search(r"highland\s+cabinetry", text, re.IGNORECASE):
            return 0.95
        tables = pdf.pages[0].tables
        if (_table_value(tables, "Invoice #") or _table_value(tables, "W.O. No.")
                or re.search(r"\bSO[\s#-]*\d{4,}", text)):
            return 0.75
        return 0.0

    def parse(self, context: ImportContext) -> ParseOutcome:
        outcome = ParseOutcome(adapter=self.name)
        pdf = context.pdf()
        if pdf is None:
            return ParseOutcome(adapter=self.name, status="failed", messages=[context.pdf_error or "Unreadable PDF"])
        if not pdf.has_text:
            return ParseOutcome(
                adapter=self.name, status="unsupported",
                messages=["This PDF has no text layer (it looks scanned). OCR is not installed; "
                          "enter the lines by hand or request a digital copy."],
            )
        documents, _suggestion, _name = _pdf_documents(context.filename, pdf.pages)
        aliases = header_aliases(context.config)
        orders: list[ParsedOrder] = []
        for document in documents:
            pages = [page for page in pdf.pages if page.page_number in document["pages"]]
            first = pages[0]
            reference = document.get("primary_reference")
            state = PackingState(str(document.get("packing_state") or "flatpack"))
            order = ParsedOrder(
                order_number=str(reference) if reference else None,
                document_type=str(document.get("document_type") or "unknown"),
                packing_state=state,
                pages=list(document["pages"]),
            )
            for value in (reference, document.get("sales_order_number"),
                          f"INV{document['invoice_number']}" if document.get("invoice_number") else None):
                if value and value not in order.references:
                    order.references.append(str(value))
            order.order_date = _first_table_value(first.tables, "Date") or _text_value_after(first.text, "Date")
            order.po_number = _first_table_value(first.tables, "P.O. No.", "PO Number", "P.O. Number") or \
                _text_value_after(first.text, "P.O. No.")
            order.sales_rep = _first_table_value(first.tables, "Sales Rep", "Rep") or \
                _text_value_after(first.text, "Sales Rep", "Rep")
            order.ready_date = _first_table_value(first.tables, "DELIVER / CPU / SHIP DATE", "Ready Date") or \
                _text_value_after(first.text, "Ready Date")
            customer_block = next(
                (block for header in CUSTOMER_HEADERS if (block := _block_after_header(first.tables, header))), ""
            )
            if customer_block:
                order.customer_name = customer_block.splitlines()[0].strip()
            ship_block = _block_after_header(first.tables, "Ship To")
            address = parse_address(ship_block) if ship_block else {}
            if not address.get("postal_code") and document.get("destination_zips"):
                address["postal_code"] = str(document["destination_zips"][0])
            order.ship_to = address
            if len(document.get("destination_zips") or []) > 1:
                order.warnings.append("This document lists more than one delivery ZIP; confirm the ship-to address.")
            accessorials = document.get("accessorials") or {}
            negatives = document.get("negative_accessorials") or {}
            for name in ("residential", "liftgate"):
                if accessorials.get(name):
                    setattr(order, name, True)
                elif negatives.get(name):
                    setattr(order, name, False)
            order.warnings.extend(str(message) for message in document.get("warnings") or [])
            for page in pages:
                if page.tables:
                    for number, table in enumerate(page.tables, start=1):
                        source_ref = f"{context.filename} · page {page.page_number}, table {number}"
                        pairs, ignored, _ = rows_to_lines(table, source_ref, aliases, state)
                        order.lines.extend(line for _, line in pairs)
                        order.ignored.extend(ignored)
                else:
                    rows = [["Quantity", "SKU", "Description"]]
                    rows.extend([m.group(1), m.group(2), m.group(3)] for line in page.text.splitlines()
                                if (m := TEXT_LINE.match(line)))
                    if len(rows) > 1:
                        pairs, ignored, _ = rows_to_lines(rows, f"{context.filename} · page {page.page_number}, text",
                                                          aliases, state)
                        order.lines.extend(line for _, line in pairs)
                        order.ignored.extend(ignored)
            if not order.order_number:
                order.warnings.append("No invoice or sales-order number was found; enter one before quoting.")
            if not order.lines:
                order.warnings.append("No item lines were recognised in this document.")
            orders.append(order)
        outcome.orders = [order for order in orders if order.lines or order.order_number or order.ignored]
        if not outcome.orders:
            outcome.status = "unsupported"
            outcome.messages.append("No Highland invoice, packing slip or sales order content was recognised.")
        return outcome


def summarize(document: dict[str, Any]) -> str:  # pragma: no cover - debugging helper
    return f"{document.get('document_type')} {document.get('primary_reference')} pages {document.get('pages')}"
