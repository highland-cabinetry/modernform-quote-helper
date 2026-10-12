from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from ..models import ItemMaster, PackingResult
from ..packing.ranking import result_rank
from ..release import RELEASE


def capture(request, repository, connection=None):
    if connection is None:
        with repository.connect() as connection:
            connection.execute("BEGIN")
            return capture(request, repository, connection)

    def item(row):
        return ItemMaster(**{**json.loads(row["payload_json"]), "id": row["id"]}).model_dump(mode="json")

    from ..edition import current as current_edition

    catalog, rule_set = "default", current_edition().rule_set
    if request.company_id or hasattr(repository, "default_company"):
        from .shipments import request_catalog

        catalog, rule_set = request_catalog(request, repository)
    if catalog == "default":
        master_sql = "SELECT id,payload_json FROM item_master WHERE active=1 ORDER BY id"
        alias_sql = ("SELECT a.normalized_alias,i.id,i.payload_json FROM sku_aliases a JOIN item_master i "
                     "ON a.item_master_id=i.id WHERE i.active=1 ORDER BY a.normalized_alias")
        params: tuple = ()
    else:
        master_sql = "SELECT id,payload_json FROM catalog_items WHERE active=1 AND catalog=? ORDER BY id"
        alias_sql = ("SELECT a.normalized_alias,i.id,i.payload_json FROM catalog_aliases a JOIN catalog_items i "
                     "ON a.item_id=i.id WHERE i.active=1 AND a.catalog=? ORDER BY a.normalized_alias")
        params = (catalog,)
    return {
        "request": request.model_dump(mode="json"),
        "catalog": catalog,
        "rule_set": rule_set,
        "master": [item(row) for row in connection.execute(master_sql, params)],
        "aliases": {row["normalized_alias"]: item(row) for row in connection.execute(alias_sql, params)},
        "engine": "stack-planner-" + RELEASE["production_engine_version"],
    }


def fingerprint(snapshot):
    value = {**snapshot, "request": dict(snapshot["request"])}
    value["request"]["settings"] = dict(value["request"]["settings"])
    for key in ("quote_origin", "default_commodity_description", "default_nmfc"):
        value["request"]["settings"].pop(key, None)
    for key in ("shipping_info", "shipment_name", "save_result", "order_ids", "freight_shipment_id"):
        value["request"].pop(key, None)
    for key in ("seed", "mode"):
        value.pop(key, None)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def worker_environment() -> dict[str, str]:
    """Environment for the packing worker: import the same ``app`` package as this server.

    uvicorn's ``--app-dir`` only changes ``sys.path`` inside the server process. Without
    this, a worker started from a release folder would import the *workspace* code through
    the editable install, so a selected release could plan pallets with unreleased code.
    """

    import os

    from .. import __file__ as app_init

    backend = str(Path(app_init).resolve().parents[1])
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join([backend, *([existing] if existing else [])])
    return environment


class PackingJobs:
    def __init__(self):
        self.jobs = {}
        self.cache = {}
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="packing-jobs")
        self.closed = False

    def activate(self):
        with self.lock:
            if self.closed:
                self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="packing-jobs")
                self.closed = False

    def start(self, request, repository, parent_id=None):
        snapshot = capture(request, repository)
        digest = fingerprint(snapshot)
        seed = None
        if parent_id:
            parent = self.get(parent_id)
            if parent["input_fingerprint"] != digest:
                raise ValueError("Inputs changed or the previous job has no valid candidate. Build a new plan.")
            seed = parent.get("candidate")
        job_id = str(uuid4())
        mode = "refine" if parent_id else "quick"
        with self.lock:
            terminal = [key for key, value in self.jobs.items() if value["state"] not in {"queued", "running"}]
            for key in terminal[:-31]:
                del self.jobs[key]
            self.jobs[job_id] = {
                "id": job_id,
                "state": "queued",
                "input_fingerprint": digest,
                "candidate": seed,
                "comparison_estimate": None,
                "message": "Waiting for the packing worker",
                "created": time.monotonic(),
                "elapsed_seconds": 0,
                "timings": [],
                "outcome_category": "searching",
                "blockers": [],
                "search_progress": {},
                "mode": mode,
                "snapshot": snapshot,
                "cancel": threading.Event(),
                "accepted": None,
            }
            if not parent_id and digest in self.cache:
                self.jobs[job_id].update(
                    state="complete",
                    candidate=json.loads(json.dumps(self.cache[digest])),
                    message="Reused a validated result for identical packing inputs",
                    cache_hit=True,
                    outcome_category="valid",
                )
                return self.get(job_id)
        self.executor.submit(self._run, job_id, {**snapshot, "seed": seed, "mode": mode})
        return self.get(job_id)

    def get(self, job_id):
        with self.lock:
            value = self.jobs[job_id]
            result = {k: v for k, v in value.items() if k not in {"snapshot", "cancel", "created", "accepted"}}
            if value["state"] in {"queued", "running"}:
                result["elapsed_seconds"] = round(time.monotonic() - value["created"], 2)
            return json.loads(json.dumps(result))

    def cancel(self, job_id):
        with self.lock:
            self.jobs[job_id]["cancel"].set()
        return self.get(job_id)

    def accept(self, job_id, request, repository):
        with self.lock:
            job = self.jobs[job_id]
            with repository.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if fingerprint(capture(request, repository, connection)) != job["input_fingerprint"]:
                    raise ValueError(
                        "This result is stale. Items, settings or handling rules changed; rebuild the plan."
                    )
                if not job.get("candidate"):
                    raise ValueError("No complete validated result is available to apply.")
                if job["accepted"]:
                    return job["accepted"]
                result = PackingResult.model_validate(job["candidate"])
                if not result.freight_ready:
                    raise ValueError("Candidate does not reconcile every shipping unit and weight.")
                result.id = str(uuid4())
                result.shipment_name = request.shipment_name
                result.shipping_info = request.shipping_info
                connection.execute(
                    "INSERT INTO shipments(id,shipment_name,request_json,result_json,created_at) VALUES (?,?,?,?,?)",
                    (
                        result.id,
                        result.shipment_name,
                        request.model_dump_json(),
                        result.model_dump_json(),
                        result.created_at.isoformat(),
                    ),
                )
            job["accepted"] = result
        if hasattr(repository, "save_quote"):
            from .freight import record_packed_quote

            record_packed_quote(repository, request, result)
        return result

    def _run(self, job_id, snapshot):
        with self.lock:
            job = self.jobs[job_id]
            if job["cancel"].is_set():
                job["state"] = "cancelled"
                job["termination_reason"] = "cancelled"
                job["message"] = "Search cancelled before it started."
                return
            job["state"] = "running"
        budget = 60 if snapshot["mode"] == "refine" else 30
        started = time.monotonic()
        process = None
        timed_out = False
        try:
            with tempfile.TemporaryDirectory(prefix="quote-helper-job-") as directory:
                source, output = Path(directory) / "input.json", Path(directory) / "output.json"
                source.write_text(
                    json.dumps(
                        {**snapshot, "deadline_monotonic": started + budget - 0.15, "started_monotonic": started}
                    ),
                    encoding="utf-8",
                )
                with (Path(directory) / "worker.log").open("w", encoding="utf-8") as log:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "app.packing_worker", str(source), str(output)],
                        stdout=log,
                        stderr=log,
                        env=worker_environment(),
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    last = None
                    while True:
                        if output.exists():
                            text = output.read_text(encoding="utf-8")
                            if text != last:
                                last = text
                                update = json.loads(text)
                                with self.lock:
                                    job.update(update)
                        if process.poll() is not None:
                            break
                        if job["cancel"].is_set() or time.monotonic() - started >= budget:
                            timed_out = not job["cancel"].is_set()
                            process.terminate()
                            process.wait(timeout=5)
                            break
                        time.sleep(0.05)
                    if output.exists():
                        with self.lock:
                            job.update(json.loads(output.read_text(encoding="utf-8")))
                    with self.lock:
                        job["state"] = (
                            "cancelled"
                            if job["cancel"].is_set()
                            else "complete"
                            if job.get("candidate")
                            else "incomplete"
                        )
                        if job.get("error"):
                            job["state"] = "error"
                        if job["cancel"].is_set():
                            job.update(
                                termination_reason="cancelled",
                                message="Search cancelled; the reviewed plan is unchanged.",
                            )
                        elif timed_out:
                            job.update(
                                outcome_category="valid" if job.get("candidate") else "timed_out",
                                termination_reason="deadline",
                                message="Time limit reached; retained the validated plan."
                                if job.get("candidate")
                                else (
                                    "Time limit reached without a complete plan. "
                                    "Review diagnostics or refine the search."
                                ),
                            )
                        elif process.returncode and not job.get("error"):
                            job.update(
                                state="error",
                                outcome_category="software_error",
                                message="Packing worker exited unexpectedly; diagnostics retained.",
                            )

        except Exception as exc:
            with self.lock:
                job.update(state="error", outcome_category="software_error", message=str(exc))
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            with self.lock:
                job["elapsed_seconds"] = round(time.monotonic() - job["created"], 2)
                if job.get("candidate"):
                    key = job["input_fingerprint"]
                    previous = self.cache.get(key)
                    if not previous or result_rank(PackingResult.model_validate(job["candidate"])) <= result_rank(
                        PackingResult.model_validate(previous)
                    ):
                        self.cache[key] = json.loads(json.dumps(job["candidate"]))
                    while len(self.cache) > 8:
                        del self.cache[next(iter(self.cache))]

    def shutdown(self):
        with self.lock:
            for job in self.jobs.values():
                job["cancel"].set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.closed = True


packing_jobs = PackingJobs()
