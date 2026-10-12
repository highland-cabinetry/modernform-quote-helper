"""Generic spreadsheet/table adapter (CSV, TSV, XLSX and table-based PDFs).

Headers are recognised through the shared alias list in ``services.imports`` plus any
company-specific names in ``CompanyConfig.column_mapping``. When an order-number column
exists, rows are grouped into one order per value; otherwise the whole sheet (or file)
is one order named after the file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...models import PackingState
from ..imports import (
    COLUMN_ALIASES,
    _csv_grid,
    _header_key,
    _non_physical_reason,
    _parse_number,
    _parse_packing_state,
    _quantity_error,
    _selected_quantity,
)
from .base import ImportContext, ParsedLine, ParsedOrder, ParseOutcome, canonical_reference, xlsx_bytes_to_grids

ORDER_ALIASES = {"order", "orderno", "ordernumber", "order#", "so", "sonumber", "salesorder", "invoice", "invoiceno",
                 "invoicenumber", "invoice#", "workorder", "wono"}


def header_aliases(config: Any) -> dict[str, set[str]]:
    aliases = {key: set(values) for key, values in COLUMN_ALIASES.items()}
    aliases["order_number"] = set(ORDER_ALIASES)
    mapping = getattr(config, "column_mapping", None)
    if mapping is not None:
        for field_name, target in (
            ("sku", "sku"),
            ("description", "description"),
            ("quantity", "quantity_ordered"),
            ("order_number", "order_number"),
            ("packing_state", "packing_state"),
        ):
            for name in getattr(mapping, field_name):
                key = _header_key(name)
                if key:
                    # A company-specific name wins over a generic meaning of the same header.
                    for values in aliases.values():
                        values.discard(key)
                    aliases[target].add(key)
    return aliases


def canonical_headers(row: list[Any], aliases: dict[str, set[str]]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    used: set[str] = set()
    for index, raw in enumerate(row):
        normalized = _header_key(raw)
        if not normalized:
            continue
        for canonical, names in aliases.items():
            if normalized in names and canonical not in used:
                mapping[index] = canonical
                used.add(canonical)
                break
    return mapping


def find_header(grid: list[list[Any]], aliases: dict[str, set[str]]) -> tuple[int, dict[int, str]] | None:
    for index, row in enumerate(grid[:25]):
        candidate = canonical_headers(list(row or []), aliases)
        values = set(candidate.values())
        if "sku" in values and values & {"quantity_ordered", "quantity_shipped"}:
            return index, candidate
    return None


def rows_to_lines(
    grid: list[list[Any]],
    source_ref: str,
    aliases: dict[str, set[str]],
    default_state: PackingState | None,
) -> tuple[list[tuple[str | None, ParsedLine]], list[dict[str, Any]], bool]:
    """Return (order_value, line) pairs, ignored rows, and whether a header was recognised."""

    found = find_header(grid, aliases)
    if not found:
        return [], [], False
    header_index, header_map = found
    lines: list[tuple[str | None, ParsedLine]] = []
    ignored: list[dict[str, Any]] = []
    for row_number, row in enumerate(grid[header_index + 1 :], start=header_index + 2):
        row = list(row or [])
        record = {canonical: row[index] if index < len(row) else None for index, canonical in header_map.items()}
        sku = str(record.get("sku") or "").strip()
        if not sku:
            continue
        description = str(record.get("description") or "").strip()
        quantity_value = _selected_quantity(record)
        quantity, quantity_reason = _quantity_error(quantity_value)
        reason = _non_physical_reason(sku, description) or quantity_reason
        if reason:
            ignored.append({
                "source_ref": source_ref, "row": row_number, "original_sku": sku, "quantity": quantity_value,
                "description": description, "reason_code": reason[0], "reason": reason[1],
                "order_number": str(record.get("order_number") or "").strip() or None,
            })
            continue
        state_text = str(record.get("packing_state") or "").strip()
        lines.append((
            str(record.get("order_number") or "").strip() or None,
            ParsedLine(
                original_sku=sku,
                quantity=int(quantity),
                description=description,
                packing_state=_parse_packing_state(state_text) if state_text else default_state,
                length=_parse_number(record.get("length")),
                width=_parse_number(record.get("width")),
                height=_parse_number(record.get("height")),
                weight=_parse_number(record.get("weight")),
                line_number=row_number,
                source_ref=source_ref,
            ),
        ))
    return lines, ignored, True


def group_orders(
    pairs: list[tuple[str | None, ParsedLine]],
    ignored: list[dict[str, Any]],
    fallback_reference: str | None,
    bare_prefix: str,
    document_type: str,
) -> list[ParsedOrder]:
    grouped: dict[str | None, ParsedOrder] = {}
    for order_value, line in pairs:
        reference = canonical_reference(order_value, bare_prefix) if order_value else fallback_reference
        order = grouped.get(reference)
        if order is None:
            order = grouped[reference] = ParsedOrder(order_number=reference, document_type=document_type)
            if reference:
                order.references.append(reference)
        order.lines.append(line)
    orders = list(grouped.values())
    if orders:
        # Report each ignored row with the order it was printed under (the first order when unknown).
        for row in ignored:
            value = row.get("order_number")
            reference = canonical_reference(value, bare_prefix) if value else fallback_reference
            grouped.get(reference, orders[0]).ignored.append(row)
    elif ignored:
        orders.append(ParsedOrder(order_number=fallback_reference, document_type=document_type, ignored=list(ignored)))
    for order in orders:
        if not order.order_number:
            order.warnings.append("No order number was found; enter one before quoting.")
    return orders


def reference_from_filename(filename: str, bare_prefix: str) -> str | None:
    stem = Path(filename).stem
    match = re.search(r"\b(?:SO|INV)?[\s#-]*\d{4,7}\b", stem, re.IGNORECASE)
    return canonical_reference(match.group(0), bare_prefix) if match else None


class TableAdapter:
    name = "table"
    label = "Spreadsheet or table"
    description = "CSV, TSV or Excel files (and table-based PDFs) with SKU and quantity columns."

    def score(self, context: ImportContext) -> float:
        if context.suffix in {".csv", ".tsv", ".txt", ".xlsx", ".xlsm"}:
            return 0.6
        pdf = context.pdf()
        if pdf and any(page.tables for page in pdf.pages):
            aliases = header_aliases(context.config)
            if any(find_header(table, aliases) for page in pdf.pages for table in page.tables):
                return 0.35
        return 0.0

    def parse(self, context: ImportContext) -> ParseOutcome:
        outcome = ParseOutcome(adapter=self.name)
        aliases = header_aliases(context.config)
        prefix = getattr(context.config, "bare_number_prefix", "")
        default_state = getattr(context.config, "default_packing_state", PackingState.FLATPACK)
        sources: list[tuple[str, list[list[Any]]]] = []
        try:
            if context.suffix in {".csv", ".tsv", ".txt"}:
                sources = [(context.filename, _csv_grid(context.content))]
            elif context.suffix in {".xlsx", ".xlsm"}:
                sources = [
                    (f"{context.filename} · {sheet}", grid) for sheet, grid in xlsx_bytes_to_grids(context.content)
                ]
            elif context.is_pdf:
                pdf = context.pdf()
                if pdf is None:
                    return ParseOutcome(adapter=self.name, status="failed",
                                        messages=[context.pdf_error or "Unreadable PDF"])
                sources = [
                    (f"{context.filename} · page {page.page_number}, table {number}", table)
                    for page in pdf.pages
                    for number, table in enumerate(page.tables, start=1)
                ]
        except Exception as exc:
            return ParseOutcome(adapter=self.name, status="failed",
                                messages=[f"{context.filename} could not be read: {exc.__class__.__name__}: {exc}"])
        pairs: list[tuple[str | None, ParsedLine]] = []
        ignored: list[dict[str, Any]] = []
        recognised = False
        for source_ref, grid in sources:
            found_pairs, found_ignored, has_header = rows_to_lines(grid, source_ref, aliases, default_state)
            recognised = recognised or has_header
            pairs.extend(found_pairs)
            ignored.extend(found_ignored)
        if not recognised:
            outcome.status = "unsupported"
            outcome.messages.append(
                "No SKU and quantity columns were recognised. Add the column names under Company & Import "
                "Settings → Column names, or use a supported layout."
            )
            return outcome
        outcome.orders = group_orders(
            pairs, ignored, reference_from_filename(context.filename, prefix), prefix,
            "spreadsheet" if not context.is_pdf else "pdf_table",
        )
        if not pairs:
            outcome.messages.append("The file was read, but no physical item rows with a positive quantity were found.")
        return outcome
