import json
import threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.database import Repository
from app.models import ItemMaster, PackingRequest, PackingSettings
from app.packing_worker import run
from app.services.imports import parse_packing_slip_with_report
from app.services.packing_jobs import PackingJobs, capture


def snapshot(tmp_path, sku="BOX", length=10, width=10, settings=None):
    repo = Repository(tmp_path / "accuracy.sqlite3")
    repo.initialize()
    repo.upsert_items([ItemMaster(sku=sku, length=length, width=width, height=5, weight=20)])
    request = PackingRequest(lines=[{"original_sku": sku, "quantity": 1}], settings=settings or PackingSettings())
    return {**capture(request, repo), "mode": "quick"}, request, repo


def collect(value):
    updates = []
    run(value, lambda row: updates.append(deepcopy(row)))
    return updates


def test_assembly_charge_is_reported_and_does_not_change_packing_state():
    report = parse_packing_slip_with_report(
        "order.csv", b"SKU,Quantity,Description\nAssembly (Shaker),2,Labor\nB30,1,Cabinet\n"
    )
    assert [line.original_sku for line in report.lines] == ["B30"]
    assert report.lines[0].packing_state.value == "flatpack"
    assert report.ignored[0]["original_sku"] == "Assembly (Shaker)"


def test_ambiguous_match_blocks_before_search(tmp_path, monkeypatch):
    value, _, _ = snapshot(tmp_path, "PC309024FFD")
    value["request"]["lines"][0]["original_sku"] = "MSL-PC309024F..."
    monkeypatch.setattr("app.packing_worker.build_shipment", lambda *a: pytest.fail("Search must not run"))
    result = collect(value)[-1]
    assert result["outcome_category"] == "input_blocked"
    assert result["candidate"] is None
    assert result["blockers"][0]["reason_code"] == "FUZZY_MATCH_REVIEW"
    assert result["carton_accounting"]["unresolved_quantity"] == 1


def test_w4242_fits_with_the_warehouse_overhang_allowance(tmp_path):
    value, _, _ = snapshot(tmp_path, "W4242", 43.5, 42 + 8 / 9)
    result = collect(value)[-1]
    assert result["outcome_category"] == "valid"
    pallet = result["candidate"]["pallets"][0]
    assert pallet["load_width"] <= 40 + 2 * 2.0 + 1e-6


def test_w4242_restriction_is_explained_without_search(tmp_path, monkeypatch):
    value, _, _ = snapshot(tmp_path, "W4242", 43.5, 42 + 8 / 9, PackingSettings(general_overhang_per_side=0))
    original = deepcopy(value)
    monkeypatch.setattr("app.packing_worker.build_shipment", lambda *a: pytest.fail("Search must not run"))
    result = collect(value)[-1]
    assert result["outcome_category"] == "physically_blocked"
    assert result["termination_reason"] == "preflight"
    assert result["blockers"][0]["reason_code"] == "OVERSIZE_FOOTPRINT"
    assert result["blockers"][0]["unit_ids"]
    assert value == original


@pytest.mark.parametrize("cancel", [False, True])
def test_parent_preserves_progress_after_forced_worker_stop(monkeypatch, cancel):
    manager = PackingJobs()
    event = threading.Event()
    manager.jobs["test"] = {
        "state": "queued",
        "cancel": event,
        "created": 0,
        "candidate": None,
        "accepted": None,
        "input_fingerprint": "test",
    }
    clock = iter([0, 100, 100])
    monkeypatch.setattr(
        "app.services.packing_jobs.time", SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None)
    )

    class Worker:
        returncode = None

        def __init__(self, args, **kwargs):
            Path(args[-1]).write_text(
                json.dumps(
                    {
                        "search_progress": {"current_carton": {"unit_id": "box-1"}},
                        "matching_decisions": [{"line_id": "line-1"}],
                        "outcome_category": "searching",
                    }
                ),
                encoding="utf-8",
            )

        def poll(self):
            if cancel:
                event.set()
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout):
            return self.returncode

    monkeypatch.setattr("app.services.packing_jobs.subprocess.Popen", Worker)
    try:
        manager._run("test", {"mode": "quick"})
        result = manager.get("test")
        assert result["candidate"] is None
        assert result["search_progress"]["current_carton"]["unit_id"] == "box-1"
        assert result["matching_decisions"]
        assert result["state"] == ("cancelled" if cancel else "incomplete")
        assert result["termination_reason"] == ("cancelled" if cancel else "deadline")
        if not cancel:
            assert result["outcome_category"] == "timed_out"
    finally:
        manager.shutdown()
