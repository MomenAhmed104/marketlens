"""Verified SQLite backups for one-off maintenance writes (backfill, migration, seeding)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime
from pathlib import Path


class BackupError(RuntimeError):
    """The backup could not be made or verified; nothing must be written."""


def uri(db_path: Path, mode: str) -> str:
    return f"{Path(db_path).resolve().as_uri()}?mode={mode}"


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    """A connection that cannot write, and never creates the file if it is missing."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Review database not found: {path}")
    return sqlite3.connect(uri(path, "ro"), uri=True, timeout=10)


def user_tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                       "AND name NOT LIKE 'sqlite_%' ORDER BY name")]  # fmt: skip


def table_rows(conn: sqlite3.Connection, tables: Iterable[str] | None = None) -> dict[str, list[tuple]]:
    """Every row of each table, in a stable order (all user tables when `tables` is None)."""
    names = list(tables) if tables is not None else user_tables(conn)
    return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in names}


def make_backup(db_path: Path, backup_dir: Path, now: datetime, label: str) -> Path:
    """Copy the database with the SQLite backup API (a consistent snapshot). Never overwrites."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"{Path(db_path).stem}.pre-{label}-{now.strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
    if path.exists():
        raise BackupError(f"Backup file already exists: {path}")
    with closing(connect_read_only(db_path)) as src, closing(sqlite3.connect(path)) as dst:
        src.backup(dst)
    return path


def verify_backup(backup: Path, expected: dict[str, list[tuple]], version: int | None = None) -> None:
    """The backup is readable, intact, at `version` (if given) and holds exactly `expected` rows."""
    if not backup.is_file() or backup.stat().st_size == 0:
        raise BackupError(f"Backup missing or empty: {backup}")
    try:
        with closing(sqlite3.connect(uri(backup, "ro"), uri=True)) as conn:
            if (check := conn.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
                raise BackupError(f"Backup integrity check failed: {check}")
            if version is not None and conn.execute("PRAGMA user_version").fetchone()[0] != version:
                raise BackupError("Backup has the wrong schema version")
            if table_rows(conn, expected) != expected:
                raise BackupError("Backup contents differ from the database")
    except sqlite3.Error as e:
        raise BackupError(f"Backup is not readable: {e}") from e
