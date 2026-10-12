"""Make a verified, online copy of the Quote Helper V2 SQLite database.

Usage (from the project folder, PowerShell or cmd):

    .venv\\Scripts\\python.exe scripts\\backup_database.py
    .venv\\Scripts\\python.exe scripts\\backup_database.py --keep 20
    .venv\\Scripts\\python.exe scripts\\backup_database.py --output-dir D:\\Backups\\QuoteHelper
    .venv\\Scripts\\python.exe scripts\\backup_database.py --output tmp\\preview\\quote_helper_v2-preview.sqlite3

Defaults: copies data\\quote_helper_v2.sqlite3 (or --database) into
data\\backups\\quote-helper-v2-manual-<UTC stamp>.sqlite3 (or --output-dir), or to the exact
file named by --output. The copy uses the SQLite online backup API, so it is consistent even while
Quote Helper is running. The copy is integrity-checked before it gets its final name. An existing
file is never overwritten. The source is opened read-only and never modified.

--keep N removes the oldest backups so that N remain. It only removes files named
quote-helper-v2-manual-*.sqlite3 in the output folder. Automatic backups that the application
writes (and any other file) are never touched.

Prints the path of the new copy on stdout. Exit code 0 on success, 1 on any failure.
--root points the defaults at another project folder (used by the tests).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_RELATIVE = Path("data") / "quote_helper_v2.sqlite3"
BACKUP_RELATIVE = Path("data") / "backups"
MANUAL_PREFIX = "quote-helper-v2-manual-"
SUFFIX = ".sqlite3"


class BackupError(RuntimeError):
    pass


def manual_backup_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{MANUAL_PREFIX}{stamp}{SUFFIX}"


def _remove_sqlite_files(path: Path) -> None:
    for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm"),
                      path.with_name(path.name + "-journal")):
        candidate.unlink(missing_ok=True)


def backup_database(source: Path, *, output_dir: Path | None = None, output: Path | None = None) -> Path:
    """Copy ``source`` into a new, verified SQLite file and return its path."""

    source = Path(source).resolve()
    if not source.is_file():
        raise BackupError(f"Database not found: {source}")
    if output is not None:
        destination = Path(output).resolve()
    else:
        if output_dir is None:
            raise BackupError("An output folder or output file is required.")
        destination = Path(output_dir).resolve() / manual_backup_name()
    if destination == source:
        raise BackupError("The backup must not be written over the database itself.")
    if destination.exists():
        raise BackupError(f"Refusing to overwrite an existing file: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    _remove_sqlite_files(partial)

    try:
        # mode=ro: the source is never written, and is never created if missing.
        reader = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)
        try:
            writer = sqlite3.connect(partial)
            try:
                reader.backup(writer)
                # A standalone file: no -wal/-shm companions are needed to read the copy.
                writer.execute("PRAGMA journal_mode = DELETE")
                result = [row[0] for row in writer.execute("PRAGMA integrity_check")]
            finally:
                writer.close()
        finally:
            reader.close()
        if result != ["ok"]:
            raise BackupError("Integrity check failed on the copy: " + "; ".join(map(str, result[:5])))
        if destination.exists():
            raise BackupError(f"Refusing to overwrite an existing file: {destination}")
        partial.rename(destination)
    except sqlite3.Error as exc:
        _remove_sqlite_files(partial)
        raise BackupError(f"SQLite backup failed: {exc}") from exc
    except BaseException:
        _remove_sqlite_files(partial)
        raise
    return destination


def prune_manual_backups(output_dir: Path, keep: int, *, protect: Path | None = None) -> list[Path]:
    """Delete the oldest quote-helper-v2-manual-*.sqlite3 files beyond ``keep``; return them."""

    if keep < 1:
        raise BackupError("--keep must be at least 1.")
    output_dir = Path(output_dir)
    backups = sorted(
        (path for path in output_dir.glob(f"{MANUAL_PREFIX}*{SUFFIX}") if path.is_file()),
        key=lambda path: path.name,
    )
    protected = Path(protect).resolve() if protect else None
    removed = []
    for path in backups[: max(0, len(backups) - keep)]:
        if protected is not None and path.resolve() == protected:
            continue
        path.unlink()
        removed.append(path)
    return removed


def _console_safe() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _console_safe()
    parser = argparse.ArgumentParser(description="Verified online backup of the Quote Helper V2 database.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Project folder (default: this checkout).")
    parser.add_argument("--database", type=Path,
                        help="Database to copy (default: <root>/data/quote_helper_v2.sqlite3).")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--output-dir", type=Path, help="Folder for a timestamped copy (default: <root>/data/backups).")
    target.add_argument("--output", type=Path, help="Exact file to create instead of a timestamped name.")
    parser.add_argument("--keep", type=int, help="Keep only the newest N manual backups in the output folder.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    database = args.database or root / DATABASE_RELATIVE
    output_dir = None if args.output else (args.output_dir or root / BACKUP_RELATIVE)
    try:
        if args.keep is not None and args.keep < 1:
            raise BackupError("--keep must be at least 1.")
        if args.keep is not None and args.output:
            raise BackupError("--keep applies to timestamped backups; it cannot be combined with --output.")
        created = backup_database(database, output_dir=output_dir, output=args.output)
        if args.keep is not None:
            for path in prune_manual_backups(created.parent, args.keep, protect=created):
                print(f"Removed old manual backup: {path}", file=sys.stderr)
    except (BackupError, OSError) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1
    print(created)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
