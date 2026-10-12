"""Company-configured text-line PDF layouts.

For a company whose PDFs have no ruled tables, the freight manager can describe the
layout with regular expressions in Company & Import Settings (see ``TextLayout``).
Each PDF page that matches ``detect_pattern`` is read line by line.
"""

from __future__ import annotations

import re

from ...models import PackingState
from ..imports import _non_physical_reason, _quantity_error
from .base import ImportContext, ParsedLine, ParsedOrder, ParseOutcome, canonical_reference, parse_address

FLAGS = re.IGNORECASE | re.MULTILINE


class TextLayoutAdapter:
    name = "text_layout"
    label = "Configured text layout"
    description = "PDFs without tables, read with the company's own line and header patterns."

    def _layout(self, context: ImportContext):
        pdf = context.pdf()
        if not pdf or not pdf.pages:
            return None
        text = pdf.first_page_text
        for layout in getattr(context.config, "text_layouts", []):
            if re.search(layout.detect_pattern, text, FLAGS):
                return layout
        return None

    def score(self, context: ImportContext) -> float:
        return 0.9 if self._layout(context) else 0.0

    def parse(self, context: ImportContext) -> ParseOutcome:
        layout = self._layout(context)
        if layout is None:
            return ParseOutcome(adapter=self.name, status="unsupported",
                                messages=["No configured text layout matches this document."])
        pdf = context.pdf()
        prefix = getattr(context.config, "bare_number_prefix", "")
        orders: dict[str, ParsedOrder] = {}
        current: ParsedOrder | None = None
        for page in pdf.pages:
            text = page.text or ""
            match = re.search(layout.order_pattern, text, FLAGS)
            if match:
                reference = canonical_reference(match.group("order"), prefix)
                if reference not in orders:
                    state = PackingState.FLATPACK
                    if layout.assembled_pattern and re.search(layout.assembled_pattern, text, FLAGS):
                        state = PackingState.ASSEMBLED
                    orders[reference] = ParsedOrder(order_number=reference, document_type=f"layout:{layout.name}",
                                                    references=[reference] if reference else [], packing_state=state)
                    order = orders[reference]
                    for attribute, pattern in (("po_number", layout.po_pattern), ("order_date", layout.date_pattern),
                                               ("customer_name", layout.customer_pattern)):
                        if pattern and (found := re.search(pattern, text, FLAGS)):
                            value = found.groupdict().get("value") or found.group(found.lastindex or 0)
                            setattr(order, attribute, value.strip())
                    if layout.ship_to_zip_pattern and (found := re.search(layout.ship_to_zip_pattern, text, FLAGS)):
                        order.ship_to = parse_address(found.group(0)) or {
                            "postal_code": found.group(found.lastindex or 0)
                        }
                current = orders[reference]
            if current is None:
                current = orders.setdefault("", ParsedOrder(order_number=None, document_type=f"layout:{layout.name}"))
            current.pages.append(page.page_number)
            for number, raw in enumerate(text.splitlines(), start=1):
                line = re.match(layout.line_pattern, raw, re.IGNORECASE)
                if not line:
                    continue
                groups = line.groupdict()
                sku = (groups.get("sku") or "").strip()
                description = (groups.get("description") or "").strip()
                quantity, quantity_reason = _quantity_error(groups.get("quantity"))
                reason = _non_physical_reason(sku, description) or quantity_reason
                source_ref = f"{context.filename} · page {page.page_number}, line {number}"
                if reason:
                    current.ignored.append({"source_ref": source_ref, "row": number, "original_sku": sku,
                                            "quantity": groups.get("quantity"), "description": description,
                                            "reason_code": reason[0], "reason": reason[1]})
                    continue
                current.lines.append(ParsedLine(original_sku=sku, quantity=int(quantity), description=description,
                                                packing_state=current.packing_state, line_number=number,
                                                source_ref=source_ref))
        result = [order for order in orders.values() if order.lines or order.order_number]
        for order in result:
            if not order.order_number:
                order.warnings.append("No order number was found; enter one before quoting.")
        return ParseOutcome(adapter=self.name, orders=result,
                            status="ok" if result else "unsupported",
                            messages=[] if result else [
                                f"Layout '{layout.name}' matched but no item lines were found."
                            ])
