"""Shared test helpers: the configured competitors, discovered test domains, and review
databases in the version-1 and (legacy) version-2 layouts.

The separate UK eligibility gate was retired (Option B, 19 Nov 2026). `make_v2_db` builds
a file exactly as the retired gate's schema left it (including UK rows), so tests can prove
those files still open and that their UK tables are never read, written or dropped.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from app.services import review_store

CONFIGURED_13 = (
    "andertons.co.uk", "guitarguitar.co.uk", "langleyguitarcentre.co.uk", "themusicdept.com", "gear4music.com",
    "thomann.co.uk", "bax-shop.co.uk", "projectmusic.co.uk", "the58sound.com", "perfectpitch.co.uk",
    "southlondonmusicshop.co.uk", "yamahamusiclondon.com", "gibson.com",
)  # fmt: skip

# Discovered domains used by the discovery/review test fixtures.
DISCOVERED_TEST_DOMAINS = ("beckettsmusic.co.uk", "ebay.co.uk", "lookalike-music.co.uk", "guitarworld.com",
                           "youtube.com", "idealo.co.uk", "shop.co.uk", "someshop.co.uk")  # fmt: skip

# The retired gate's version-2 tables, verbatim from checkpoint a6d5a15.
LEGACY_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS uk_eligibility (
    domain TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('UK_ELIGIBLE', 'INSUFFICIENT_EVIDENCE', 'NOT_UK_ELIGIBLE')),
    version INTEGER NOT NULL CHECK (version >= 1),
    record TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uk_eligibility_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    version INTEGER NOT NULL,
    record TEXT NOT NULL,
    UNIQUE (domain, version)
);
CREATE TABLE IF NOT EXISTS uk_gate_exceptions (
    domain TEXT PRIMARY KEY,
    allow_search INTEGER NOT NULL CHECK (allow_search IN (0, 1)),
    allow_benchmark INTEGER NOT NULL CHECK (allow_benchmark IN (0, 1)),
    version INTEGER NOT NULL CHECK (version >= 1),
    record TEXT NOT NULL,
    CHECK (allow_search = 1 OR allow_benchmark = 0)
);
CREATE TABLE IF NOT EXISTS uk_gate_exception_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    version INTEGER NOT NULL,
    record TEXT NOT NULL,
    UNIQUE (domain, version)
);
CREATE TABLE IF NOT EXISTS uk_eligibility_proposals (
    domain TEXT PRIMARY KEY,
    record TEXT NOT NULL
);
"""


def make_v1_db(path: Path) -> Path:
    """An empty database exactly as the version-1 code created it."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(review_store._SCHEMA)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    return path


def make_v2_db(path: Path) -> Path:
    """A database as the retired UK gate left it: version 2, the five UK tables, and UK rows
    (a NOT_UK_ELIGIBLE record and an exception) that must never influence anything."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(review_store._SCHEMA + LEGACY_V2_SCHEMA)
        conn.execute("INSERT INTO uk_eligibility (domain, status, version, record) VALUES (?, ?, ?, ?)",
                     ("andertons.co.uk", "NOT_UK_ELIGIBLE", 1, '{"domain": "andertons.co.uk"}'))  # fmt: skip
        conn.execute("INSERT INTO uk_eligibility_history (domain, version, record) VALUES (?, ?, ?)",
                     ("andertons.co.uk", 1, '{"domain": "andertons.co.uk"}'))  # fmt: skip
        conn.execute("INSERT INTO uk_gate_exceptions (domain, allow_search, allow_benchmark, version, record) "
                     "VALUES (?, ?, ?, ?, ?)", ("gibson.com", 1, 1, 1, '{"domain": "gibson.com"}'))  # fmt: skip
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
    return path


def table_snapshot(path: Path, tables=review_store.LEGACY_UK_TABLES) -> dict[str, list]:
    """Every row of `tables`, read-only, for before/after comparisons."""
    with closing(sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)) as conn:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
