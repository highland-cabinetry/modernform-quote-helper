from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .freight_repository import FreightRepository
from .models import ItemMaster, PackingResult, PackingSettings, SkuAlias
from .release import RELEASE
from .services.product_exceptions import is_status_placeholder
from .services.sku_matching import normalize_sku
from .workflow_repository import WorkflowRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "quote_helper_v2.sqlite3"

# Raise this only for a schema change that older versions cannot read safely (a dropped or
# renamed column, a changed meaning). Additive changes (new tables or columns) keep it the same,
# so an older version can still open the database after an update is rolled back.
DATABASE_COMPATIBILITY = 1
UPDATE_BACKUP_PREFIX = "quote-helper-v2-before-update-"
UPDATE_BACKUPS_KEPT = 10
SEED_MARKER = "edition_seed"
SEED_ENV = "QUOTE_HELPER_SEED_CATALOG"  # "0" turns the starter catalog off


class AliasConflictError(ValueError):
    pass


class DatabaseTooNewError(RuntimeError):
    """The database was changed by a newer Quote Helper in a way this version cannot read."""


class Repository(WorkflowRepository, FreightRepository):
    """Small, explicit SQLite repository for local/offline warehouse use."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        backup_directory: str | Path | None = None,
    ) -> None:
        configured = database_path or os.getenv("QUOTE_HELPER_V2_DATABASE")
        self.database_path = Path(configured) if configured else DEFAULT_DATABASE_PATH
        self.backup_directory = (
            Path(backup_directory) if backup_directory else self.database_path.parent / "backups"
        )
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        update = self._prepare_for_app_version()
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS item_master (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sku TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    payload_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shipments (
                    id TEXT PRIMARY KEY,
                    shipment_name TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sku_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alias_sku TEXT NOT NULL,
                    normalized_alias TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    item_master_id INTEGER NOT NULL REFERENCES item_master(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_shipments_created_at ON shipments(created_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_sku_aliases_item ON sku_aliases(item_master_id)")
            connection.execute("PRAGMA optimize")
        self.initialize_workflows()
        self.initialize_freight()
        self.apply_edition_seed()
        self._record_app_version(update)

    # ------------------------------------------------------------------ starter data (edition.py)

    def apply_edition_seed(self) -> dict[str, Any]:
        """Add the edition's starter catalog and packing settings, once per seed version.

        Never changes existing data: catalog items whose SKU is already present (active or not) are
        left alone, and packing settings are written only when none were saved yet. A later release
        can raise ``seed_version`` to add new products to existing installations the same way.
        """

        from .edition import current, seed_path

        if os.environ.get(SEED_ENV) == "0":
            return {"applied": False, "reason": "disabled"}
        path = seed_path(current())
        if path is None or not path.exists():
            return {"applied": False, "reason": "no seed"}
        seed = json.loads(path.read_text(encoding="utf-8"))
        version = int(seed.get("seed_version") or 1)
        with self.connect() as connection:
            row = connection.execute("SELECT value_json FROM settings WHERE key = ?", (SEED_MARKER,)).fetchone()
        applied = int(json.loads(row["value_json"]).get("seed_version") or 0) if row else 0
        if applied >= version:
            return {"applied": False, "reason": "already applied", "seed_version": applied}
        items = [ItemMaster.model_validate(raw) for raw in seed.get("items") or []]
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            present = {str(sku).upper() for (sku,) in connection.execute("SELECT sku FROM item_master")}
            added = [item for item in items if item.sku.upper() not in present]
            self._insert_items(connection, added)
            settings_written = False
            if seed.get("settings") and not connection.execute(
                "SELECT 1 FROM settings WHERE key = 'packing'"
            ).fetchone():
                settings = PackingSettings.model_validate(seed["settings"])
                connection.execute(
                    "INSERT INTO settings(key, value_json, updated_at) VALUES ('packing', ?, ?)",
                    (settings.model_dump_json(), now),
                )
                settings_written = True
            connection.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
                (SEED_MARKER, json.dumps({"seed_version": version, "edition": current().name,
                                          "items_added": len(added)}), now),
            )
        return {"applied": True, "seed_version": version, "items_added": len(added),
                "settings_written": settings_written}

    # ------------------------------------------------------------------ version changes

    def _prepare_for_app_version(self) -> dict[str, Any]:
        """Refuse a database a newer version made unreadable; back it up before a new version opens it.

        Runs before any schema change, so the backup is the database exactly as the previous
        version left it.
        """
        version = str(RELEASE["version"])
        if not self.database_path.exists() or self.database_path.stat().st_size == 0:
            return {"version": version, "previous": None, "backup": None}
        with self.connect() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            meta: dict[str, str] = {}
            if "app_meta" in tables:
                meta = {row[0]: row[1] for row in connection.execute("SELECT key, value FROM app_meta")}
        required = int(meta.get("database_compatibility") or 0)
        if required > DATABASE_COMPATIBILITY:
            raise DatabaseTooNewError(
                f"This database was updated by Quote Helper {meta.get('last_app_version', 'a newer version')}, "
                f"which changed it in a way version {version} cannot read. Install that version or newer. "
                f"Database: {self.database_path}"
            )
        previous = meta.get("last_app_version")
        backup = None
        if tables and previous != version:
            backup = self._verified_backup(f"before-update-{_safe_label(version)}")
            self._prune_update_backups()
        return {"version": version, "previous": previous, "backup": str(backup) if backup else None}

    def _record_app_version(self, update: dict[str, Any]) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS app_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT NOT NULL, previous_version TEXT,
                    backup_path TEXT, opened_at TEXT NOT NULL)"""
            )
            current = connection.execute("SELECT value FROM app_meta WHERE key='last_app_version'").fetchone()
            if current and current[0] == update["version"]:
                return
            stored = connection.execute(
                "SELECT value FROM app_meta WHERE key='database_compatibility'"
            ).fetchone()
            compatibility = max(DATABASE_COMPATIBILITY, int(stored[0]) if stored else 0)
            connection.executemany(
                "INSERT INTO app_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [("last_app_version", update["version"]), ("database_compatibility", str(compatibility))],
            )
            connection.execute(
                "INSERT INTO app_versions(version, previous_version, backup_path, opened_at) VALUES (?, ?, ?, ?)",
                (update["version"], update["previous"], update["backup"], now),
            )

    def _prune_update_backups(self) -> None:
        backups = sorted(self.backup_directory.glob(f"{UPDATE_BACKUP_PREFIX}*.sqlite3"),
                         key=lambda path: path.stat().st_mtime, reverse=True)
        for backup in backups[UPDATE_BACKUPS_KEPT:]:
            try:
                backup.unlink()
            except OSError:
                pass  # an old backup that cannot be removed is harmless

    def item_count(self) -> int:
        with self.connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM item_master WHERE active = 1").fetchone()[0])

    def database_counts(self) -> dict[str, int]:
        with self.connect() as connection:
            return {
                "active_items": int(
                    connection.execute("SELECT COUNT(*) FROM item_master WHERE active = 1").fetchone()[0]
                ),
                "all_items": int(connection.execute("SELECT COUNT(*) FROM item_master").fetchone()[0]),
                "shipments": int(connection.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]),
                "settings": int(connection.execute("SELECT COUNT(*) FROM settings").fetchone()[0]),
            }

    def migrate_item_handling_metadata(self) -> dict[str, Any]:
        """Persist V2.4 item-kind/bundle defaults without touching user workflow data."""

        self.backup_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        backup_path = self.backup_directory / f"quote-helper-v2-before-item-kind-{stamp}.sqlite3"
        with sqlite3.connect(self.database_path) as source, sqlite3.connect(backup_path) as destination:
            source.backup(destination)
        with sqlite3.connect(backup_path) as backup:
            integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            backup_path.unlink(missing_ok=True)
            raise RuntimeError(f"Item metadata backup failed integrity check: {integrity}")

        updated = 0
        counts = {"cabinet": 0, "cabinet_component": 0, "accessory": 0, "other": 0}
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            rows = connection.execute("SELECT id, payload_json FROM item_master").fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                item = ItemMaster(**payload)
                normalized = item.model_dump(mode="json", exclude={"id"})
                counts[item.item_kind.value] += 1
                if item.item_kind.value == "accessory":
                    normalized["bundle_approved"] = True
                    normalized["bundle_max_units"] = 5
                    if normalized.get("packing_role") == "standard":
                        normalized["packing_role"] = "top_accessory"
                else:
                    normalized["bundle_approved"] = False
                    normalized["bundle_max_units"] = 1
                if str(item.sku).upper().startswith(("PC", "OC")):
                    normalized["allow_edge_standing"] = False
                if normalized != payload:
                    connection.execute(
                        "UPDATE item_master SET payload_json = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(normalized), now, int(row["id"])),
                    )
                    updated += 1
        return {"updated": updated, "counts": counts, "backup": str(backup_path), "integrity_check": integrity}

    def preview_item_master_replacement(self, items: list[ItemMaster]) -> dict[str, Any]:
        with self.connect() as connection:
            current_skus = {
                str(row["sku"]).upper()
                for row in connection.execute("SELECT sku FROM item_master").fetchall()
            }
            alias_targets = [
                str(row["sku"]).upper()
                for row in connection.execute(
                    """
                    SELECT item_master.sku
                    FROM sku_aliases JOIN item_master ON item_master.id = sku_aliases.item_master_id
                    """
                ).fetchall()
            ]
        incoming_skus = {item.sku.upper() for item in items}
        counts = self.database_counts()
        return {
            "incoming_rows": len(items),
            "current_items_removed": counts["all_items"],
            "active_items_removed": counts["active_items"],
            "shipments_deleted": counts["shipments"],
            "settings_reset": counts["settings"],
            "sku_overlap": len(current_skus & incoming_skus),
            "new_skus": len(incoming_skus - current_skus),
            "obsolete_skus": len(current_skus - incoming_skus),
            "aliases_preserved": sum(target in incoming_skus for target in alias_targets),
            "aliases_removed": sum(target not in incoming_skus for target in alias_targets),
        }

    def create_backup(self) -> Path:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        backup_path = self.backup_directory / f"quote-helper-v2-before-master-replace-{timestamp}.sqlite3"
        source = sqlite3.connect(self.database_path, timeout=30)
        destination = sqlite3.connect(backup_path)
        try:
            source.execute("PRAGMA busy_timeout = 30000")
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError("The automatic database backup failed its integrity check")
        finally:
            destination.close()
            source.close()
        return backup_path

    @staticmethod
    def _insert_items(connection: sqlite3.Connection, items: list[ItemMaster]) -> None:
        now = datetime.now(UTC).isoformat()
        for item in items:
            payload = item.model_dump(exclude={"id"}, mode="json")
            cursor = connection.execute(
                "INSERT INTO item_master(sku, payload_json, active, updated_at) VALUES (?, ?, ?, ?)",
                (item.sku, json.dumps(payload), int(item.active), now),
            )
            item.id = int(cursor.lastrowid)

    def _prune_backups(self, retain: int) -> list[str]:
        warnings: list[str] = []
        backups = sorted(
            self.backup_directory.glob("quote-helper-v2-before-master-replace-*.sqlite3"),
            key=lambda path: path.name,
            reverse=True,
        )
        for backup in backups[max(1, retain) :]:
            try:
                backup.unlink()
            except OSError as exc:
                warnings.append(f"Could not remove old backup {backup.name}: {exc}")
        return warnings

    def replace_all_data(self, items: list[ItemMaster], *, backup_retention: int = 10) -> dict[str, Any]:
        if not items:
            raise ValueError("A replacement item master must contain at least one valid item")
        before = self.database_counts()
        backup_path = self.create_backup()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            aliases = connection.execute(
                """
                SELECT sku_aliases.alias_sku, sku_aliases.normalized_alias,
                       sku_aliases.created_at, item_master.sku AS target_sku
                FROM sku_aliases JOIN item_master ON item_master.id = sku_aliases.item_master_id
                """
            ).fetchall()
            connection.execute("DELETE FROM shipments")
            # The starter-catalog marker always stays (the starter catalog must not come back on the
            # next start). Editions other than Highland also keep their packing settings: ship-from
            # address, cabinet colors and pallet limits are not part of a catalog workbook.
            from .edition import current as current_edition

            kept = [SEED_MARKER] + (["packing"] if current_edition().name != "highland" else [])
            connection.execute(f"DELETE FROM settings WHERE key NOT IN ({','.join('?' * len(kept))})", kept)
            connection.execute("DELETE FROM sku_aliases")
            connection.execute("DELETE FROM item_master")
            connection.execute("DELETE FROM sqlite_sequence WHERE name = 'item_master'")
            self._insert_items(connection, items)
            target_ids = {
                str(row["sku"]).upper(): int(row["id"])
                for row in connection.execute("SELECT id, sku FROM item_master").fetchall()
            }
            now = datetime.now(UTC).isoformat()
            preserved_aliases = 0
            for alias in aliases:
                target_id = target_ids.get(str(alias["target_sku"]).upper())
                if target_id is None:
                    continue
                connection.execute(
                    """
                    INSERT INTO sku_aliases(
                        alias_sku, normalized_alias, item_master_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        alias["alias_sku"], alias["normalized_alias"], target_id,
                        alias["created_at"], now,
                    ),
                )
                preserved_aliases += 1
        warnings = self._prune_backups(backup_retention)
        return {
            "inserted": len(items),
            "items_removed": before["all_items"],
            "shipments_deleted": before["shipments"],
            "settings_reset": before["settings"],
            "aliases_preserved": preserved_aliases,
            "aliases_removed": len(aliases) - preserved_aliases,
            "backup_name": backup_path.name,
            "backup_path": str(backup_path.resolve()),
            "warnings": warnings,
        }

    @staticmethod
    def _alias_from_row(row: sqlite3.Row) -> SkuAlias:
        payload = json.loads(row["payload_json"])
        return SkuAlias(
            id=int(row["id"]),
            alias_sku=str(row["alias_sku"]),
            normalized_alias=str(row["normalized_alias"]),
            target_item_id=int(row["item_master_id"]),
            target_sku=str(row["target_sku"]),
            target_description=str(payload.get("description") or ""),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def list_sku_aliases(self, search: str | None = None) -> list[SkuAlias]:
        sql = """
            SELECT sku_aliases.*, item_master.sku AS target_sku, item_master.payload_json
            FROM sku_aliases JOIN item_master ON item_master.id = sku_aliases.item_master_id
        """
        params: list[object] = []
        if search:
            sql += " WHERE sku_aliases.alias_sku LIKE ? OR item_master.sku LIKE ?"
            params.extend([f"%{search}%", f"%{search}%"])
        sql += " ORDER BY sku_aliases.alias_sku COLLATE NOCASE"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._alias_from_row(row) for row in rows]

    def alias_targets(self) -> dict[str, ItemMaster]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT sku_aliases.normalized_alias, item_master.id, item_master.payload_json
                FROM sku_aliases JOIN item_master ON item_master.id = sku_aliases.item_master_id
                WHERE item_master.active = 1
                """
            ).fetchall()
        return {
            str(row["normalized_alias"]): ItemMaster(
                id=int(row["id"]), **json.loads(row["payload_json"])
            )
            for row in rows
        }

    def save_sku_alias(
        self,
        alias_sku: str,
        target_item_id: int,
        *,
        ignored_prefixes: list[str],
    ) -> SkuAlias:
        if is_status_placeholder(alias_sku):
            raise ValueError("Back-order status placeholders cannot be saved as product aliases")
        normalized = normalize_sku(alias_sku, ignored_prefixes)
        if not normalized:
            raise ValueError("Alias must contain letters or numbers after normalization")
        with self.connect() as connection:
            target = connection.execute(
                "SELECT id, sku, payload_json FROM item_master WHERE id = ? AND active = 1",
                (target_item_id,),
            ).fetchone()
            if not target:
                raise ValueError("Target item was not found or is inactive")

            for row in connection.execute(
                "SELECT id, sku, payload_json FROM item_master WHERE active = 1"
            ).fetchall():
                item = ItemMaster(id=int(row["id"]), **json.loads(row["payload_json"]))
                identifiers = [item.sku, item.manufacturer_sku]
                if any(
                    identifier and normalize_sku(identifier, ignored_prefixes) == normalized
                    for identifier in identifiers
                ):
                    raise AliasConflictError(
                        f"{alias_sku} already resolves through the item master and cannot be saved as an alias"
                    )

            existing = connection.execute(
                "SELECT item_master_id FROM sku_aliases WHERE normalized_alias = ? COLLATE NOCASE",
                (normalized,),
            ).fetchone()
            if existing and int(existing["item_master_id"]) != target_item_id:
                raise AliasConflictError(
                    "That normalized alias is already assigned to another item; delete it before reassigning"
                )

            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                INSERT INTO sku_aliases(
                    alias_sku, normalized_alias, item_master_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(normalized_alias) DO UPDATE SET
                    alias_sku = excluded.alias_sku,
                    updated_at = excluded.updated_at
                """,
                (alias_sku.strip(), normalized, target_item_id, now, now),
            )
            row = connection.execute(
                """
                SELECT sku_aliases.*, item_master.sku AS target_sku, item_master.payload_json
                FROM sku_aliases JOIN item_master ON item_master.id = sku_aliases.item_master_id
                WHERE sku_aliases.normalized_alias = ? COLLATE NOCASE
                """,
                (normalized,),
            ).fetchone()
        return self._alias_from_row(row)

    def delete_sku_alias(self, alias_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM sku_aliases WHERE id = ?", (alias_id,))
        return cursor.rowcount > 0

    def upsert_items(self, items: list[ItemMaster]) -> tuple[int, int]:
        inserted = 0
        updated = 0
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            for item in items:
                existing = connection.execute(
                    "SELECT id FROM item_master WHERE sku = ? COLLATE NOCASE", (item.sku,)
                ).fetchone()
                payload = item.model_dump(exclude={"id"}, mode="json")
                if existing:
                    item.id = int(existing["id"])
                    connection.execute(
                        "UPDATE item_master SET payload_json = ?, active = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(payload), int(item.active), now, item.id),
                    )
                    updated += 1
                else:
                    cursor = connection.execute(
                        "INSERT INTO item_master(sku, payload_json, active, updated_at) VALUES (?, ?, ?, ?)",
                        (item.sku, json.dumps(payload), int(item.active), now),
                    )
                    item.id = int(cursor.lastrowid)
                    inserted += 1
        return inserted, updated

    def list_items(self, *, active_only: bool = True, search: str | None = None) -> list[ItemMaster]:
        sql = "SELECT id, payload_json FROM item_master"
        params: list[object] = []
        clauses: list[str] = []
        if active_only:
            clauses.append("active = 1")
        if search:
            clauses.append("(sku LIKE ? OR payload_json LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY sku"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [ItemMaster(id=int(row["id"]), **json.loads(row["payload_json"])) for row in rows]

    def get_item(self, item_id: int) -> ItemMaster | None:
        with self.connect() as connection:
            row = connection.execute("SELECT id, payload_json FROM item_master WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        return ItemMaster(id=int(row["id"]), **json.loads(row["payload_json"]))

    def delete_item(self, item_id: int) -> bool:
        with self.connect() as connection:
            connection.execute("DELETE FROM sku_aliases WHERE item_master_id = ?", (item_id,))
            cursor = connection.execute(
                "UPDATE item_master SET active = 0, updated_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), item_id),
            )
        return cursor.rowcount > 0

    def save_shipment(self, request_payload: dict, result: PackingResult) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO shipments(id, shipment_name, request_json, result_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    result.id,
                    result.shipment_name,
                    json.dumps(request_payload),
                    result.model_dump_json(),
                    result.created_at.isoformat(),
                ),
            )

    def get_shipment(self, shipment_id: str) -> PackingResult | None:
        with self.connect() as connection:
            row = connection.execute("SELECT result_json FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
        return PackingResult.model_validate_json(row["result_json"]) if row else None

    def update_shipment_result(self, result: PackingResult) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE shipments SET result_json = ? WHERE id = ?",
                (result.model_dump_json(), result.id),
            )
        if cursor.rowcount == 0:
            raise ValueError("Saved shipment was not found")

    def list_shipments(self, limit: int = 50) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, shipment_name, created_at,
                       json_extract(result_json, '$.summary.total_items') AS total_items,
                       json_extract(result_json, '$.summary.total_pallets') AS total_pallets,
                       json_extract(result_json, '$.summary.total_freight_weight') AS total_weight,
                       json_extract(result_json, '$.human_review_required') AS needs_review
                FROM shipments ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_settings(self, settings: PackingSettings) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value_json, updated_at) VALUES ('packing', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (settings.model_dump_json(), datetime.now(UTC).isoformat()),
            )

    def get_settings(self) -> PackingSettings:
        with self.connect() as connection:
            row = connection.execute("SELECT value_json FROM settings WHERE key = 'packing'").fetchone()
        return PackingSettings.model_validate_json(row["value_json"]) if row else PackingSettings()


repository = Repository()


def _safe_label(text: str) -> str:
    return "".join(character if character.isalnum() or character in ".-" else "-" for character in text)
