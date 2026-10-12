"""Batch import of packing slips, invoices and sales orders into stored orders.

Guarantees:

* every uploaded file is hashed and its original bytes kept under ``data/source_files``;
* the same file imported again never creates a second order (duplicate by SHA-256);
* a different file for an order that already exists never overwrites it silently —
  identical lines are reported as "already imported", different lines as "changed"
  with the parsed lines returned so the user can choose to replace them (audited);
* rows that are not physical products are listed with a reason, never dropped silently;
* unreadable, scanned or unrecognised documents are reported per file.
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
import zipfile
import zlib
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from ..freight_models import Company, Order, OrderLine, SourceFile
from ..models import AddressInfo, MatchStatus, PackingSlipLine, PackingState
from .adapters import ImportContext, ParsedOrder, choose_adapter
from .adapters.base import canonical_reference, references_in_text
from .product_exceptions import identity_key
from .sku_matching import is_truncated_sku, match_lines

log = logging.getLogger("quote_helper.imports")

SUPPORTED_SUFFIXES = {".pdf", ".csv", ".tsv", ".txt", ".xlsx", ".xlsm"}
MAX_FILES_PER_BATCH = 100
MAX_ZIP_MEMBERS = 100
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024


def expand_uploads(files: list[tuple[str, bytes]]) -> tuple[list[tuple[str, bytes]], list[dict[str, Any]]]:
    """Unpack ZIP files (one level), skipping folders, hidden files and unsupported types."""

    expanded: list[tuple[str, bytes]] = []
    rejected: list[dict[str, Any]] = []
    for filename, content in files:
        suffix = Path(filename).suffix.lower()
        if suffix == ".zip":
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    members = [info for info in archive.infolist() if not info.is_dir()]
                    if len(members) > MAX_ZIP_MEMBERS:
                        rejected.append({"file": filename, "status": "failed",
                                         "messages": [f"ZIP has more than {MAX_ZIP_MEMBERS} files."]})
                        continue
                    if sum(info.file_size for info in members) > MAX_ZIP_UNCOMPRESSED:
                        rejected.append({"file": filename, "status": "failed",
                                         "messages": ["ZIP expands to more than 200 MB."]})
                        continue
                    for info in members:
                        name = PurePosixPath(info.filename)
                        if name.name.startswith(".") or "__MACOSX" in name.parts:
                            continue
                        if name.suffix.lower() not in SUPPORTED_SUFFIXES:
                            rejected.append({"file": f"{filename}/{info.filename}", "status": "unsupported",
                                             "messages": ["File type is not supported (PDF, CSV, TSV, XLSX)."]})
                            continue
                        try:
                            data = archive.read(info)
                        except (RuntimeError, NotImplementedError, zlib.error, zipfile.BadZipFile) as exc:
                            rejected.append({"file": f"{filename}/{info.filename}", "status": "failed",
                                             "messages": [f"Could not extract this file from the ZIP "
                                                          f"({exc.__class__.__name__}: it may be encrypted)."]})
                            continue
                        expanded.append((f"{Path(filename).stem}/{name.name}", data))
            except zipfile.BadZipFile:
                rejected.append({"file": filename, "status": "failed", "messages": ["ZIP archive is damaged."]})
            continue
        if suffix not in SUPPORTED_SUFFIXES:
            rejected.append({"file": filename, "status": "unsupported",
                             "messages": ["File type is not supported (PDF, CSV, TSV, XLSX, ZIP)."]})
            continue
        expanded.append((filename, content))
    return expanded, rejected


def _line_key(line: OrderLine) -> tuple:
    return (
        line.original_sku.strip().upper(), line.quantity, line.packing_state.value, line.description.strip().upper()
    )


def same_lines(left: list[OrderLine], right: list[OrderLine]) -> bool:
    return Counter(map(_line_key, left)) == Counter(map(_line_key, right))


def line_differences(old: list[OrderLine], new: list[OrderLine]) -> list[str]:
    before = Counter(
        (line.original_sku.upper(), line.packing_state.value) for line in old for _ in range(line.quantity)
    )
    after = Counter(
        (line.original_sku.upper(), line.packing_state.value) for line in new for _ in range(line.quantity)
    )
    changes = []
    for key in sorted(set(before) | set(after)):
        if before[key] != after[key]:
            changes.append(f"{key[0]} ({key[1]}): {before[key]} → {after[key]}")
    return changes


def _code_key(sku: str) -> str:
    return identity_key(sku.rstrip(". \u2026"))


def _same_goods(old: OrderLine, new: OrderLine) -> bool:
    """One line of the same goods, where either side's item code may be shortened ('NS-PC309024...')."""

    if (old.quantity, old.packing_state) != (new.quantity, new.packing_state):
        return False
    if identity_key(old.description) != identity_key(new.description):
        return False
    before, after = _code_key(old.original_sku), _code_key(new.original_sku)
    old_short, new_short = is_truncated_sku(old.original_sku), is_truncated_sku(new.original_sku)
    if old_short and not new_short:
        return after.startswith(before)
    if new_short and not old_short:
        return before.startswith(after)
    return before == after


def same_goods_allowing_short_codes(old: list[OrderLine], new: list[OrderLine]) -> bool:
    """The invoice and the packing slip of one order list the same lines; the slip shortens long codes."""

    if len(old) != len(new):
        return False
    remaining = list(new)
    for line in old:
        partner = next((candidate for candidate in remaining if _same_goods(line, candidate)), None)
        if partner is None:
            return False
        remaining.remove(partner)
    return True


def _short_codes(lines: list[OrderLine]) -> int:
    return sum(1 for line in lines if is_truncated_sku(line.original_sku))


def _to_order_lines(parsed: ParsedOrder) -> list[OrderLine]:
    return [
        OrderLine(
            line_number=line.line_number,
            original_sku=line.original_sku,
            description=line.description,
            quantity=line.quantity,
            packing_state=line.packing_state or parsed.packing_state,
            length=line.length, width=line.width, height=line.height, weight=line.weight,
            source_ref=line.source_ref,
        )
        for line in parsed.lines
    ]


def _address(values: dict[str, str]) -> AddressInfo:
    return AddressInfo(**{key: str(value) for key, value in values.items() if key in AddressInfo.model_fields})


class OrderImporter:
    def __init__(self, repository, company: Company, storage_root: Path | None = None) -> None:
        self.repository = repository
        self.company = company
        self.storage_root = storage_root or Path(repository.database_path).parent / "source_files"
        self.settings = repository.company_settings(company)

    # -------------------------------------------------------------- helpers

    def _store_original(self, sha256: str, filename: str, content: bytes) -> str:
        target = self.storage_root / sha256[:2] / f"{sha256}{Path(filename).suffix.lower()}"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".part")
            temporary.write_bytes(content)
            temporary.replace(target)
        return str(target)

    def _catalog_issues(self, orders: list[Order]) -> None:
        """Flag SKUs the catalog cannot resolve, so problems surface at import time."""

        lines = [
            PackingSlipLine(id=line.id, source_ref=order.order_number, original_sku=line.original_sku,
                            quantity=line.quantity, packing_state=line.packing_state, description=line.description,
                            length=line.length, width=line.width, height=line.height, weight=line.weight)
            for order in orders for line in order.lines
        ]
        if not lines:
            return
        catalog = self.company.config.catalog
        matches = match_lines(
            lines,
            self.repository.catalog_items(catalog),
            ignored_prefixes=self.settings.sku_prefixes_to_ignore,
            fuzzy_threshold=self.settings.fuzzy_match_threshold,
            confirmed_aliases=self.repository.catalog_alias_targets(catalog),
            rule_set=self.company.config.rule_set,
        )
        by_line = {match.line.id: match for match in matches}
        for order in orders:
            unmatched = sorted({by_line[line.id].line.original_sku for line in order.lines
                                if by_line[line.id].match_status == MatchStatus.UNMATCHED})
            fuzzy = sorted({by_line[line.id].line.original_sku for line in order.lines
                            if by_line[line.id].match_status == MatchStatus.FUZZY})
            if unmatched:
                order.issues.append(f"{len(unmatched)} SKU(s) not in the catalog: {', '.join(unmatched[:6])}"
                                    + (" …" if len(unmatched) > 6 else ""))
            if fuzzy:
                order.issues.append(f"{len(fuzzy)} SKU(s) only roughly match the catalog: {', '.join(fuzzy[:6])}")

    # -------------------------------------------------------------- main entry

    def import_files(self, uploads: list[tuple[str, bytes]]) -> dict[str, Any]:
        started = time.perf_counter()
        batch_id = str(uuid4())
        expanded, rejected = expand_uploads(uploads)
        if len(expanded) > MAX_FILES_PER_BATCH:
            raise ValueError(f"Import at most {MAX_FILES_PER_BATCH} files at once.")
        file_results: list[dict[str, Any]] = list(rejected)
        created: list[Order] = []
        updated: list[Order] = []
        existing_hits: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        batch_numbers: dict[str, tuple[Order, str]] = {}
        pending: list[tuple[SourceFile, list[tuple[ParsedOrder, Order]]]] = []

        for filename, content in expanded:
            sha256 = hashlib.sha256(content).hexdigest()
            entry: dict[str, Any] = {"file": filename, "sha256": sha256, "size": len(content)}
            if len(content) > MAX_FILE_BYTES:
                file_results.append({**entry, "status": "failed", "messages": ["File is larger than 50 MB."]})
                continue
            previous = self.repository.find_source_file(self.company.id, sha256)
            if previous:
                orders = [order for order in self.repository.get_orders(
                    [oid for oid in previous.order_ids if self.repository.get_order(oid)])]
                file_results.append({
                    **entry, "status": "duplicate", "adapter": previous.adapter, "order_ids": previous.order_ids,
                    "messages": [f"Already imported on {previous.created_at:%Y-%m-%d} as {previous.filename}; "
                                 "no new order was created."],
                })
                existing_hits.extend({"order_id": order.id, "order_number": order.order_number,
                                      "file": filename, "reason": "same file"} for order in orders)
                continue
            context = ImportContext(filename=filename, content=content, config=self.company.config)
            adapter, score = choose_adapter(context, self.company.config.adapters)
            if adapter is None:
                if context.is_pdf and context.pdf_error:
                    status, message = "failed", context.pdf_error
                elif context.is_pdf and context.pdf() is not None and not context.pdf().has_text:
                    status, message = "unsupported", ("This PDF has no text layer (it looks scanned). OCR is not "
                                                      "installed; enter the lines by hand or request a digital copy.")
                else:
                    status, message = "unsupported", ("The layout was not recognised by any document format enabled "
                                                      f"for {self.company.name}. Check Company & Import Settings.")
                file_results.append({**entry, "status": status, "messages": [message]})
                continue
            try:
                outcome = adapter.parse(context)
            except Exception as exc:  # an adapter bug must not abort the batch
                log.exception("Adapter %s failed on %s", adapter.name, filename)
                file_results.append({**entry, "status": "failed", "adapter": adapter.name,
                                     "messages": [
                                         f"{adapter.label} could not read this file ({exc.__class__.__name__})."
                                     ]})
                continue
            source = SourceFile(company_id=self.company.id, batch_id=batch_id, filename=filename, sha256=sha256,
                                size=len(content), adapter=adapter.name, messages=list(outcome.messages))
            if outcome.status != "ok" or not outcome.orders:
                file_results.append({**entry, "status": outcome.status if outcome.status != "ok" else "unsupported",
                                     "adapter": adapter.name,
                                     "messages": outcome.messages or ["No orders were found."]})
                continue
            source.stored_path = self._store_original(sha256, filename, content)
            pairs: list[tuple[ParsedOrder, Order]] = []
            for index, parsed in enumerate(outcome.orders, start=1):
                number = parsed.order_number or f"PENDING-{sha256[:8]}-{index}"
                issues = list(parsed.warnings)
                unclear = [row for row in parsed.ignored
                           if row.get("reason_code") not in {"NON_PHYSICAL_ROW", "SECTION_HEADING"}]
                if unclear:
                    # Fee, freight and heading rows are expected on every invoice; only rows whose
                    # quantity could not be read need a person to look at them.
                    issues.append(f"{len(unclear)} row(s) had no usable quantity and were not imported: "
                                  + ", ".join(str(row.get("original_sku")) for row in unclear[:5]))
                order = Order(
                    company_id=self.company.id,
                    order_number=number,
                    references=[ref for ref in parsed.references if ref and ref != number],
                    customer_name=parsed.customer_name, po_number=parsed.po_number, order_date=parsed.order_date,
                    ready_date=parsed.ready_date, sales_rep=parsed.sales_rep, ship_to=_address(parsed.ship_to),
                    residential=parsed.residential, liftgate=parsed.liftgate, packing_state=parsed.packing_state,
                    lines=_to_order_lines(parsed), document_type=parsed.document_type, source_file_id=source.id,
                    issues=issues,
                )
                pairs.append((parsed, order))
            pending.append((source, pairs))

        self._catalog_issues([order for _, pairs in pending for _, order in pairs])

        with self.repository.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for source, pairs in pending:
                order_ids: list[str] = []
                file_messages = list(source.messages)
                ignored_rows: list[dict[str, Any]] = []
                for parsed, order in pairs:
                    ignored_rows.extend(parsed.ignored)
                    order.status = "ready" if order.lines and not order.issues and not order.order_number.startswith(
                        "PENDING-") else "needs_review"
                    key = order.order_number.upper()
                    if key in batch_numbers:
                        first, first_file = batch_numbers[key]
                        if same_lines(first.lines, order.lines):
                            file_messages.append(f"{order.order_number} also appears in {first_file}; imported once.")
                        elif same_goods_allowing_short_codes(first.lines, order.lines):
                            new_in_batch = any(created_order.id == first.id for created_order in created)
                            if new_in_batch and _short_codes(order.lines) < _short_codes(first.lines):
                                first.lines = order.lines
                                first.references = list(dict.fromkeys([*first.references, *order.references]))
                                self.repository._write_order(connection, first)
                            file_messages.append(f"{order.order_number} also appears in {first_file} (same goods); "
                                                 "imported once.")
                        else:
                            note = (f"{source.filename} lists {order.order_number} with different lines: "
                                    + "; ".join(line_differences(first.lines, order.lines)[:5]))
                            file_messages.append(note)
                            conflicts.append({"order_id": first.id, "order_number": first.order_number,
                                              "file": source.filename,
                                              "differences": line_differences(first.lines, order.lines),
                                              "lines": [line.model_dump(mode="json") for line in order.lines]})
                        order_ids.append(first.id)
                        continue
                    # Identity is the document's own number. A shared secondary reference (the sales
                    # order behind two invoices, a P.O.) only fills a reference-only placeholder;
                    # it never makes two different invoices the same order.
                    existing = self.repository.find_order_by_reference(
                        connection, self.company.id, [order.order_number])
                    if existing is None:
                        related = self.repository.find_order_by_reference(
                            connection, self.company.id, order.references)
                        if related and related.status == "reference_only" and not related.lines:
                            existing = related
                        elif related:
                            order.issues.append(
                                f"Shares reference {', '.join(r for r in order.references if r)} with order "
                                f"{related.order_number}; check that both aren't the same goods.")
                            order.status = "needs_review"
                    if existing and existing.status == "reference_only" and not existing.lines:
                        imported = order.model_dump(exclude={"id", "company_id", "created_at", "order_number",
                                                             "revision"})
                        # Keep what the freight manager typed on the placeholder when the document is silent.
                        for name in ("customer_name", "po_number", "order_date", "ready_date", "sales_rep",
                                     "notes", "residential", "liftgate"):
                            if imported[name] in ("", None):
                                imported[name] = getattr(existing, name)
                        if not order.ship_to.postal_code and existing.ship_to.postal_code:
                            imported["ship_to"] = existing.ship_to.model_dump()
                        merged = Order.model_validate({
                            **existing.model_dump(),
                            **imported,
                            "references": list(dict.fromkeys([*existing.references, *order.references])),
                            "revision": existing.revision + 1,
                        })
                        self.repository._write_order(connection, merged)
                        self.repository.audit(connection, "order", merged.id, "lines_imported",
                                              f"Lines imported from {source.filename}", {"units": merged.unit_count})
                        updated.append(merged)
                        batch_numbers[key] = (merged, source.filename)
                        order_ids.append(merged.id)
                    elif existing:
                        batch_numbers[key] = (existing, source.filename)
                        order_ids.append(existing.id)
                        if same_lines(existing.lines, order.lines):
                            existing_hits.append({"order_id": existing.id, "order_number": existing.order_number,
                                                  "file": source.filename, "reason": "same lines"})
                            file_messages.append(f"{existing.order_number} was already imported with the same lines.")
                        elif same_goods_allowing_short_codes(existing.lines, order.lines):
                            # The packing slip and the invoice of one order: same goods, but the slip
                            # shortens long item codes. Keep the complete codes; never change quantities.
                            if (_short_codes(order.lines) < _short_codes(existing.lines)
                                    and existing.status != "shipped"):
                                upgraded = Order.model_validate({
                                    **existing.model_dump(),
                                    "lines": [line.model_dump() for line in order.lines],
                                    "references": list(dict.fromkeys([*existing.references, *order.references])),
                                    "revision": existing.revision + 1,
                                })
                                for name in ("customer_name", "po_number", "order_date", "ready_date", "sales_rep"):
                                    if not getattr(upgraded, name) and getattr(order, name):
                                        setattr(upgraded, name, getattr(order, name))
                                self.repository._write_order(connection, upgraded)
                                self.repository.audit(
                                    connection, "order", upgraded.id, "codes_completed",
                                    f"Complete item codes taken from {source.filename}",
                                    {"before": [line.original_sku for line in existing.lines],
                                     "after": [line.original_sku for line in order.lines]})
                                updated.append(upgraded)
                                batch_numbers[key] = (upgraded, source.filename)
                                file_messages.append(f"{existing.order_number}: same goods as the stored packing "
                                                     f"slip; complete item codes taken from {source.filename}.")
                            else:
                                existing_hits.append({"order_id": existing.id,
                                                      "order_number": existing.order_number,
                                                      "file": source.filename, "reason": "same lines"})
                                file_messages.append(f"{existing.order_number} was already imported with the same "
                                                     "goods (the packing slip shortens some item codes).")
                        else:
                            differences = line_differences(existing.lines, order.lines)
                            conflicts.append({"order_id": existing.id, "order_number": existing.order_number,
                                              "file": source.filename, "differences": differences,
                                              "lines": [line.model_dump(mode="json") for line in order.lines]})
                            file_messages.append(f"{existing.order_number} already exists with different lines; "
                                                 "the stored order was not changed.")
                    else:
                        self.repository.insert_order(connection, order,
                                                     f"Imported from {source.filename} ({source.adapter})")
                        created.append(order)
                        batch_numbers[key] = (order, source.filename)
                        order_ids.append(order.id)
                source.order_ids = list(dict.fromkeys(order_ids))
                source.messages = file_messages
                source.status = "imported" if all(o.lines for _, o in pairs) else "partial"
                self.repository.save_source_file(connection, source)
                file_results.append({
                    "file": source.filename, "sha256": source.sha256, "size": source.size, "status": source.status,
                    "adapter": source.adapter, "order_ids": source.order_ids, "messages": file_messages,
                    "ignored_rows": ignored_rows,
                })
            summary = {
                "files": len(file_results),
                "created": len(created), "updated": len(updated), "existing": len(existing_hits),
                "conflicts": len(conflicts),
                "failed": sum(1 for row in file_results if row["status"] in {"failed", "unsupported"}),
                "seconds": round(time.perf_counter() - started, 3),
            }
            self.repository.save_batch(connection, batch_id, self.company.id, {**summary, "files_detail": [
                {k: v for k, v in row.items() if k != "ignored_rows"} for row in file_results]})
        log.info("Import batch %s: %s", batch_id, summary)
        touched = list(dict.fromkeys([o.id for o in created] + [o.id for o in updated]
                                     + [hit["order_id"] for hit in existing_hits]
                                     + [c["order_id"] for c in conflicts]))
        return {
            "batch_id": batch_id,
            "company_id": self.company.id,
            "summary": summary,
            "files": file_results,
            "created_order_ids": [o.id for o in created],
            "updated_order_ids": [o.id for o in updated],
            "existing": existing_hits,
            "conflicts": conflicts,
            "orders": [order.model_dump(mode="json") for order in self.repository.get_orders(touched)],
        }


# ------------------------------------------------------------------ order references & packing lines


def ensure_orders_for_references(repository, company: Company, references: list[str], *, reason: str,
                                 history: bool = False) -> list[Order]:
    """Find orders by typed reference, creating reference-only orders for unknown ones."""

    canonical = [ref for ref in (canonical_reference(r, company.config.bare_number_prefix) for r in references) if ref]
    orders: list[Order] = []
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for reference in dict.fromkeys(canonical):
            found = repository.find_order_by_reference(connection, company.id, [reference])
            if found is None:
                # "history" marks orders recreated from quotes saved before 3.0, so they don't
                # crowd the list of current work.
                found = Order(company_id=company.id, order_number=reference, status="reference_only",
                              document_type="history" if history else "reference",
                              issues=["Reference only: no packing slip has been imported."])
                repository.insert_order(connection, found, reason)
            orders.append(found)
    return orders


def references_from_name(company: Company, text: str) -> list[str]:
    return references_in_text(text, company.config.bare_number_prefix)


def packing_lines_for_orders(orders: list[Order]) -> tuple[list[PackingSlipLine], list[str]]:
    """Turn stored orders into builder lines, prefixing the source with the order number.

    The prefix lets a packed plan attribute every carton back to its order (for weight
    and pallet-based expense allocation). Returns (lines, warnings).
    """

    warnings: list[str] = []
    if len({order.company_id for order in orders}) > 1:
        raise ValueError("Orders from different companies cannot be quoted together.")
    zips = {order.ship_to.postal_code for order in orders if order.ship_to.postal_code}
    if len(zips) > 1:
        warnings.append(
            f"Orders ship to different ZIP codes ({', '.join(sorted(zips))}); confirm they travel together."
        )
    states = {line.packing_state for order in orders for line in order.lines}
    if len(states) > 1:
        warnings.append("Orders mix flatpack and assembled freight; assembled pallets use the assembled height limit.")
    for order in orders:
        if not order.lines:
            warnings.append(f"{order.order_number} has no imported lines.")
        if order.status == "needs_review":
            warnings.append(f"{order.order_number} still needs review: {'; '.join(order.issues[:2])}")
    lines = [
        PackingSlipLine(
            source_ref=f"{order.order_number} · {line.source_ref or 'entered'}",
            original_sku=line.original_sku,
            quantity=line.quantity,
            packing_state=line.packing_state or PackingState.FLATPACK,
            description=line.description,
            length=line.length, width=line.width, height=line.height, weight=line.weight,
            line_number=line.line_number,
        )
        for order in orders for line in order.lines
    ]
    return lines, warnings


def refresh_order_issues(repository, company: Company, order: Order) -> Order:
    """Recompute catalog-related issues after a correction; keep the user's other notes."""

    kept = [issue for issue in order.issues
            if "not in the catalog" not in issue and "roughly match the catalog" not in issue
            and not issue.startswith("Reference only")]
    order.issues = kept
    importer = OrderImporter(repository, company)
    importer._catalog_issues([order])
    return order


def dry_run(company: Company, uploads: list[tuple[str, bytes]]) -> dict[str, Any]:
    """Parse files with a company's (possibly unsaved) configuration; nothing is stored."""

    expanded, rejected = expand_uploads(uploads)
    files: list[dict[str, Any]] = [{**row, "orders": [], "adapter": None} for row in rejected]
    for filename, content in expanded[:MAX_FILES_PER_BATCH]:
        context = ImportContext(filename=filename, content=content, config=company.config)
        adapter, score = choose_adapter(context, company.config.adapters)
        if adapter is None:
            message = context.pdf_error if context.is_pdf and context.pdf_error else (
                "No enabled document format recognised this file.")
            files.append({"file": filename, "status": "unsupported", "adapter": None, "messages": [message],
                          "orders": []})
            continue
        try:
            outcome = adapter.parse(context)
        except Exception as exc:  # pragma: no cover - reported to the user
            files.append({"file": filename, "status": "failed", "adapter": adapter.name,
                          "messages": [f"{exc.__class__.__name__}: {exc}"], "orders": []})
            continue
        files.append({
            "file": filename, "status": outcome.status, "adapter": adapter.name, "confidence": round(score, 2),
            "messages": outcome.messages,
            "orders": [{
                "order_number": order.order_number, "document_type": order.document_type,
                "line_count": len(order.lines), "unit_count": sum(line.quantity for line in order.lines),
                "customer_name": order.customer_name, "zip": order.ship_to.get("postal_code", ""),
                "pages": order.pages, "ignored": len(order.ignored), "warnings": order.warnings,
                "lines": [{"sku": line.original_sku, "quantity": line.quantity, "description": line.description}
                          for line in order.lines[:50]],
            } for order in outcome.orders],
        })
    return {"files": files}
