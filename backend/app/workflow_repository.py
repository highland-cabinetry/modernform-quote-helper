from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, datetime

from .workflow_models import ManualQuoteInput, ManualQuoteShipment, SupportProfile


class WorkflowRepository:
    """Additive storage, separate from production packing requests and settings."""

    def initialize_workflows(self) -> None:
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_schema'"
            ).fetchone()
        if exists:
            return
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = self.backup_directory / f"quote-helper-v2-before-workflows-{stamp}.sqlite3"
        with sqlite3.connect(self.database_path) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)
        with sqlite3.connect(backup) as check:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Workflow migration backup failed integrity check; no migration applied")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for statement in (
                "CREATE TABLE IF NOT EXISTS manual_quotes (id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS manual_quote_revisions (id TEXT, revision INTEGER, "
                "payload_json TEXT NOT NULL, PRIMARY KEY(id, revision))",
                "CREATE TABLE IF NOT EXISTS quote_snapshots (request_id TEXT PRIMARY KEY, source_kind TEXT, "
                "source_id TEXT, revision TEXT, packet_json TEXT NOT NULL, rates_json TEXT NOT NULL DEFAULT '[]')",
                "CREATE TABLE IF NOT EXISTS support_profiles (id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS packing_experiments (id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS workflow_schema (version INTEGER NOT NULL, backup_path TEXT NOT NULL)",
            ):
                connection.execute(statement)
            connection.execute("INSERT INTO workflow_schema VALUES (1, ?)", (str(backup),))

    def save_manual_quote(self, value: ManualQuoteInput, quote_id: str | None = None) -> ManualQuoteShipment:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload_json FROM manual_quotes WHERE id=?", (quote_id,)).fetchone()
            if quote_id and not row:
                raise KeyError("Manual quote not found")
            previous = ManualQuoteShipment.model_validate_json(row[0]) if row else None
            quote = ManualQuoteShipment(**value.model_dump())
            if previous:
                quote.id = previous.id
                quote.created_at = previous.created_at
                quote.revision = previous.revision + 1
            payload = quote.model_dump_json()
            connection.execute("INSERT OR REPLACE INTO manual_quotes VALUES (?,?)", (quote.id, payload))
            connection.execute("INSERT INTO manual_quote_revisions VALUES (?,?,?)", (quote.id, quote.revision, payload))
        return quote

    def get_manual_quote(self, quote_id: str) -> ManualQuoteShipment | None:
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM manual_quotes WHERE id=?", (quote_id,)).fetchone()
        return ManualQuoteShipment.model_validate_json(row[0]) if row else None

    def list_manual_quotes(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM manual_quotes").fetchall()
        quotes = [ManualQuoteShipment.model_validate_json(row[0]) for row in rows]
        return [
            dict(
                id=q.id,
                shipment_name=q.shipment_name,
                source_kind="manual",
                revision=q.revision,
                created_at=q.updated_at.isoformat(),
                total_pallets=sum(p.quantity for p in q.pallets),
                # History shows the values the broker receives. Each physical
                # pallet is rounded independently, just like the handoff packet.
                total_weight=sum(math.floor(p.weight + 0.5) * p.quantity for p in q.pallets),
                total_items=0,
                needs_review=False,
            )
            for q in sorted(quotes, key=lambda q: q.updated_at, reverse=True)
        ]

    def save_quote_snapshot(self, session) -> None:
        self.save_quote_snapshots([session])

    def save_quote_snapshots(self, sessions) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "INSERT INTO quote_snapshots(request_id,source_kind,source_id,revision,packet_json) VALUES (?,?,?,?,?)",
                [
                    (
                        session.request_id,
                        session.source_kind,
                        session.shipment_id,
                        session.source_revision,
                        json.dumps(session.packet),
                    )
                    for session in sessions
                ],
            )

    def quote_history(self, source_kind: str, source_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT request_id, revision, packet_json FROM quote_snapshots WHERE source_kind=? AND source_id=?",
                (source_kind, source_id),
            ).fetchall()
        return [
            dict(
                request_id=r["request_id"],
                revision=r["revision"],
                packet=json.loads(r["packet_json"]),
            )
            for r in rows
        ]

    def list_support_profiles(self) -> list[SupportProfile]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM support_profiles ORDER BY id").fetchall()
        return [SupportProfile.model_validate_json(row[0]) for row in rows]

    def save_support_profile(self, profile: SupportProfile) -> SupportProfile:
        profile.updated_at = datetime.now(UTC)
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO support_profiles VALUES (?,?)", (profile.id, profile.model_dump_json())
            )
        return profile

    def save_experiment(self, value: dict) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO packing_experiments VALUES (?,?)", (value["id"], json.dumps(value))
            )

    def get_experiment(self, experiment_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM packing_experiments WHERE id=?", (experiment_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None
