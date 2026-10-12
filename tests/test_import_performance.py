"""A realistic import batch stays fast.

40 files (30 Highland PDFs, several combined or running onto a second page, plus 10
CSV exports) carrying 1,500 item lines are imported in one request. The bound is
deliberately generous so the test is stable on slow machines; the measured time is
printed (run with ``-s`` or ``-rA`` to see it).
"""

from __future__ import annotations

import time

import pytest
from app.services.order_import import OrderImporter
from fixtures_freight import HighlandDoc, catalog_item, csv_bytes, highland_pdf, make_repo, seed_catalog

SKUS = [f"{family}{width}" for family in ("B", "W", "DB", "SB", "WC") for width in range(9, 45, 3)]  # 60 SKUs
LIMIT_SECONDS = 20.0


def _lines(seed: int, count: int) -> list[tuple[str, str, str, str]]:
    rows = []
    for index in range(count):
        sku = SKUS[(seed * 7 + index) % len(SKUS)]
        quantity = str((seed + index) % 4 + 1)
        rows.append((quantity, f"WHS-{sku}" if index % 5 == 0 else sku, f"{sku} shaker cabinet", quantity))
    return rows


def build_batch() -> tuple[list[tuple[str, bytes]], int, int]:
    files: list[tuple[str, bytes]] = []
    lines = orders = 0
    for number in range(30):
        invoice = 70000 + number * 2
        if number % 5 == 0:  # a combined PDF holding two invoices
            first, second = _lines(number, 17), _lines(number + 100, 18)
            content = highland_pdf(HighlandDoc(invoice=str(invoice), items=first),
                                   HighlandDoc(invoice=str(invoice + 1), kind="Packing Slip", items=second))
            lines += len(first) + len(second)
            orders += 2
        else:  # one invoice; 35 rows run onto a second page
            items = _lines(number, 35)
            assembly = "YES" if number % 2 else "NO"
            content = highland_pdf(HighlandDoc(invoice=str(invoice), items=items, assembly=assembly))
            lines += len(items)
            orders += 1
        files.append((f"{invoice}.pdf", content))
    for number in range(10):
        rows = [["SO Number", "Item", "Description", "Qty"]]
        for group in range(3):  # three sales orders per export
            rows += [[f"SO{78000 + number * 3 + group}", sku, desc, qty] for qty, sku, desc, _ in
                     _lines(number * 3 + group + 500, 15)]
        files.append((f"export-{number}.csv", csv_bytes(rows)))
        lines += 45
        orders += 3
    return files, lines, orders


@pytest.mark.performance
def test_large_mixed_batch_imports_quickly(tmp_path):
    repo = make_repo(tmp_path, seed=False)
    seed_catalog(repo, [catalog_item(sku, assembled=(24, 24, 34.5)) for sku in SKUS])
    files, expected_lines, expected_orders = build_batch()
    assert len(files) == 40 and expected_lines == 1500

    started = time.perf_counter()
    result = OrderImporter(repo, repo.default_company()).import_files(files)
    elapsed = time.perf_counter() - started

    imported_lines = sum(len(order["lines"]) for order in result["orders"])
    print(f"\nimport performance: {len(files)} files, {expected_orders} orders, {imported_lines} lines "
          f"in {elapsed:.2f} s ({imported_lines / elapsed:.0f} lines/s; reported batch time "
          f"{result['summary']['seconds']} s)")
    assert result["summary"]["created"] == expected_orders
    assert result["summary"]["failed"] == 0 and result["summary"]["conflicts"] == 0
    assert imported_lines == expected_lines
    assert all(order["status"] == "ready" for order in result["orders"])
    assert elapsed < LIMIT_SECONDS

    started = time.perf_counter()
    again = OrderImporter(repo, repo.default_company()).import_files(files)
    repeat = time.perf_counter() - started
    print(f"import performance: re-import of the same 40 files (all duplicates) in {repeat:.2f} s")
    assert again["summary"]["created"] == 0
    assert {row["status"] for row in again["files"]} == {"duplicate"}
    assert repeat < LIMIT_SECONDS
