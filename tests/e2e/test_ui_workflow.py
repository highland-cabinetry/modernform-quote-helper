"""Browser end-to-end check of the order -> quote -> shipment -> freight cost workflow.

Skipped unless QH_E2E=1 and the Python Playwright package (with Chromium) is installed.
Playwright is not a project dependency; install it once into the venv when needed:

    .venv\\Scripts\\python.exe -m pip install playwright
    .venv\\Scripts\\python.exe -m playwright install chromium

    # Windows (after Setup has built frontend\\dist):
    set QH_E2E=1
    .venv\\Scripts\\python.exe -m pytest tests\\e2e\\test_ui_workflow.py -q

    # With a different build of the interface:
    QH_E2E=1 QH_FRONTEND_DIST=/path/to/dist PYTHONPATH=backend:tests python -m pytest tests/e2e/test_ui_workflow.py

The test seeds a throwaway SQLite database, starts the real backend with uvicorn in a subprocess
on a free port, and drives the built interface. It never touches data/quote_helper_v2.sqlite3.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from decimal import Decimal
from pathlib import Path

import pytest

if os.environ.get("QH_E2E") != "1":
    pytest.skip("browser end-to-end test: set QH_E2E=1 to run", allow_module_level=True)

sync_api = pytest.importorskip("playwright.sync_api")

from app.database import Repository  # noqa: E402
from fixtures_freight import HighlandDoc, highland_pdf, seed_catalog  # noqa: E402

expect = sync_api.expect
PROJECT = Path(__file__).resolve().parents[2]
BACKEND = PROJECT / "backend"
ORDERS = ("INV71001", "INV71002", "INV71003")
UI_TIMEOUT_MS = 15_000
PACK_TIMEOUT_MS = 120_000
EXPECTED_CONSOLE = re.compile(r"Failed to load resource: the server responded with a status of 4\d\d")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    dist = Path(os.environ.get("QH_FRONTEND_DIST") or PROJECT / "frontend" / "dist").resolve()
    if not (dist / "index.html").is_file():
        pytest.fail(f"No built interface at {dist}. Build the frontend or set QH_FRONTEND_DIST.")
    database = tmp_path / "e2e.sqlite3"
    repo = Repository(database, tmp_path / "backups")
    repo.initialize()
    seed_catalog(repo)

    port = _free_port()
    env = {
        **os.environ,
        "QUOTE_HELPER_V2_DATABASE": str(database),
        "QUOTE_HELPER_FRONTEND_DIST": str(dist),
        "QUOTE_HELPER_REVIEW_MODE": "0",
        "PYTHONPATH": os.pathsep.join(filter(None, [str(BACKEND), os.environ.get("PYTHONPATH", "")])),
        "PYTHONUTF8": "1",
    }
    log_path = tmp_path / "server.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(BACKEND),
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            if process.poll() is not None:
                pytest.fail(f"uvicorn exited with {process.returncode}:\n{log_path.read_text(encoding='utf-8')}")
            try:
                with urllib.request.urlopen(f"{base}/api/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f"server did not become healthy:\n{log_path.read_text(encoding='utf-8')}")
            time.sleep(0.25)
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)
        log.close()


@pytest.fixture
def page(server):
    problems: list[str] = []
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(locale="en-US", timezone_id="America/Denver",
                                      viewport={"width": 1440, "height": 1000})
        context.set_default_timeout(UI_TIMEOUT_MS)
        tab = context.new_page()
        tab.on("console", lambda message: message.type == "error" and not EXPECTED_CONSOLE.search(message.text)
               and problems.append(f"console: {message.text}"))
        tab.on("pageerror", lambda error: problems.append(f"page error: {error}"))
        tab.on("dialog", lambda dialog: dialog.accept())
        tab.base = server  # type: ignore[attr-defined]
        tab.problems = problems  # type: ignore[attr-defined]
        try:
            yield tab
        finally:
            context.close()
            browser.close()
    assert problems == [], "browser reported errors:\n" + "\n".join(problems)


def documents() -> list[dict]:
    combined = highland_pdf(
        HighlandDoc(invoice="71001", items=[("2", "WHS-B30", "Base cabinet 30", "2")], po="PO-E2E-1"),
        HighlandDoc(invoice="71002", items=[("1", "WHS-W3030", "Wall cabinet 30x30", "1")], po="PO-E2E-2"),
    )
    single = highland_pdf(HighlandDoc(invoice="71003", items=[("2", "WHS-B18", "Base cabinet 18", "2")], po="PO-E2E-3"))
    return [
        {"name": "combined-invoices.pdf", "mimeType": "application/pdf", "buffer": combined},
        {"name": "invoice-71003.pdf", "mimeType": "application/pdf", "buffer": single},
    ]


def open_orders(page) -> None:
    page.goto(f"{page.base}/#/orders")
    expect(page.get_by_role("heading", name="Orders", level=1)).to_be_visible()


def quote_selected(page, numbers: tuple[str, ...]) -> None:
    open_orders(page)
    for number in numbers:
        page.get_by_role("checkbox", name=f"Select {number}").check()
    page.get_by_role("link", name="Quote together").click()
    build = page.get_by_role("button", name="Build pallet plan")
    expect(build).to_be_enabled(timeout=PACK_TIMEOUT_MS)
    build.click()
    expect(page.get_by_role("heading", name="Plan review")).to_be_visible(timeout=PACK_TIMEOUT_MS)
    record = page.get_by_role("region", name="Quote record")
    expect(record).to_contain_text(re.compile(r"Saved as quote Q-\d+"), timeout=PACK_TIMEOUT_MS)
    expect(record).to_contain_text("For " + ", ".join(numbers))


def dollars(text: str) -> Decimal:
    match = re.search(r"\$([\d,]+\.\d{2})", text)
    assert match, f"no dollar amount in {text!r}"
    return Decimal(match[1].replace(",", ""))


def test_import_quote_ship_record_and_allocate_cost(page):
    open_orders(page)
    page.get_by_role("button", name="Import documents").first.click()
    drawer = page.get_by_role("dialog", name="Import documents")
    expect(drawer).to_be_visible()
    drawer.locator("input[type=file]").set_input_files(documents())
    results = drawer.get_by_role("region", name="Import results")
    expect(results).to_contain_text("3 new orders", timeout=60_000)
    for number in ORDERS:
        expect(drawer.get_by_text(number, exact=True)).to_be_visible()
    drawer.get_by_role("button", name="Close panel").click()
    expect(drawer).to_be_hidden()

    # Two orders quoted together, then the third on its own.
    quote_selected(page, ORDERS[:2])
    quote_selected(page, ORDERS[2:])

    # All three travel as one shipment.
    open_orders(page)
    for number in ORDERS:
        page.get_by_role("checkbox", name=f"Select {number}").check()
    page.get_by_role("button", name="Create shipment").click()
    expect(page).to_have_url(re.compile(r"#/shipments/[^/?]+"))
    header = page.get_by_role("heading", level=1)
    expect(header).to_contain_text(re.compile(r"\S"))

    page.get_by_role("tab", name="Money").click()
    costs = page.get_by_role("region", name="Costs")
    costs.get_by_label("Amount ($)").fill("850.01")
    costs.get_by_label("Paid to").fill("E2E Freight Lines")
    costs.get_by_role("button", name="Record cost").click()
    expect(costs.locator(".total-badge")).to_have_text("$850.01")

    page.get_by_role("radio", name=re.compile(r"^Equal")).check()
    page.get_by_role("button", name="Allocate cost").click()
    allocation = page.locator(".allocation-result")
    expect(allocation.locator("tr.total-row")).to_contain_text("$850.01")
    lines = allocation.locator("tbody tr:not(.total-row)")
    expect(lines).to_have_count(3)
    amounts = [dollars(text) for text in lines.all_inner_texts()]
    assert sum(amounts) == Decimal("850.01")
    assert sorted(amounts) == [Decimal("283.33"), Decimal("283.34"), Decimal("283.34")]
    assert {line.split("\t")[0].split("\n")[0].strip() for line in lines.all_inner_texts()} == set(ORDERS)

    # Straight to Closed from the status track (skipping Booked/In transit/Delivered is allowed).
    page.get_by_role("list", name="Shipment status").get_by_role("button", name=re.compile(r"Closed")).click()
    page.get_by_role("dialog").get_by_role("button", name="Close shipment").click()
    expect(page.locator(".badge", has_text="Closed").first).to_be_visible()
    expect(page.get_by_role("button", name="Close shipment")).to_have_count(0)
