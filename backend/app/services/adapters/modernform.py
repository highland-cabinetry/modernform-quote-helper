"""ModernForm Cabinets & Closet invoices and packing slips (QuickBooks layouts).

Both documents carry the same invoice number, a Ship To box and an item table:

* Invoice: header row ``P.O. Number | Terms | Due Date | Ship Date | S.O. Number | Outside Sales Rep |
  Ship Via | Assembly | Warehouse`` and the columns ``Item | Description | Quantity | Unit Price | Amount``.
  Item codes are complete (``NS-PC309024BOX``).
* Packing slip: header row ``P.O. No. | Sales Rep | Ship Date | Ship Via`` and the columns
  ``Item | Description | Assemble | Quantity``. QuickBooks shortens long item codes
  (``NS-PC309024...``); the modernform product rule set resolves them from the description.

Reading the tables, document boundaries, packing state and ship-to ZIP reuses the Highland reader
(the layouts share QuickBooks' structure); this adapter adds ModernForm's header names. Prices are
never read. Section headings (``*****Natural shaker``) and the delivery block under ``******RTA&
Freight`` have no quantity and are not imported.
"""

from __future__ import annotations

import re

from ..imports import _table_value
from .base import ImportContext, ParseOutcome
from .highland import HighlandPdfAdapter

MARKER = re.compile(r"modern\s*form\s+cabinets", re.IGNORECASE)
# Terms printed inside the item table (delivery and return policy), not items.
NOTICE = re.compile(r"curbside|acknowledg|restock|customers must|signature", re.IGNORECASE)


def _first(tables: list, *headers: str) -> str:
    for header in headers:
        value = _table_value(tables, header)
        if value:
            return value
    return ""


class ModernformPdfAdapter(HighlandPdfAdapter):
    name = "modernform_pdf"
    label = "ModernForm invoice / packing slip PDF"
    description = "ModernForm Cabinets & Closet invoices and packing slips (QuickBooks), one or several per PDF."

    def score(self, context: ImportContext) -> float:
        pdf = context.pdf()
        if not pdf or not pdf.pages:
            return 0.0
        return 0.98 if MARKER.search(pdf.first_page_text[:600]) else 0.0

    def parse(self, context: ImportContext) -> ParseOutcome:
        outcome = super().parse(context)
        outcome.adapter = self.name
        pdf = context.pdf()
        if pdf is None:
            return outcome
        pages = {page.page_number: page for page in pdf.pages}
        for order in outcome.orders:
            for row in order.ignored:
                text = str(row.get("original_sku") or "")
                if row.get("reason_code") == "MISSING_QUANTITY" and NOTICE.search(text):
                    row["reason_code"] = "NON_PHYSICAL_ROW"
                    row["reason"] = "Notice printed in the item table (delivery or return terms); not an item."
            first = pages.get(order.pages[0]) if order.pages else None
            if first is None:
                continue
            tables = first.tables
            order.po_number = order.po_number or _first(tables, "P.O. Number", "P.O. No.", "PO Number")
            order.sales_rep = order.sales_rep or _first(tables, "Outside Sales Rep", "Sales Rep")
            order.ready_date = order.ready_date or _first(tables, "Ship Date")
            sales_order = re.sub(r"\D", "", _first(tables, "S.O. Number", "S.O. No."))
            if sales_order and f"SO{sales_order}" not in order.references:
                order.references.append(f"SO{sales_order}")
            if not order.customer_name and order.ship_to.get("name"):
                # Packing slips have no Bill To box; the consignee is the customer.
                order.customer_name = order.ship_to["name"]
        if outcome.status == "unsupported":
            outcome.messages = ["No ModernForm invoice or packing slip content was recognised."]
        return outcome
