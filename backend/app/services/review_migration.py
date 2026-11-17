"""Explicit review database migration: schema version 1 -> 2 (UK eligibility tables).

    python -m app.services.review_migration --db data/review/competitor_review.sqlite3            # dry run
    python -m app.services.review_migration --db data/review/competitor_review.sqlite3 --apply    # migrate

Dry run is the default and opens the database read-only. `--apply`:
1. takes an IMMEDIATE write lock, then a timestamped backup with the SQLite
   backup API, and verifies it (integrity, version 1, identical rows);
2. creates the five UK tables (empty) and sets user_version = 2, in the same
   transaction;
3. checks after commit that the three existing tables are byte-identical to the
   backup and the new tables are empty.
Any failure before commit rolls everything back. A database already at version
2 is left untouched. No row is rewritten; no UK record, exception or proposal is
written (seeding is a separate, explicit command).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from app.core.config import REPO_ROOT
from app.services import sqlite_backup
from app.services.review_store import _SCHEMA_V2, SCHEMA_VERSION, UK_TABLES, V1_TABLES
from app.services.sqlite_backup import BackupError

DEFAULT_BACKUP_DIR = REPO_ROOT / "data" / "review" / "backups"


class MigrationStatus(StrEnum):
    WOULD_MIGRATE = "WOULD_MIGRATE"  # dry run
    MIGRATED = "MIGRATED"
    ALREADY_CURRENT = "ALREADY_CURRENT"


class MigrationError(RuntimeError):
    """The migration was refused or rolled back; the database is unchanged."""


class MigrationReport(BaseModel):
    db_path: str
    status: MigrationStatus
    from_version: int
    to_version: int
    row_counts: dict[str, int]
    tables_to_create: list[str]
    backup_path: str | None = None
    db_sha256_before: str | None = None
    problems: list[str] = []


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inspect(db_path: Path) -> tuple[int, dict[str, int], list[str]]:
    """(schema version, rows per existing table, existing table names). Read only."""
    with closing(sqlite_backup.connect_read_only(db_path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = sqlite_backup.user_tables(conn)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    return version, counts, tables


def migrate(db_path: Path, *, apply: bool = False, backup_dir: Path = DEFAULT_BACKUP_DIR,
            clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> MigrationReport:  # fmt: skip
    path = Path(db_path).resolve()
    version, counts, tables = inspect(path)
    report = MigrationReport(db_path=str(path), status=MigrationStatus.WOULD_MIGRATE, from_version=version,
                             to_version=SCHEMA_VERSION, row_counts=counts,
                             tables_to_create=[t for t in UK_TABLES if t not in tables])  # fmt: skip
    if version == SCHEMA_VERSION:
        report.status, report.tables_to_create = MigrationStatus.ALREADY_CURRENT, []
        return report
    if version != 1:
        raise MigrationError(f"Expected schema version 1, found {version}; refusing to migrate")
    if missing := [t for t in V1_TABLES if t not in tables]:
        raise MigrationError(f"Version-1 tables missing: {missing}; refusing to migrate")
    if present := [t for t in UK_TABLES if t in tables]:
        raise MigrationError(f"UK tables already exist in a version-1 database: {present}; refusing to migrate")
    if not apply:
        return report
    report.db_sha256_before = _sha256(path)
    with closing(sqlite3.connect(sqlite_backup.uri(path, "rw"), uri=True, timeout=10, isolation_level=None)) as conn:
        conn.execute("BEGIN IMMEDIATE")  # nothing else can write until COMMIT/ROLLBACK
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise MigrationError("Schema version changed before the write lock was taken")
            start = sqlite_backup.table_rows(conn, V1_TABLES)
            backup = sqlite_backup.make_backup(path, backup_dir, clock(), "schema-v2-migration")
            report.backup_path = str(backup)
            sqlite_backup.verify_backup(backup, start, version=1)
            for stmt in (s.strip() for s in _SCHEMA_V2.split(";")):
                if stmt:
                    conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            if sqlite_backup.table_rows(conn, V1_TABLES) != start:
                raise MigrationError("Existing rows changed during the migration")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    report.status = MigrationStatus.MIGRATED
    report.problems = verify_migrated(path, backup)
    return report


def verify_migrated(db_path: Path, backup: Path) -> list[str]:
    """After commit: version 2, intact, existing tables identical to the backup, UK tables empty."""
    problems = []
    with closing(sqlite_backup.connect_read_only(db_path)) as a, closing(sqlite_backup.connect_read_only(backup)) as b:
        if (check := a.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
            problems.append(f"integrity_check: {check}")
        if (v := a.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION:
            problems.append(f"user_version is {v}")
        if sqlite_backup.table_rows(a, V1_TABLES) != sqlite_backup.table_rows(b, V1_TABLES):
            problems.append("existing tables differ from the backup")
        for t in UK_TABLES:
            if a.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]:
                problems.append(f"{t} is not empty")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="migrate (default: dry run, read only)")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    args = parser.parse_args(argv)
    if not args.db.is_file():
        print(f"Review database not found: {args.db}", file=sys.stderr)
        return 2
    try:
        report = migrate(args.db, apply=args.apply, backup_dir=args.backup_dir)
    except (MigrationError, BackupError, sqlite3.Error) as e:
        print(f"Migration refused or rolled back; database unchanged: {e}", file=sys.stderr)
        return 4
    print(report.model_dump_json(indent=1))
    return 5 if report.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
