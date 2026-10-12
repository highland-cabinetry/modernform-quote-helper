"""Product catalog validation, import preview/apply and export.

Bulk updates are two-step: a preview classifies every row (new, changed with field
differences, unchanged, invalid, conflicting) and is held for 30 minutes; applying it
writes all accepted rows in one transaction, so a catalog import is never half applied.
"""

from __future__ import annotations

import csv
import io
import threading
import time
from collections import Counter
from typing import Any
from uuid import uuid4

from ..models import CabinetColor, ItemMaster, PackingState
from .imports import parse_cabinet_colors, parse_item_master_with_report
from .reports import item_master_template_xlsx, simple_catalog_csv, simple_catalog_xlsx
from .sku_matching import normalize_sku

PREVIEW_TTL_SECONDS = 30 * 60
COMPARE_EXCLUDE = {"id"}


def _round(value: Any) -> Any:
    return round(value, 4) if isinstance(value, float) else value


def item_differences(before: ItemMaster, after: ItemMaster) -> dict[str, list[Any]]:
    old = before.model_dump(mode="json", exclude=COMPARE_EXCLUDE)
    new = after.model_dump(mode="json", exclude=COMPARE_EXCLUDE)
    return {key: [old.get(key), value] for key, value in new.items() if _round(old.get(key)) != _round(value)}


def item_issues(item: ItemMaster) -> list[dict[str, str]]:
    """Data-quality findings for one item. Errors block nothing by themselves; they flag work."""

    issues: list[dict[str, str]] = []

    def add(severity: str, code: str, message: str) -> None:
        issues.append({"severity": severity, "code": code, "message": message})

    flat = item.dimensions_for(PackingState.FLATPACK)
    assembled = item.dimensions_for(PackingState.ASSEMBLED)
    if flat is None:
        add("warning", "NO_FLATPACK_DIMENSIONS", "No flatpack carton size: flatpack lines cannot be packed.")
    if assembled is None:
        add("warning", "NO_ASSEMBLED_DIMENSIONS", "No assembled carton size: assembled lines cannot be packed.")
    for label, dims in (("Flatpack", flat), ("Assembled", assembled)):
        if dims and max(dims) > 120:
            add("warning", "OVERSIZE", f"{label} size {max(dims):g} in exceeds 120 in; check the unit (inches).")
        if dims and min(dims) < 0.25:
            add("warning", "TINY_DIMENSION", f"{label} size has a dimension under 0.25 in.")
    if item.weight > 400:
        add("warning", "HEAVY", f"Unit weight {item.weight:g} lb is unusually high; check pounds per unit.")
    if flat and assembled and item.item_kind.value == "cabinet" and flat[2] > assembled[2]:
        add("info", "FLATPACK_TALLER", "Flatpack carton is taller than the assembled carton.")
    if not item.description:
        add("info", "NO_DESCRIPTION", "Description is empty.")
    if not item.rules_reviewed:
        add("info", "RULES_NOT_REVIEWED", "Handling and load-bearing defaults have not been warehouse-reviewed.")
    return issues


def catalog_report(items: list[ItemMaster], ignored_prefixes: list[str]) -> dict[str, Any]:
    keys = Counter()
    for item in items:
        values = {
            normalize_sku(item.sku, ignored_prefixes),
            normalize_sku(item.manufacturer_sku or "", ignored_prefixes),
        }
        for value in values:
            if value:
                keys[value] += 1
    rows = []
    for item in items:
        issues = item_issues(item)
        clashes = [value for value in {normalize_sku(item.sku, ignored_prefixes),
                                       normalize_sku(item.manufacturer_sku or "", ignored_prefixes)}
                   if value and keys[value] > 1]
        if clashes:
            issues.append({"severity": "error", "code": "AMBIGUOUS_SKU",
                           "message": f"Another item normalizes to the same SKU ({', '.join(clashes)}); "
                                      "lines with this SKU will need manual review."})
        if issues:
            rows.append({"id": item.id, "sku": item.sku, "description": item.description, "issues": issues})
    counts = Counter(issue["code"] for row in rows for issue in row["issues"])
    return {
        "items": len(items),
        "items_with_issues": sum(1 for row in rows if any(i["severity"] != "info" for i in row["issues"])),
        "counts": dict(counts),
        "rows": rows,
    }


class CatalogImports:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._previews: dict[str, dict[str, Any]] = {}

    def _prune(self) -> None:
        cutoff = time.monotonic() - PREVIEW_TTL_SECONDS
        for key in [key for key, value in self._previews.items() if value["created"] < cutoff]:
            del self._previews[key]

    def preview(self, repository, catalog: str, filename: str, content: bytes,
                ignored_prefixes: list[str]) -> dict[str, Any]:
        from ..edition import current as current_edition

        colors = [CabinetColor(name=name, prefix=prefix) for name, prefix in parse_cabinet_colors(filename, content)
                  ] if current_edition().name != "highland" else []
        if colors and colors == cabinet_colors(repository, catalog):
            colors = []   # unchanged
        try:
            parsed = parse_item_master_with_report(filename, content)
        except ValueError:
            if not colors:
                raise
            parsed = None
        current = {item.sku.upper(): item for item in repository.catalog_items(catalog, active_only=False)}
        parsed_items = parsed.items if parsed else []
        # Simple workbooks update only their own columns (Highland keeps its upstream full-row behaviour).
        merge = current_edition().name != "highland"
        provided = (parsed.provided if merge and parsed and parsed.provided else [None] * len(parsed_items))
        seen = Counter(item.sku.upper() for item in parsed_items)
        manufacturer_owner = {
            normalize_sku(item.manufacturer_sku, ignored_prefixes): item.sku.upper()
            for item in current.values() if item.manufacturer_sku and item.active
        }
        rows: list[dict[str, Any]] = []
        accepted: list[ItemMaster] = []
        for item, fields in zip(parsed_items, provided, strict=True):
            key = item.sku.upper()
            existing = current.get(key)
            if existing is not None and fields is not None:
                # Only the columns in the file change; handling rules stay as they are.
                item = existing.model_copy(update={name: getattr(item, name) for name in fields if name != "id"})
            base = {"sku": item.sku, "description": item.description}
            if seen[key] > 1:
                rows.append({**base, "status": "conflict", "message": "SKU appears more than once in this file."})
                continue
            owner = manufacturer_owner.get(normalize_sku(item.manufacturer_sku or "", ignored_prefixes))
            if item.manufacturer_sku and owner and owner != key:
                rows.append({**base, "status": "conflict",
                             "message": f"Manufacturer SKU {item.manufacturer_sku} already belongs to {owner}."})
                continue
            existing = current.get(key)
            issues = [issue for issue in item_issues(item) if issue["severity"] != "info"]
            if existing is None:
                rows.append({**base, "status": "new", "issues": issues})
                accepted.append(item)
            else:
                differences = item_differences(existing, item)
                if differences:
                    rows.append({**base, "status": "changed", "changes": differences, "issues": issues})
                    accepted.append(item)
                else:
                    rows.append({**base, "status": "unchanged"})
        for skipped in (parsed.skipped if parsed else []):
            rows.append({"sku": skipped.get("sku") or "", "description": "", "status": "invalid",
                         "message": f"{skipped.get('sheet')} row {skipped.get('row')}: {skipped.get('reason')}"})
        missing = [item.sku for key, item in current.items() if item.active and key not in seen] if parsed else []
        counts = Counter(row["status"] for row in rows)
        preview_id = str(uuid4())
        with self._lock:
            self._prune()
            self._previews[preview_id] = {"created": time.monotonic(), "catalog": catalog, "items": accepted,
                                          "filename": filename, "colors": colors}
        return {
            "preview_id": preview_id,
            "filename": filename,
            "catalog": catalog,
            "counts": {
                status: counts.get(status, 0) for status in ("new", "changed", "unchanged", "invalid", "conflict")
            },
            "rows": rows,
            "not_in_file": missing,
            "colors": [color.model_dump() for color in colors],
            "can_apply": bool(accepted or colors),
        }

    def apply(self, repository, preview_id: str) -> dict[str, Any]:
        from ..freight_repository import NotFoundError

        with self._lock:
            self._prune()
            preview = self._previews.get(preview_id)
            if preview is None:
                raise NotFoundError("This import preview expired or was already applied. Upload the file again.")
            # Hold the lock while writing so the same preview cannot be applied twice; drop it
            # only after the transaction commits, so a failed apply can be retried.
            inserted, updated = (repository.catalog_upsert(preview["catalog"], preview["items"],
                                                           reason=f"import {preview['filename']}")
                                 if preview["items"] else (0, 0))
            if preview.get("colors"):
                save_cabinet_colors(repository, preview["catalog"], preview["colors"])
            self._previews.pop(preview_id, None)
        result = {"inserted": inserted, "updated": updated, "filename": preview["filename"]}
        if preview.get("colors"):
            result["colors"] = len(preview["colors"])
        return result


catalog_imports = CatalogImports()


def catalog_companies(repository, catalog: str) -> list:
    companies = [company for company in repository.list_companies() if company.config.catalog == catalog]
    return companies or [repository.default_company()]


def cabinet_colors(repository, catalog: str) -> list[CabinetColor]:
    return repository.company_settings(catalog_companies(repository, catalog)[0]).cabinet_colors


def save_cabinet_colors(repository, catalog: str, colors: list[CabinetColor]) -> None:
    """The workbook's Cabinet Colors replace the list of every company using this catalog."""
    for company in catalog_companies(repository, catalog):
        settings = repository.company_settings(company)
        settings.cabinet_colors = list(colors)
        repository.save_company_settings(company, settings.model_validate(settings.model_dump()))


def export_catalog(items: list[ItemMaster], export_format: str, colors: list[CabinetColor] | None = None,
                   simple: bool | None = None) -> tuple[bytes, str, str]:
    from ..edition import current as current_edition

    if simple is None:
        simple = current_edition().name != "highland"
    if simple:
        if export_format == "xlsx":
            return (simple_catalog_xlsx(items, colors or []),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx")
        return simple_catalog_csv(items), "text/csv; charset=utf-8", "csv"
    if export_format == "xlsx":
        return (item_master_template_xlsx(items),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx")
    headers = [name for name in ItemMaster.model_fields if name != "id"]
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers)
    for item in items:
        payload = item.model_dump(mode="json")
        writer.writerow([",".join(payload[name]) if name == "allowed_rotations" else payload[name] for name in headers])
    return stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "csv"
