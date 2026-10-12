import io
import sqlite3
import time

import pytest
from app.database import Repository
from app.models import ItemMaster, PackingRequest, PackingSettings
from app.packing import StackPlannerPackingEngine
from app.services.broker_handoff import BrokerHandoffManager, quote_rows
from app.services.packing_jobs import PackingJobs, capture, fingerprint
from app.services.reports import packing_result_xlsx
from openpyxl import load_workbook
from test_broker_handoff import ready_result
from test_workflows import manual


def test_batch_persistence_failure_preserves_previous_active_handoff():
    manager = BrokerHandoffManager()
    value = manual()
    old = manager.prepare("tql", value)

    def fail(_sessions):
        raise sqlite3.OperationalError("disk full")

    with pytest.raises(sqlite3.OperationalError):
        manager.prepare_many(
            ("echoship", "tql"),
            value,
            fail,
            bridge_origin="http://127.0.0.1:8000",
            replace_active=True,
        )
    assert list(manager._sessions) == [old["request_id"]]
    assert not manager._get(old["request_id"]).cancelled


def test_snapshot_batch_transaction_rolls_back_every_row(tmp_path):
    repo = Repository(tmp_path / "batch.sqlite3")
    repo.initialize()
    manager = BrokerHandoffManager()
    launch = manager.prepare("tql", manual())
    session = manager._get(launch["request_id"])
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_quote_snapshots([session, session])
    assert repo.quote_history("manual", session.shipment_id) == []


def test_actual_envelope_quote_rows_match_packet_and_export(combined_factory):
    result = ready_result(combined_factory)
    pallet = result.pallets[0]
    pallet.load_length, pallet.load_width, pallet.freight_height, pallet.total_weight = 96.001, 40.01, 30.001, 100.5
    pallet.items[0].weight += pallet.total_weight - pallet.tare_weight - pallet.item_weight
    pallet.item_weight = sum(item.weight for item in pallet.items)
    result.summary.packed_item_weight = pallet.item_weight
    result.summary.total_item_weight = pallet.item_weight
    result.summary.total_freight_weight = pallet.total_weight
    row = quote_rows(result)[0]
    assert (row["length"], row["width"], row["height"], row["weight"]) == (97, 41, 31, 101)
    preview = BrokerHandoffManager().preview(result)
    assert preview["quote_rows"] == result.quote_rows
    for key, value in row.items():
        assert preview["packet"]["pallets"][0][key] == value
    workbook = load_workbook(io.BytesIO(packing_result_xlsx(result)))
    assert [c.value for c in workbook["Broker measurements"][2]][:5] == [1, 97, 41, 31, 101]


def test_weight_is_not_rounded_before_broker_conversion(combined_factory):
    result = StackPlannerPackingEngine().pack(
        "Half pound boundary", [combined_factory(weight=20.4999)], PackingSettings()
    )
    pallet = result.pallets[0]
    assert pallet.total_weight == pallet.tare_weight + 20.4999
    assert result.quote_rows[0]["weight"] == int(pallet.total_weight)


def test_job_snapshot_metadata_changes_and_stale_master(tmp_path):
    repo = Repository(tmp_path / "jobs.sqlite3")
    repo.initialize()
    item = ItemMaster(sku="BOX", length=10, width=10, height=10, weight=20)
    repo.upsert_items([item])
    request = PackingRequest(shipment_name="Job test", lines=[{"original_sku": "BOX", "quantity": 1}])
    first = capture(request, repo)
    renamed = request.model_copy(update={"shipment_name": "Different reference"})
    assert fingerprint(first) == fingerprint(capture(renamed, repo))
    item.flatpack_height = 12
    repo.upsert_items([item])
    assert fingerprint(first) != fingerprint(capture(request, repo))


def test_isolated_job_accept_and_stale_rejection(tmp_path):
    repo = Repository(tmp_path / "worker.sqlite3")
    repo.initialize()
    repo.upsert_items([ItemMaster(sku="BOX", length=10, width=10, height=10, weight=20)])
    request = PackingRequest(shipment_name="Job test", lines=[{"original_sku": "BOX", "quantity": 1}])
    manager = PackingJobs()
    try:
        job = manager.start(request, repo)
        deadline = time.monotonic() + 20
        while job["state"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.1)
            job = manager.get(job["id"])
        assert job["state"] == "complete", (job.get("error"), job.get("message"), job.get("outcome_category"))
        assert job["timings"][0]["final_validation_seconds"] >= 0
        assert repo.list_shipments() == []
        changed = request.model_copy(deep=True)
        changed.lines[0].quantity = 2
        with pytest.raises(ValueError, match="stale"):
            manager.accept(job["id"], changed, repo)
        saved = manager.accept(job["id"], request, repo)
        assert saved.summary.packed_items == 1
        assert manager.accept(job["id"], request, repo).id == saved.id
        refinement = manager.start(request, repo, parent_id=job["id"])
        manager.cancel(refinement["id"])
        assert refinement["candidate"] is not None
    finally:
        manager.shutdown()
