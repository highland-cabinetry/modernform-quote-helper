"""SQLite storage for companies, catalogs, orders, quotes, shipments and expenses.

Migration rules (the live database is shared with the released 2.10.0 build):

* additive only — no existing table or column is altered or dropped;
* a verified SQLite backup is written before the first migration;
* no foreign keys point at the legacy ``shipments`` or ``item_master`` tables, so the
  released build's "Replace all data" keeps working;
* nothing new is written into legacy payloads (2.10.0 forbids unknown item fields).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .freight_models import (
    ACTIVE_SHIPMENT_STATUSES,
    LOCKED_SHIPMENT_STATUSES,
    Allocation,
    AuditEntry,
    Company,
    CompanyConfig,
    EmailRecord,
    FreightShipment,
    Order,
    Quote,
    ShipmentExpense,
    ShipmentRevenue,
    SourceFile,
    UnassignedExpense,
)
from .models import AddressInfo, ItemMaster, PackingSettings, SkuAlias

FREIGHT_SCHEMA_VERSION = 2  # 1: 3.0 freight domain; 2: 3.2 workflow tables (additive)
DEFAULT_COMPANY_CODE = "HCI"  # upstream default; new databases use the edition's code (edition.py)
DEFAULT_CATALOG = "default"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS companies (
        id TEXT PRIMARY KEY, code TEXT NOT NULL COLLATE NOCASE UNIQUE, name TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1, is_default INTEGER NOT NULL DEFAULT 0,
        payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS company_settings (
        company_id TEXT PRIMARY KEY REFERENCES companies(id), value_json TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS catalog_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT, catalog TEXT NOT NULL, sku TEXT NOT NULL COLLATE NOCASE,
        payload_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL,
        UNIQUE(catalog, sku))""",
    """CREATE TABLE IF NOT EXISTS catalog_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, catalog TEXT NOT NULL, alias_sku TEXT NOT NULL,
        normalized_alias TEXT NOT NULL COLLATE NOCASE,
        item_id INTEGER NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(catalog, normalized_alias))""",
    """CREATE TABLE IF NOT EXISTS import_batches (
        id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id), created_at TEXT NOT NULL,
        summary_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS source_files (
        id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id), batch_id TEXT NOT NULL,
        filename TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_source_files_hash ON source_files(company_id, sha256)",
    """CREATE TABLE IF NOT EXISTS orders (
        id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id),
        order_number TEXT NOT NULL COLLATE NOCASE, status TEXT NOT NULL, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(company_id, order_number))""",
    "CREATE INDEX IF NOT EXISTS idx_orders_updated ON orders(updated_at DESC)",
    """CREATE TABLE IF NOT EXISTS order_references (
        order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE, company_id TEXT NOT NULL,
        reference TEXT NOT NULL COLLATE NOCASE, PRIMARY KEY(company_id, reference))""",
    """CREATE TABLE IF NOT EXISTS order_revisions (
        order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE, revision INTEGER NOT NULL,
        payload_json TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(order_id, revision))""",
    """CREATE TABLE IF NOT EXISTS quotes (
        id TEXT PRIMARY KEY, number TEXT NOT NULL UNIQUE, company_id TEXT NOT NULL REFERENCES companies(id),
        kind TEXT NOT NULL, status TEXT NOT NULL, packing_result_id TEXT, manual_quote_id TEXT,
        freight_shipment_id TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_quotes_created ON quotes(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quotes_packing ON quotes(packing_result_id)",
    "CREATE INDEX IF NOT EXISTS idx_quotes_manual ON quotes(manual_quote_id)",
    """CREATE TABLE IF NOT EXISTS quote_orders (
        quote_id TEXT NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
        order_id TEXT NOT NULL REFERENCES orders(id), PRIMARY KEY(quote_id, order_id))""",
    "CREATE INDEX IF NOT EXISTS idx_quote_orders_order ON quote_orders(order_id)",
    """CREATE TABLE IF NOT EXISTS freight_shipments (
        id TEXT PRIMARY KEY, number TEXT NOT NULL UNIQUE, company_id TEXT NOT NULL REFERENCES companies(id),
        status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS shipment_orders (
        shipment_id TEXT NOT NULL REFERENCES freight_shipments(id), order_id TEXT NOT NULL REFERENCES orders(id),
        added_at TEXT NOT NULL, PRIMARY KEY(shipment_id, order_id))""",
    "CREATE INDEX IF NOT EXISTS idx_shipment_orders_order ON shipment_orders(order_id)",
    """CREATE TABLE IF NOT EXISTS shipment_expenses (
        id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL REFERENCES freight_shipments(id),
        amount_cents INTEGER NOT NULL, voided INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_expenses_shipment ON shipment_expenses(shipment_id)",
    """CREATE TABLE IF NOT EXISTS expense_allocations (
        shipment_id TEXT PRIMARY KEY REFERENCES freight_shipments(id), payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
        action TEXT NOT NULL, summary TEXT NOT NULL, details_json TEXT NOT NULL, actor TEXT NOT NULL,
        at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id, id)",
    "CREATE TABLE IF NOT EXISTS freight_counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL)",
    # ---- schema 2 (3.2): extra revenue, bills without a shipment, prepared emails, import keys
    """CREATE TABLE IF NOT EXISTS shipment_revenue (
        id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id), shipment_id TEXT,
        amount_cents INTEGER NOT NULL, voided INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_revenue_shipment ON shipment_revenue(shipment_id)",
    """CREATE TABLE IF NOT EXISTS unassigned_expenses (
        id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id), amount_cents INTEGER NOT NULL,
        voided INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS email_log (
        id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id), kind TEXT NOT NULL,
        entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_email_entity ON email_log(entity_type, entity_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_quotes_external ON quotes(json_extract(payload_json, '$.external_ref'))",
    """CREATE INDEX IF NOT EXISTS idx_shipments_external
        ON freight_shipments(json_extract(payload_json, '$.external_ref'))""",
)


class ConflictError(ValueError):
    """A request conflicts with stored data (duplicates, locked records)."""


class NotFoundError(KeyError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


class FreightRepository:
    """Mixin for ``Repository``: needs ``connect()``, ``database_path`` and ``backup_directory``."""

    # ------------------------------------------------------------------ migration

    def initialize_freight(self) -> dict[str, Any]:
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='freight_schema'"
            ).fetchone()
            version = (
                connection.execute("SELECT MAX(version) FROM freight_schema").fetchone()[0] if exists else 0
            ) or 0
        if version >= FREIGHT_SCHEMA_VERSION:
            return {"migrated": False, "version": version}
        label = "before-freight-domain" if version == 0 else f"before-freight-schema-{FREIGHT_SCHEMA_VERSION}"
        backup = self._verified_backup(label)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA:
                connection.execute(statement)
            connection.execute(
                "CREATE TABLE IF NOT EXISTS freight_schema (version INTEGER NOT NULL, backup_path TEXT NOT NULL, "
                "applied_at TEXT NOT NULL)"
            )
            self._ensure_default_company(connection)
            connection.execute(
                "INSERT INTO freight_schema VALUES (?, ?, ?)", (FREIGHT_SCHEMA_VERSION, str(backup), _now())
            )
        return {"migrated": True, "version": FREIGHT_SCHEMA_VERSION, "backup": str(backup)}

    def _verified_backup(self, label: str) -> Path:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = self.backup_directory / f"quote-helper-v2-{label}-{stamp}.sqlite3"
        source = sqlite3.connect(self.database_path, timeout=30)
        destination = sqlite3.connect(backup)
        try:
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Migration backup failed its integrity check; nothing was changed")
        finally:
            destination.close()
            source.close()
        return backup

    def _ensure_default_company(self, connection: sqlite3.Connection) -> None:
        if connection.execute("SELECT 1 FROM companies WHERE is_default=1").fetchone():
            return
        settings_row = connection.execute("SELECT value_json FROM settings WHERE key='packing'").fetchone()
        settings = PackingSettings.model_validate_json(settings_row[0]) if settings_row else PackingSettings()
        from .edition import current as current_edition

        edition = current_edition()
        origin = settings.quote_origin
        if not origin.postal_code and edition.origin:
            origin = AddressInfo(**edition.origin)
        company = Company(
            code=edition.company_code,
            name=edition.company_name,
            config=CompanyConfig(
                catalog=DEFAULT_CATALOG,
                rule_set=edition.rule_set,
                adapters=list(edition.adapters),
                document_markers=list(edition.document_markers),
                bare_number_prefix=edition.bare_number_prefix,
                origin=origin,
            ),
        )
        connection.execute(
            "INSERT INTO companies VALUES (?,?,?,?,?,?,?,?)",
            (company.id, company.code, company.name, 1, 1, company.model_dump_json(), _now(), _now()),
        )

    # ------------------------------------------------------------------ helpers

    def audit(self, connection: sqlite3.Connection, entity_type: str, entity_id: str, action: str,
              summary: str = "", details: dict | None = None) -> None:
        connection.execute(
            "INSERT INTO audit_log(entity_type, entity_id, action, summary, details_json, actor, at) "
            "VALUES (?,?,?,?,?,?,?)",
            (entity_type, entity_id, action, summary, json.dumps(details or {}, default=str), "freight-manager",
             _now()),
        )

    def audit_trail(self, entity_type: str, entity_id: str) -> list[AuditEntry]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_log WHERE entity_type=? AND entity_id=? ORDER BY id", (entity_type, entity_id)
            ).fetchall()
        return [
            AuditEntry(id=row["id"], entity_type=row["entity_type"], entity_id=row["entity_id"], action=row["action"],
                       summary=row["summary"], details=json.loads(row["details_json"]), actor=row["actor"],
                       at=row["at"])
            for row in rows
        ]

    def recent_activity(self, limit: int = 20) -> list[AuditEntry]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [
            AuditEntry(id=row["id"], entity_type=row["entity_type"], entity_id=row["entity_id"], action=row["action"],
                       summary=row["summary"], details=json.loads(row["details_json"]), actor=row["actor"],
                       at=row["at"])
            for row in rows
        ]

    @staticmethod
    def _next_number(connection: sqlite3.Connection, name: str, prefix: str) -> str:
        row = connection.execute("SELECT value FROM freight_counters WHERE name=?", (name,)).fetchone()
        value = (row[0] if row else 0) + 1
        connection.execute(
            "INSERT INTO freight_counters VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (name, value),
        )
        return f"{prefix}-{value:06d}"

    # ------------------------------------------------------------------ companies

    def list_companies(self, include_inactive: bool = True) -> list[Company]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM companies " + ("" if include_inactive else "WHERE active=1 ")
                + "ORDER BY is_default DESC, name COLLATE NOCASE"
            ).fetchall()
        return [Company.model_validate_json(row[0]) for row in rows]

    def default_company(self) -> Company:
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM companies WHERE is_default=1").fetchone()
        if not row:
            raise RuntimeError("The default company is missing; restart Quote Helper to run its migration.")
        return Company.model_validate_json(row[0])

    def get_company(self, company_id: str | None) -> Company:
        if not company_id:
            return self.default_company()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM companies WHERE id=? OR code=? COLLATE NOCASE", (company_id, company_id)
            ).fetchone()
        if not row:
            raise NotFoundError(f"Company {company_id} was not found")
        return Company.model_validate_json(row[0])

    def is_default_company(self, company: Company) -> bool:
        return company.id == self.default_company().id

    def save_company(self, company: Company) -> Company:
        company.updated_at = datetime.now(UTC)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            clash = connection.execute(
                "SELECT id FROM companies WHERE code=? COLLATE NOCASE AND id<>?", (company.code, company.id)
            ).fetchone()
            if clash:
                raise ConflictError(f"Company code {company.code} is already used")
            existing = connection.execute("SELECT is_default FROM companies WHERE id=?", (company.id,)).fetchone()
            if existing and existing[0] and not company.active:
                raise ConflictError("The default company cannot be deactivated")
            connection.execute(
                """INSERT INTO companies(id, code, name, active, is_default, payload_json, created_at, updated_at)
                   VALUES (?,?,?,?,0,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET code=excluded.code, name=excluded.name, active=excluded.active,
                   payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (company.id, company.code, company.name, int(company.active), company.model_dump_json(),
                 company.created_at.isoformat(), company.updated_at.isoformat()),
            )
            self.audit(connection, "company", company.id, "saved", f"{company.code} · {company.name}",
                       {"config": company.config.model_dump(mode="json")})
        return company

    def company_settings(self, company: Company) -> PackingSettings:
        if self.is_default_company(company):
            return self.get_settings()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM company_settings WHERE company_id=?", (company.id,)
            ).fetchone()
        return PackingSettings.model_validate_json(row[0]) if row else PackingSettings()

    def save_company_settings(self, company: Company, settings: PackingSettings) -> PackingSettings:
        if self.is_default_company(company):
            self.save_settings(settings)
            return settings
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO company_settings VALUES (?,?,?) ON CONFLICT(company_id) DO UPDATE SET "
                "value_json=excluded.value_json, updated_at=excluded.updated_at",
                (company.id, settings.model_dump_json(), _now()),
            )
        return settings

    # ------------------------------------------------------------------ catalogs

    def catalog_names(self) -> list[str]:
        with self.connect() as connection:
            names = [row[0] for row in connection.execute("SELECT DISTINCT catalog FROM catalog_items")]
        configured = [company.config.catalog for company in self.list_companies()]
        return list(dict.fromkeys([DEFAULT_CATALOG, *configured, *names]))

    @staticmethod
    def _catalog_row_item(row) -> ItemMaster:
        # Deactivation only flips the ``active`` column (the 2.x item-master behaviour), so the
        # column, not the stored payload, says whether an item is active.
        return ItemMaster(**{**json.loads(row["payload_json"]), "id": int(row["id"]), "active": bool(row["active"])})

    @staticmethod
    def _catalog_scope(catalog: str) -> tuple[str, str, list[Any]]:
        if catalog == DEFAULT_CATALOG:
            return "item_master", "1=1", []
        return "catalog_items", "catalog=?", [catalog]

    def catalog_items(self, catalog: str = DEFAULT_CATALOG, *, active_only: bool = True,
                      search: str | None = None) -> list[ItemMaster]:
        table, where, params = self._catalog_scope(catalog)
        sql = f"SELECT id, active, payload_json FROM {table} WHERE {where}"
        if active_only:
            sql += " AND active=1"
        if search:
            sql += " AND (sku LIKE ? OR payload_json LIKE ?)"
            params += [f"%{search}%", f"%{search}%"]
        with self.connect() as connection:
            rows = connection.execute(sql + " ORDER BY sku", params).fetchall()
        return [self._catalog_row_item(row) for row in rows]

    def catalog_item(self, catalog: str, item_id: int) -> ItemMaster | None:
        table, where, params = self._catalog_scope(catalog)
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT id, active, payload_json FROM {table} WHERE {where} AND id=?", [*params, item_id]
            ).fetchone()
        return self._catalog_row_item(row) if row else None

    def catalog_upsert(self, catalog: str, items: list[ItemMaster], *, reason: str = "edited") -> tuple[int, int]:
        """Insert or update by SKU in one transaction, with one audit entry per changed item."""

        inserted = updated = 0
        now = _now()
        table, key = ("item_master", None) if catalog == DEFAULT_CATALOG else ("catalog_items", catalog)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for item in items:
                payload = item.model_dump(exclude={"id"}, mode="json")
                if key is None:
                    existing = connection.execute(
                        "SELECT id, active, payload_json FROM item_master WHERE sku=? COLLATE NOCASE", (item.sku,)
                    ).fetchone()
                else:
                    existing = connection.execute(
                        "SELECT id, active, payload_json FROM catalog_items WHERE catalog=? AND sku=? COLLATE NOCASE",
                        (key, item.sku),
                    ).fetchone()
                if existing:
                    before = {**json.loads(existing["payload_json"]), "active": bool(existing["active"])}
                    item.id = int(existing["id"])
                    if before == payload:
                        continue
                    connection.execute(
                        f"UPDATE {table} SET payload_json=?, active=?, updated_at=? WHERE id=?",
                        (json.dumps(payload), int(item.active), now, item.id),
                    )
                    changed = {k: [before.get(k), v] for k, v in payload.items() if before.get(k) != v}
                    self.audit(connection, "catalog_item", f"{catalog}:{item.sku.upper()}", "updated",
                               f"{item.sku}: {', '.join(changed)} ({reason})", {"changes": changed})
                    updated += 1
                else:
                    if key is None:
                        cursor = connection.execute(
                            "INSERT INTO item_master(sku, payload_json, active, updated_at) VALUES (?,?,?,?)",
                            (item.sku, json.dumps(payload), int(item.active), now),
                        )
                    else:
                        cursor = connection.execute(
                            "INSERT INTO catalog_items(catalog, sku, payload_json, active, updated_at) "
                            "VALUES (?,?,?,?,?)",
                            (key, item.sku, json.dumps(payload), int(item.active), now),
                        )
                    item.id = int(cursor.lastrowid)
                    self.audit(connection, "catalog_item", f"{catalog}:{item.sku.upper()}", "created",
                               f"{item.sku} added ({reason})", {"item": payload})
                    inserted += 1
        return inserted, updated

    def catalog_deactivate(self, catalog: str, item_id: int) -> bool:
        if catalog == DEFAULT_CATALOG:
            item = self.get_item(item_id)
            ok = self.delete_item(item_id)
        else:
            item = self.catalog_item(catalog, item_id)
            with self.connect() as connection:
                connection.execute("DELETE FROM catalog_aliases WHERE catalog=? AND item_id=?", (catalog, item_id))
                ok = connection.execute(
                    "UPDATE catalog_items SET active=0, updated_at=? WHERE catalog=? AND id=?",
                    (_now(), catalog, item_id),
                ).rowcount > 0
        if ok and item:
            with self.connect() as connection:
                self.audit(connection, "catalog_item", f"{catalog}:{item.sku.upper()}", "deactivated",
                           f"{item.sku} removed from the active catalog")
        return ok

    def catalog_alias_targets(self, catalog: str = DEFAULT_CATALOG) -> dict[str, ItemMaster]:
        if catalog == DEFAULT_CATALOG:
            return self.alias_targets()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT a.normalized_alias, i.id, i.payload_json FROM catalog_aliases a JOIN catalog_items i "
                "ON i.id=a.item_id WHERE a.catalog=? AND i.active=1", (catalog,)
            ).fetchall()
        return {
            row["normalized_alias"]: ItemMaster(id=int(row["id"]), **json.loads(row["payload_json"])) for row in rows
        }

    def catalog_aliases(self, catalog: str = DEFAULT_CATALOG, search: str | None = None) -> list[SkuAlias]:
        if catalog == DEFAULT_CATALOG:
            return self.list_sku_aliases(search=search)
        sql = ("SELECT a.*, i.sku AS target_sku, i.payload_json FROM catalog_aliases a JOIN catalog_items i "
               "ON i.id=a.item_id WHERE a.catalog=?")
        params: list[Any] = [catalog]
        if search:
            sql += " AND (a.alias_sku LIKE ? OR i.sku LIKE ?)"
            params += [f"%{search}%", f"%{search}%"]
        with self.connect() as connection:
            rows = connection.execute(sql + " ORDER BY a.alias_sku COLLATE NOCASE", params).fetchall()
        return [
            SkuAlias(id=int(r["id"]), alias_sku=r["alias_sku"], normalized_alias=r["normalized_alias"],
                     target_item_id=int(r["item_id"]), target_sku=r["target_sku"],
                     target_description=str(json.loads(r["payload_json"]).get("description") or ""),
                     created_at=r["created_at"], updated_at=r["updated_at"])
            for r in rows
        ]

    def catalog_save_alias(self, catalog: str, alias_sku: str, target_item_id: int, *,
                           ignored_prefixes: list[str]) -> SkuAlias:
        if catalog == DEFAULT_CATALOG:
            alias = self.save_sku_alias(alias_sku, target_item_id, ignored_prefixes=ignored_prefixes)
        else:
            from .services.product_exceptions import is_status_placeholder
            from .services.sku_matching import normalize_sku

            if is_status_placeholder(alias_sku):
                raise ValueError("Back-order status placeholders cannot be saved as product aliases")
            normalized = normalize_sku(alias_sku, ignored_prefixes)
            if not normalized:
                raise ValueError("Alias must contain letters or numbers after normalization")
            target = self.catalog_item(catalog, target_item_id)
            if not target or not target.active:
                raise ValueError("Target item was not found or is inactive")
            for item in self.catalog_items(catalog):
                if normalized in {normalize_sku(item.sku, ignored_prefixes),
                                  normalize_sku(item.manufacturer_sku or "", ignored_prefixes)}:
                    raise ConflictError(f"{alias_sku} already resolves through the catalog")
            with self.connect() as connection:
                existing = connection.execute(
                    "SELECT item_id FROM catalog_aliases WHERE catalog=? AND normalized_alias=?", (catalog, normalized)
                ).fetchone()
                if existing and int(existing[0]) != target_item_id:
                    raise ConflictError("That alias is already assigned to another item; delete it first")
                connection.execute(
                    "INSERT INTO catalog_aliases"
                    "(catalog, alias_sku, normalized_alias, item_id, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(catalog, normalized_alias) DO UPDATE SET "
                    "alias_sku=excluded.alias_sku, updated_at=excluded.updated_at",
                    (catalog, alias_sku.strip(), normalized, target_item_id, _now(), _now()),
                )
            alias = next(a for a in self.catalog_aliases(catalog) if a.normalized_alias.upper() == normalized.upper())
        with self.connect() as connection:
            self.audit(connection, "catalog_item", f"{catalog}:{alias.target_sku.upper()}", "alias_saved",
                       f"{alias.alias_sku} → {alias.target_sku}")
        return alias

    def catalog_delete_alias(self, catalog: str, alias_id: int) -> bool:
        if catalog == DEFAULT_CATALOG:
            return self.delete_sku_alias(alias_id)
        with self.connect() as connection:
            return connection.execute(
                "DELETE FROM catalog_aliases WHERE catalog=? AND id=?", (catalog, alias_id)
            ).rowcount > 0

    # ------------------------------------------------------------------ source files and batches

    def find_source_file(self, company_id: str, sha256: str) -> SourceFile | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM source_files WHERE company_id=? AND sha256=? AND status IN "
                "('imported','partial') ORDER BY created_at LIMIT 1", (company_id, sha256)
            ).fetchone()
        return SourceFile.model_validate_json(row[0]) if row else None

    def save_source_file(self, connection: sqlite3.Connection, source: SourceFile) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO source_files VALUES (?,?,?,?,?,?,?,?)",
            (source.id, source.company_id, source.batch_id, source.filename, source.sha256, source.status,
             source.model_dump_json(), source.created_at.isoformat()),
        )

    def get_source_file(self, source_id: str) -> SourceFile | None:
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM source_files WHERE id=?", (source_id,)).fetchone()
        return SourceFile.model_validate_json(row[0]) if row else None

    def save_batch(self, connection: sqlite3.Connection, batch_id: str, company_id: str, summary: dict) -> None:
        connection.execute("INSERT OR REPLACE INTO import_batches VALUES (?,?,?,?)",
                           (batch_id, company_id, _now(), json.dumps(summary, default=str)))

    # ------------------------------------------------------------------ orders

    @staticmethod
    def _order_from_row(row) -> Order:
        return Order.model_validate_json(row["payload_json"])

    def get_order(self, order_id: str) -> Order | None:
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM orders WHERE id=?", (order_id,)).fetchone()
        return self._order_from_row(row) if row else None

    def get_orders(self, order_ids: Iterable[str]) -> list[Order]:
        ids = list(order_ids)
        if not ids:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT payload_json FROM orders WHERE id IN ({','.join('?' * len(ids))})", ids
            ).fetchall()
        found = {order.id: order for order in (self._order_from_row(row) for row in rows)}
        missing = [order_id for order_id in ids if order_id not in found]
        if missing:
            raise NotFoundError(f"Orders not found: {', '.join(missing)}")
        return [found[order_id] for order_id in ids]

    def find_order_by_reference(self, connection: sqlite3.Connection, company_id: str,
                                references: Iterable[str]) -> Order | None:
        for reference in references:
            if not reference:
                continue
            row = connection.execute(
                "SELECT o.payload_json FROM orders o WHERE o.company_id=? AND o.order_number=? COLLATE NOCASE "
                "UNION ALL SELECT o.payload_json FROM order_references r JOIN orders o ON o.id=r.order_id "
                "WHERE r.company_id=? AND r.reference=? COLLATE NOCASE LIMIT 1",
                (company_id, reference, company_id, reference),
            ).fetchone()
            if row:
                return self._order_from_row(row)
        return None

    def _write_order(self, connection: sqlite3.Connection, order: Order) -> None:
        order.updated_at = datetime.now(UTC)
        clash = connection.execute(
            "SELECT id FROM orders WHERE company_id=? AND order_number=? COLLATE NOCASE AND id<>?",
            (order.company_id, order.order_number, order.id),
        ).fetchone()
        if clash:
            raise ConflictError(f"Order {order.order_number} already exists")
        connection.execute(
            """INSERT INTO orders VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               order_number=excluded.order_number, status=excluded.status, payload_json=excluded.payload_json,
               updated_at=excluded.updated_at""",
            (order.id, order.company_id, order.order_number, order.status, order.model_dump_json(),
             order.created_at.isoformat(), order.updated_at.isoformat()),
        )
        connection.execute("DELETE FROM order_references WHERE order_id=?", (order.id,))
        for reference in dict.fromkeys([order.order_number, *order.references]):
            if not reference:
                continue
            owner = connection.execute(
                "SELECT order_id FROM order_references WHERE company_id=? AND reference=? COLLATE NOCASE",
                (order.company_id, reference),
            ).fetchone()
            if owner and owner[0] != order.id:
                continue  # another order already answers to this reference; keep the first owner
            connection.execute("INSERT OR IGNORE INTO order_references VALUES (?,?,?)",
                               (order.id, order.company_id, reference))
        connection.execute(
            "INSERT OR REPLACE INTO order_revisions VALUES (?,?,?,?,?)",
            (order.id, order.revision, order.model_dump_json(), "saved", _now()),
        )

    def insert_order(self, connection: sqlite3.Connection, order: Order, summary: str) -> Order:
        self._write_order(connection, order)
        self.audit(connection, "order", order.id, "created", summary,
                   {"order_number": order.order_number, "units": order.unit_count,
                    "source_file_id": order.source_file_id})
        return order

    def update_order(self, order: Order, *, reason: str, details: dict | None = None) -> Order:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT payload_json FROM orders WHERE id=?", (order.id,)).fetchone()
            if not current:
                raise NotFoundError("Order not found")
            previous = Order.model_validate_json(current[0])
            if previous.status == "shipped" and order.lines != previous.lines:
                raise ConflictError("This order is in a closed shipment; its lines can no longer change")
            order.revision = previous.revision + 1
            order.created_at = previous.created_at
            self._write_order(connection, order)
            self.audit(connection, "order", order.id, "corrected", reason,
                       details or {"before": previous.model_dump(mode="json", include={"lines", "order_number"}),
                                   "after": order.model_dump(mode="json", include={"lines", "order_number"})})
        return order

    def set_order_status(self, connection: sqlite3.Connection, order_id: str, status: str, reason: str) -> None:
        row = connection.execute("SELECT payload_json FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            return
        order = Order.model_validate_json(row[0])
        if order.status == status:
            return
        order.status = status  # type: ignore[assignment]
        order.updated_at = datetime.now(UTC)
        connection.execute("UPDATE orders SET status=?, payload_json=?, updated_at=? WHERE id=?",
                           (status, order.model_dump_json(), order.updated_at.isoformat(), order_id))
        self.audit(connection, "order", order_id, "status", reason, {"status": status})

    def list_orders(self, *, company_id: str | None = None, search: str | None = None,
                    status: str | None = None, limit: int = 200, unshipped_only: bool = False) -> list[dict]:
        sql = "SELECT o.payload_json FROM orders o WHERE 1=1"
        params: list[Any] = []
        if company_id:
            sql += " AND o.company_id=?"
            params.append(company_id)
        if status:
            sql += " AND o.status=?"
            params.append(status)
        if search:
            # Order numbers and references are stored compacted ("SO-77767" -> "SO77767"); match that form too.
            compact = re.sub(r"[^A-Z0-9]", "", search.upper()) or search
            sql += (" AND (o.order_number LIKE ? OR o.order_number LIKE ? OR o.payload_json LIKE ? OR EXISTS "
                    "(SELECT 1 FROM order_references r WHERE r.order_id=o.id "
                    "AND (r.reference LIKE ? OR r.reference LIKE ?)))")
            params += [f"%{search}%", f"%{compact}%", f"%{search}%", f"%{search}%", f"%{compact}%"]
        if unshipped_only:
            sql += (" AND NOT EXISTS (SELECT 1 FROM shipment_orders so JOIN freight_shipments s ON s.id=so.shipment_id "
                    f"WHERE so.order_id=o.id AND s.status IN ({','.join('?' * len(ACTIVE_SHIPMENT_STATUSES))}))")
            params += list(ACTIVE_SHIPMENT_STATUSES)
        sql += " ORDER BY o.updated_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as connection:
            orders = [self._order_from_row(row) for row in connection.execute(sql, params).fetchall()]
            links = self._order_links(connection, [order.id for order in orders])
        return [
            {
                **order.model_dump(mode="json", exclude={"lines"}),
                "line_count": len(order.lines),
                "unit_count": order.unit_count,
                **links.get(order.id, self._empty_order_links()),
            }
            for order in orders
        ]

    @staticmethod
    def _empty_order_links() -> dict:
        return {"quote_ids": [], "quote_numbers": [], "shipment": None, "quoted_count": 0,
                "latest_customer_status": None}

    def _order_links(self, connection: sqlite3.Connection, order_ids: list[str]) -> dict[str, dict]:
        if not order_ids:
            return {}
        marks = ",".join("?" * len(order_ids))
        links: dict[str, dict] = {order_id: self._empty_order_links() for order_id in order_ids}
        # The same pass counts the times quoted (WORKFLOW §1: priced, sent or answered) and keeps the
        # customer's answer on the newest of those quotes.
        for row in connection.execute(
            f"SELECT qo.order_id, q.id, q.number, json_extract(q.payload_json, '$.customer_status'), "
            f"json_extract(q.payload_json, '$.cost.customer_price_cents'), json_extract(q.payload_json, '$.sent_at') "
            f"FROM quote_orders qo JOIN quotes q ON q.id=qo.quote_id "
            f"WHERE qo.order_id IN ({marks}) AND q.status<>'void' ORDER BY q.created_at, q.number", order_ids
        ):
            link = links[row[0]]
            link["quote_ids"].append(row[1])
            link["quote_numbers"].append(row[2])
            status = row[3] or "not_sent"
            if row[4] is not None or row[5] is not None or status != "not_sent":
                link["quoted_count"] += 1
                link["latest_customer_status"] = status
        for row in connection.execute(
            f"SELECT so.order_id, s.id, s.number, s.status FROM shipment_orders so JOIN freight_shipments s "
            f"ON s.id=so.shipment_id WHERE so.order_id IN ({marks}) AND s.status<>'cancelled'", order_ids
        ):
            links[row[0]]["shipment"] = {"id": row[1], "number": row[2], "status": row[3]}
        return links

    def order_revisions(self, order_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT revision, reason, created_at FROM order_revisions WHERE order_id=? ORDER BY revision",
                (order_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ quotes

    def save_quote(self, quote: Quote, *, action: str = "created", summary: str = "") -> Quote:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._write_quote(connection, quote, action=action, summary=summary)
        return quote

    def _write_quote(self, connection: sqlite3.Connection, quote: Quote, *, action: str, summary: str,
                     details: dict | None = None) -> None:
        if len(set(quote.order_ids)) != len(quote.order_ids):
            raise ConflictError("An order can be included in a quote only once")
        if not quote.number:
            quote.number = self._next_number(connection, "quote", "Q")
        quote.updated_at = datetime.now(UTC)
        connection.execute(
            """INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               status=excluded.status, freight_shipment_id=excluded.freight_shipment_id,
               payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
            (quote.id, quote.number, quote.company_id, quote.kind, quote.status, quote.packing_result_id,
             quote.manual_quote_id, quote.freight_shipment_id, quote.model_dump_json(),
             quote.created_at.isoformat(), quote.updated_at.isoformat()),
        )
        connection.execute("DELETE FROM quote_orders WHERE quote_id=?", (quote.id,))
        connection.executemany("INSERT INTO quote_orders VALUES (?,?)", [(quote.id, oid) for oid in quote.order_ids])
        self.audit(connection, "quote", quote.id, action, summary or f"{quote.number} {action}",
                   {"order_ids": quote.order_ids, "status": quote.status, **(details or {})})

    def get_quote(self, quote_id: str) -> Quote | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM quotes WHERE id=? OR number=?", (quote_id, quote_id)
            ).fetchone()
        return Quote.model_validate_json(row[0]) if row else None

    def quote_for_source(
        self, *, packing_result_id: str | None = None, manual_quote_id: str | None = None
    ) -> Quote | None:
        column, value = (
            ("packing_result_id", packing_result_id) if packing_result_id else ("manual_quote_id", manual_quote_id)
        )
        if not value:
            return None
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT payload_json FROM quotes WHERE {column}=? AND status<>'void' ORDER BY created_at DESC LIMIT 1",
                (value,),
            ).fetchone()
        return Quote.model_validate_json(row[0]) if row else None

    def list_quotes(self, *, company_id: str | None = None, search: str | None = None, status: str | None = None,
                    kind: str | None = None, order_id: str | None = None, shipment_id: str | None = None,
                    limit: int = 500) -> list[Quote]:
        sql = "SELECT q.payload_json FROM quotes q WHERE 1=1"
        params: list[Any] = []
        for column, value in (("q.company_id", company_id), ("q.status", status), ("q.kind", kind),
                              ("q.freight_shipment_id", shipment_id)):
            if value:
                sql += f" AND {column}=?"
                params.append(value)
        if order_id:
            sql += " AND EXISTS (SELECT 1 FROM quote_orders qo WHERE qo.quote_id=q.id AND qo.order_id=?)"
            params.append(order_id)
        if search:
            sql += (" AND (q.number LIKE ? OR q.payload_json LIKE ? OR EXISTS (SELECT 1 FROM quote_orders qo JOIN "
                    "orders o ON o.id=qo.order_id WHERE qo.quote_id=q.id "
                    "AND (o.order_number LIKE ? OR o.payload_json LIKE ?)))")
            params += [f"%{search}%"] * 4
        sql += " ORDER BY q.created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [Quote.model_validate_json(row[0]) for row in rows]

    def order_numbers(self, order_ids: Iterable[str]) -> dict[str, str]:
        ids = list(dict.fromkeys(order_ids))
        if not ids:
            return {}
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT id, order_number FROM orders WHERE id IN ({','.join('?' * len(ids))})", ids
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    # ------------------------------------------------------------------ shipments

    def get_freight_shipment(self, shipment_id: str) -> FreightShipment | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM freight_shipments WHERE id=? OR number=?", (shipment_id, shipment_id)
            ).fetchone()
        return FreightShipment.model_validate_json(row[0]) if row else None

    def list_freight_shipments(self, *, company_id: str | None = None, status: str | None = None,
                               search: str | None = None, limit: int = 300) -> list[FreightShipment]:
        sql = "SELECT s.payload_json FROM freight_shipments s WHERE 1=1"
        params: list[Any] = []
        if company_id:
            sql += " AND s.company_id=?"
            params.append(company_id)
        if status:
            sql += " AND s.status=?"
            params.append(status)
        if search:
            sql += (" AND (s.number LIKE ? OR s.payload_json LIKE ? OR EXISTS (SELECT 1 FROM shipment_orders so JOIN "
                    "orders o ON o.id=so.order_id WHERE so.shipment_id=s.id AND o.order_number LIKE ?))")
            params += [f"%{search}%"] * 3
        sql += " ORDER BY s.created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [FreightShipment.model_validate_json(row[0]) for row in rows]

    def active_shipment_for_orders(self, connection: sqlite3.Connection, order_ids: list[str],
                                   exclude_shipment_id: str | None = None) -> dict[str, str]:
        if not order_ids:
            return {}
        rows = connection.execute(
            f"SELECT so.order_id, s.number FROM shipment_orders so JOIN freight_shipments s ON s.id=so.shipment_id "
            f"WHERE so.order_id IN ({','.join('?' * len(order_ids))}) AND s.status IN "
            f"({','.join('?' * len(ACTIVE_SHIPMENT_STATUSES))}) AND s.id<>?",
            [*order_ids, *ACTIVE_SHIPMENT_STATUSES, exclude_shipment_id or ""],
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def write_freight_shipment(self, connection: sqlite3.Connection, shipment: FreightShipment, *,
                               action: str, summary: str, details: dict | None = None) -> None:
        if not shipment.number:
            shipment.number = self._next_number(connection, "shipment", "S")
        shipment.updated_at = datetime.now(UTC)
        connection.execute(
            """INSERT INTO freight_shipments VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               status=excluded.status, payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
            (shipment.id, shipment.number, shipment.company_id, shipment.status, shipment.model_dump_json(),
             shipment.created_at.isoformat(), shipment.updated_at.isoformat()),
        )
        current = {row[0] for row in connection.execute(
            "SELECT order_id FROM shipment_orders WHERE shipment_id=?", (shipment.id,))}
        wanted = set(shipment.order_ids)
        for order_id in wanted - current:
            connection.execute("INSERT INTO shipment_orders VALUES (?,?,?)", (shipment.id, order_id, _now()))
        for order_id in current - wanted:
            connection.execute(
                "DELETE FROM shipment_orders WHERE shipment_id=? AND order_id=?", (shipment.id, order_id)
            )
        self.audit(connection, "shipment", shipment.id, action, summary, details)

    def shipment_expenses(self, shipment_id: str, *, include_voided: bool = True) -> list[ShipmentExpense]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM shipment_expenses WHERE shipment_id=?"
                + ("" if include_voided else " AND voided=0") + " ORDER BY created_at", (shipment_id,)
            ).fetchall()
        return [ShipmentExpense.model_validate_json(row[0]) for row in rows]

    def write_expense(self, connection: sqlite3.Connection, expense: ShipmentExpense) -> None:
        connection.execute(
            """INSERT INTO shipment_expenses VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               amount_cents=excluded.amount_cents, voided=excluded.voided, payload_json=excluded.payload_json""",
            (expense.id, expense.shipment_id, expense.amount_cents, int(expense.voided), expense.model_dump_json(),
             expense.created_at.isoformat()),
        )

    def get_allocation(self, shipment_id: str) -> Allocation | None:
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM expense_allocations WHERE shipment_id=?",
                                     (shipment_id,)).fetchone()
        return Allocation.model_validate_json(row[0]) if row else None

    def write_allocation(self, connection: sqlite3.Connection, allocation: Allocation | None, shipment_id: str) -> None:
        if allocation is None:
            connection.execute("DELETE FROM expense_allocations WHERE shipment_id=?", (shipment_id,))
            return
        connection.execute(
            "INSERT OR REPLACE INTO expense_allocations VALUES (?,?,?)",
            (shipment_id, allocation.model_dump_json(), allocation.created_at.isoformat()),
        )

    @staticmethod
    def shipment_is_locked(shipment: FreightShipment) -> bool:
        return shipment.status in LOCKED_SHIPMENT_STATUSES

    # ================================================================== 3.2 workflow helpers
    # Extra revenue, bills without a shipment, prepared-email log, reference ownership and the
    # single-pass reads used by reports and the needs-attention list. Services: services/quote_workflow.py,
    # services/emails.py, services/attention.py, services/profit_reports.py.

    def write_revenue(self, connection: sqlite3.Connection, revenue: ShipmentRevenue) -> None:
        connection.execute(
            """INSERT INTO shipment_revenue VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               shipment_id=excluded.shipment_id, amount_cents=excluded.amount_cents, voided=excluded.voided,
               payload_json=excluded.payload_json""",
            (revenue.id, revenue.company_id, revenue.shipment_id, revenue.amount_cents, int(revenue.voided),
             revenue.model_dump_json(), revenue.created_at.isoformat()),
        )

    def get_revenue(self, revenue_id: str) -> ShipmentRevenue | None:
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM shipment_revenue WHERE id=?", (revenue_id,)).fetchone()
        return ShipmentRevenue.model_validate_json(row[0]) if row else None

    def list_revenue(self, *, shipment_id: str | None = None, company_id: str | None = None,
                     unassigned: bool = False, include_voided: bool = True) -> list[ShipmentRevenue]:
        sql = "SELECT payload_json FROM shipment_revenue WHERE 1=1"
        params: list[Any] = []
        if shipment_id:
            sql += " AND shipment_id=?"
            params.append(shipment_id)
        if company_id:
            sql += " AND company_id=?"
            params.append(company_id)
        if unassigned:
            sql += " AND shipment_id IS NULL"
        if not include_voided:
            sql += " AND voided=0"
        with self.connect() as connection:
            rows = connection.execute(sql + " ORDER BY created_at", params).fetchall()
        return [ShipmentRevenue.model_validate_json(row[0]) for row in rows]

    def write_unassigned_expense(self, connection: sqlite3.Connection, expense: UnassignedExpense) -> None:
        connection.execute(
            """INSERT INTO unassigned_expenses VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               amount_cents=excluded.amount_cents, voided=excluded.voided, payload_json=excluded.payload_json""",
            (expense.id, expense.company_id, expense.amount_cents, int(expense.voided), expense.model_dump_json(),
             expense.created_at.isoformat()),
        )

    def get_unassigned_expense(self, expense_id: str) -> UnassignedExpense | None:
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM unassigned_expenses WHERE id=?",
                                     (expense_id,)).fetchone()
        return UnassignedExpense.model_validate_json(row[0]) if row else None

    def list_unassigned_expenses(self, *, company_id: str | None = None,
                                 include_voided: bool = False) -> list[UnassignedExpense]:
        sql = "SELECT payload_json FROM unassigned_expenses WHERE 1=1"
        params: list[Any] = []
        if company_id:
            sql += " AND company_id=?"
            params.append(company_id)
        if not include_voided:
            sql += " AND voided=0"
        with self.connect() as connection:
            rows = connection.execute(sql + " ORDER BY created_at DESC", params).fetchall()
        return [UnassignedExpense.model_validate_json(row[0]) for row in rows]

    def write_email_record(self, connection: sqlite3.Connection, record: EmailRecord) -> None:
        connection.execute(
            "INSERT INTO email_log VALUES (?,?,?,?,?,?,?)",
            (record.id, record.company_id, record.kind, record.entity_type, record.entity_id,
             record.model_dump_json(), record.created_at.isoformat()),
        )

    def list_email_records(self, *, entity_type: str | None = None, entity_id: str | None = None,
                           company_id: str | None = None, limit: int = 200) -> list[EmailRecord]:
        sql = "SELECT payload_json FROM email_log WHERE 1=1"
        params: list[Any] = []
        for column, value in (("entity_type", entity_type), ("entity_id", entity_id), ("company_id", company_id)):
            if value:
                sql += f" AND {column}=?"
                params.append(value)
        with self.connect() as connection:
            rows = connection.execute(sql + " ORDER BY created_at DESC LIMIT ?", [*params, limit]).fetchall()
        return [EmailRecord.model_validate_json(row[0]) for row in rows]

    @staticmethod
    def reference_owner(connection: sqlite3.Connection, company_id: str, reference: str) -> str | None:
        """The order that answers to ``reference`` (its number or one of its references), if any."""

        row = connection.execute(
            "SELECT id FROM orders WHERE company_id=? AND order_number=? COLLATE NOCASE "
            "UNION ALL SELECT order_id FROM order_references WHERE company_id=? AND reference=? COLLATE NOCASE "
            "LIMIT 1", (company_id, reference, company_id, reference),
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def active_shipments_by_order(connection: sqlite3.Connection, order_ids: list[str]) -> dict[str, str]:
        """order id -> id of its active (not cancelled) shipment."""

        if not order_ids:
            return {}
        rows = connection.execute(
            f"SELECT so.order_id, s.id FROM shipment_orders so JOIN freight_shipments s ON s.id=so.shipment_id "
            f"WHERE so.order_id IN ({','.join('?' * len(order_ids))}) AND s.status IN "
            f"({','.join('?' * len(ACTIVE_SHIPMENT_STATUSES))})",
            [*order_ids, *ACTIVE_SHIPMENT_STATUSES],
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    # Single-pass reads (one query per table, only the fields needed; json_extract with several paths
    # parses each payload once). Used by reports, freight records and the needs-attention list.

    @staticmethod
    def _company_filter(company_id: str | None, column: str = "company_id") -> tuple[str, list[Any]]:
        return (f" WHERE {column}=?", [company_id]) if company_id else ("", [])

    def _cached_rows(self, table: str, key: tuple, loader):
        """Reuse rows read before while ``table`` is unchanged (row count, latest update, total payload bytes).

        The fingerprint is read before the rows, so a write in between only causes one extra reload. Callers
        must not modify the returned rows.
        """

        with self.connect() as connection:
            fingerprint = tuple(connection.execute(
                f"SELECT COUNT(*), MAX(updated_at), TOTAL(LENGTH(CAST(payload_json AS BLOB))) FROM {table}"
            ).fetchone())
        cache = self.__dict__.setdefault("_workflow_rows_cache", {})
        hit = cache.get(key)
        if hit is not None and hit[0] == fingerprint:
            return hit[1]
        rows = loader()
        cache[key] = (fingerprint, rows)
        return rows

    def workflow_shipment_rows(self, company_id: str | None = None) -> list[dict]:
        return self._cached_rows("freight_shipments", ("shipments", company_id),
                                 lambda: self._load_shipment_rows(company_id))

    def _json_rows(self, table: str, columns: tuple[str, ...], fields: tuple[str, ...], where: str,
                   params: list[Any]) -> list[dict]:
        """Table columns plus payload fields for every row, returned by SQLite as one JSON document.

        Each payload is parsed once (json_extract with several paths) and Python decodes a single string,
        which is several times faster than decoding one payload per row.
        """

        paths = ", ".join(f"'$.{field}'" for field in fields)
        with self.connect() as connection:
            text = connection.execute(
                f"SELECT json_group_array(json_array({', '.join(columns)}, json(json_extract(payload_json, {paths}))))"
                f" FROM {table}{where}", params).fetchone()[0]
        keys = (*columns, *fields)
        return [dict(zip(keys, (*row[:-1], *row[-1]), strict=True)) for row in json.loads(text or "[]")]

    def _load_shipment_rows(self, company_id: str | None) -> list[dict]:
        fields = ("name", "carrier", "broker", "purpose", "charge_source", "customer_charge_cents", "pickup_date",
                  "delivery_date", "expected_delivery_date", "combined_quote_id", "order_ids", "actual",
                  "external_source", "pro_number")
        where, params = self._company_filter(company_id)
        return self._json_rows("freight_shipments", ("id", "number", "company_id", "status", "created_at",
                                                     "updated_at"), fields, where, params)

    def workflow_quote_rows(self, company_id: str | None = None, *,
                            exclude_statuses: tuple[str, ...] = ()) -> list[dict]:
        return self._cached_rows("quotes", ("quotes", company_id, exclude_statuses),
                                 lambda: self._load_quote_rows(company_id, exclude_statuses))

    def _load_quote_rows(self, company_id: str | None, exclude_statuses: tuple[str, ...]) -> list[dict]:
        fields = ("customer_status", "sent_at", "responded_at", "freight_shipment_id", "role", "cost.amount_cents",
                  "cost.customer_price_cents", "cost.carrier", "cost.expires_on", "summary.pallets",
                  "summary.total_weight", "order_ids", "legacy", "external_source")
        where, params = self._company_filter(company_id)
        if exclude_statuses:
            where += (" AND " if where else " WHERE ") + f"status NOT IN ({','.join('?' * len(exclude_statuses))})"
            params += list(exclude_statuses)
        return self._json_rows("quotes", ("id", "number", "company_id", "kind", "status", "created_at"), fields,
                               where, params)

    def workflow_order_numbers(self, company_id: str | None = None) -> dict[str, str]:
        where, params = self._company_filter(company_id)
        with self.connect() as connection:
            return dict(connection.execute(f"SELECT id, order_number FROM orders{where}", params).fetchall())

    def workflow_order_fields(self, company_id: str | None = None) -> dict[str, dict]:
        return self._cached_rows("orders", ("orders", company_id), lambda: self._load_order_fields(company_id))

    def _load_order_fields(self, company_id: str | None) -> dict[str, dict]:
        where, params = self._company_filter(company_id)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, order_number, json_extract(payload_json, '$.customer_name', '$.sales_rep') "
                f"FROM orders{where}", params).fetchall()
        result = {}
        for row in rows:
            customer, rep = json.loads(row[2])
            result[row[0]] = {"order_number": row[1], "customer_name": customer or "", "sales_rep": rep or ""}
        return result

    def expense_sums_by_shipment(self, company_id: str | None = None) -> dict[str, tuple[int, int]]:
        """shipment id -> (sum of non-void expenses in cents, count)."""

        sql = ("SELECT e.shipment_id, SUM(e.amount_cents), COUNT(*) FROM shipment_expenses e "
               "JOIN freight_shipments s ON s.id=e.shipment_id WHERE e.voided=0")
        params: list[Any] = []
        if company_id:
            sql += " AND s.company_id=?"
            params.append(company_id)
        with self.connect() as connection:
            rows = connection.execute(sql + " GROUP BY e.shipment_id", params).fetchall()
        return {row[0]: (int(row[1]), int(row[2])) for row in rows}

    def revenue_sums_by_shipment(self, company_id: str | None = None) -> dict[str, int]:
        sql = "SELECT shipment_id, SUM(amount_cents) FROM shipment_revenue WHERE voided=0 AND shipment_id IS NOT NULL"
        params: list[Any] = []
        if company_id:
            sql += " AND company_id=?"
            params.append(company_id)
        with self.connect() as connection:
            rows = connection.execute(sql + " GROUP BY shipment_id", params).fetchall()
        return {row[0]: int(row[1]) for row in rows}

    def unassigned_money_rows(self, company_id: str | None = None) -> dict[str, list[dict]]:
        """Non-void expenses and revenue without a shipment: amount and date (for reports and attention)."""

        params: list[Any] = [company_id] if company_id else []
        company = " AND company_id=?" if company_id else ""
        with self.connect() as connection:
            expenses = connection.execute(
                "SELECT id, amount_cents, created_at, json_extract(payload_json, '$.incurred_on') "
                f"FROM unassigned_expenses WHERE voided=0{company}", params).fetchall()
            revenue = connection.execute(
                "SELECT id, amount_cents, created_at, json_extract(payload_json, '$.received_on') "
                f"FROM shipment_revenue WHERE voided=0 AND shipment_id IS NULL{company}", params).fetchall()
        return {
            "expenses": [{"id": r[0], "amount_cents": r[1], "created_at": r[2], "date": r[3]} for r in expenses],
            "revenue": [{"id": r[0], "amount_cents": r[1], "created_at": r[2], "date": r[3]} for r in revenue],
        }

    # ------------------------------------------------------------------ reporting

    def freight_records(self, *, company_id: str | None = None) -> list[dict]:
        """One row per shipment with estimate, actual and allocation status (Freight Data page/export)."""

        shipments = self.list_freight_shipments(company_id=company_id, limit=5000)
        with self.connect() as connection:
            expense_totals = {
                row[0]: (row[1], row[2])
                for row in connection.execute(
                    "SELECT shipment_id, SUM(amount_cents), COUNT(*) FROM shipment_expenses WHERE voided=0 "
                    "GROUP BY shipment_id"
                )
            }
            allocated = {row[0] for row in connection.execute("SELECT shipment_id FROM expense_allocations")}
        from .services.freight import separate_estimates as sum_separate

        quotes = {quote.id: quote for quote in self.list_quotes(company_id=company_id, limit=100000)}
        numbers = self.order_numbers(order_id for shipment in shipments for order_id in shipment.order_ids)
        records = []
        for shipment in shipments:
            combined = quotes.get(shipment.combined_quote_id or "")
            if combined and combined.status == "void":
                combined = None
            related = [quote for quote in quotes.values() if set(quote.order_ids) & set(shipment.order_ids)]
            estimate = combined.cost.amount_cents if combined and combined.cost.amount_cents is not None else None
            separate_total, separate_complete = sum_separate(related, shipment)
            actual, count = expense_totals.get(shipment.id, (None, 0))
            records.append({
                "id": shipment.id,
                "number": shipment.number,
                "name": shipment.name,
                "status": shipment.status,
                "carrier": shipment.carrier,
                "broker": shipment.broker,
                "pickup_date": shipment.pickup_date.isoformat() if shipment.pickup_date else None,
                "orders": [numbers.get(order_id, order_id) for order_id in shipment.order_ids],
                "pallets": combined.summary.pallets if combined else None,
                "weight": combined.summary.total_weight if combined else None,
                "estimate_cents": estimate,
                "separate_estimates_cents": separate_total if separate_complete else None,
                "actual_cents": actual,
                "expense_count": count,
                "allocated": shipment.id in allocated,
                "variance_cents": (actual - estimate) if actual is not None and estimate is not None else None,
                "created_at": shipment.created_at.isoformat(),
            })
        return records


def normalized_reference(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
