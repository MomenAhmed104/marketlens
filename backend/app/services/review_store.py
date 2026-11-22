"""Persistent competitor review store (Phase 4): SQLite via the standard library.

Three tables:
- `review_candidates`: one row per discovered domain with the latest evidence
  snapshot (merged across runs), so pending domains survive restarts and every
  decision stays traceable to its evidence. Full discovery run results are
  NOT stored here; they remain temporary.
- `domain_decisions`: the current decision per domain. The domain is the
  primary key, so duplicate records are impossible.
- `decision_history`: append-only; every version of every decision.

Schema versions. Version 1 is exactly the three tables above. Version 2 (checkpoint
a6d5a15) added five tables for a separate UK eligibility gate, which has been retired
(Option B, 19 Nov 2026): UK relevance is now verified evidence on each candidate record.
Both versions are opened read/write for the three core tables, and a version-2 file's
UK tables are left exactly as they are: never read, written or dropped. No file's
version is ever changed here, and nothing is migrated. A brand-new (empty) database is
created at version 1 with only the three core tables.

Every row is stored as validated Pydantic JSON and re-validated on read.
Each operation opens its own connection, so the store is safe to use from
FastAPI worker threads. Writes run in an IMMEDIATE transaction.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from app.agents.discovery.schemas import (
    ClassificationStatus,
    DiscoveredCompetitor,
    DiscoveryMode,
    DiscoveryRunResult,
)
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.domains import registrable_domain

# New databases are created at version 1 (the three core tables only). Version 2 files
# from the retired UK gate stay supported; their extra tables are ignored.
SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = (1, 2)
CORE_TABLES = ("review_candidates", "domain_decisions", "decision_history")
# Tables of the retired UK eligibility gate (schema version 2). Never read, written or dropped.
LEGACY_UK_TABLES = ("uk_eligibility", "uk_eligibility_history", "uk_gate_exceptions", "uk_gate_exception_history",
                    "uk_eligibility_proposals")  # fmt: skip
MAX_EVIDENCE_PER_CANDIDATE = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_candidates (
    domain TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_run_id TEXT NOT NULL,
    times_seen INTEGER NOT NULL CHECK (times_seen >= 1),
    record TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS domain_decisions (
    domain TEXT PRIMARY KEY,
    decision TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED')),
    version INTEGER NOT NULL CHECK (version >= 1),
    record TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    version INTEGER NOT NULL,
    record TEXT NOT NULL,
    UNIQUE (domain, version)
);
"""

class StoredCandidate(BaseModel):
    """A discovered domain awaiting or past review, with its merged evidence."""

    domain: str
    first_seen_at: datetime
    last_seen_at: datetime
    last_run_id: str
    times_seen: int
    # Mode of the run that supplied the current classification fields.
    discovery_mode: DiscoveryMode
    candidate: DiscoveredCompetitor


class DecisionConflictError(Exception):
    """A decision already exists; updating it must be explicit."""


class DecisionNotFoundError(Exception):
    pass


CLASS_FIELDS = ("proposed_competitor_class", "competitor_class", "competitor_class_reasons",
                "classified_at", "classification_model")  # fmt: skip
# Everything a completed LLM 1 classification sets; kept together by `merge_candidate`.
LLM_FIELDS = ("classification_status", "classification_version", "company_name", "company_type",
              "relevant_categories", "serves_uk_customers", "serves_uk_customers_note",
              "sells_new_musical_instruments", "relevance", "confidence", "rationale", "cited_evidence_urls",
              "name_verified", "validation_notes", *CLASS_FIELDS)  # fmt: skip


def has_llm_competitor_class(c: DiscoveredCompetitor) -> bool:
    """True if the record holds a competitor class backed by an LLM 1 proposal.

    That covers classes from LLM-enabled discovery runs and from the classification
    backfill (which writes only the class fields, so `classification_status` can
    still say SKIPPED_LLM_DISABLED). A class derived without LLM 1 (no proposal) is not.
    """
    return c.competitor_class is not None and c.proposed_competitor_class is not None and c.classified_at is not None


def merge_candidate(old: DiscoveredCompetitor, new: DiscoveredCompetitor) -> DiscoveredCompetitor:
    """Newest run wins, except that:
    - evidence accumulates;
    - a completed LLM classification is never replaced by an unclassified one
      (no-LLM, failed or missing): all of its LLM fields are kept;
    - an LLM-backed competitor class (e.g. backfilled) is never replaced by one from
      a run without a completed LLM classification: the five class fields are kept
      together.
    A new completed LLM classification replaces both. The classification version travels
    with the LLM fields, so a kept pre-Stage 8.1 classification never becomes eligible for
    automatic search (`rules.is_search_eligible`); a new one at the current version does.
    Fields of the retired UK evidence system (`uk_assessment`, `uk_signals`) are not part of
    the model: they load from old rows without error and are not written again."""
    merged = new.model_copy(deep=True)
    seen = {(e.canonical_url, e.product_id, e.kind) for e in new.evidence}
    extra = [e for e in old.evidence if (e.canonical_url, e.product_id, e.kind) not in seen]
    merged.evidence = (new.evidence + extra)[:MAX_EVIDENCE_PER_CANDIDATE]
    merged.hostnames = sorted(set(old.hostnames) | set(new.hostnames))
    merged.products_matched = sorted(set(old.products_matched) | set(new.products_matched))
    if (old.classification_status is ClassificationStatus.COMPLETED
            and new.classification_status is not ClassificationStatus.COMPLETED):  # fmt: skip
        for field in LLM_FIELDS:
            setattr(merged, field, getattr(old, field))
    elif new.classification_status is not ClassificationStatus.COMPLETED and has_llm_competitor_class(old):
        for field in CLASS_FIELDS:
            setattr(merged, field, getattr(old, field))
    merged.discovered_at = min(old.discovered_at, new.discovered_at)
    return merged


class ReviewStore:
    def __init__(self, path: Path, excluded_domains: tuple[str, ...] = ()):
        """`excluded_domains`: competitors removed by the user (e.g. PMT); never written to the queue."""
        self.path = Path(path)
        self.excluded_domains = frozenset(registrable_domain(d) for d in excluded_domains)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            if current not in (0, *SUPPORTED_SCHEMA_VERSIONS):
                raise RuntimeError(f"Review database {self.path} has unsupported schema version {current}")
            if current == 0:
                # New database: the three core tables at version 1. executescript commits on
                # its own; every statement is idempotent.
                conn.executescript(_SCHEMA)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.commit()
                current = SCHEMA_VERSION
            # An existing database (version 1 or 2) is never written here: no migration.
        self.schema_version = current

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            yield conn

    # ------------------------------------------------------------------ candidates

    def record_run(self, result: DiscoveryRunResult) -> int:
        """Add or refresh the run's candidates. Returns how many were written.

        Excluded domains are skipped (defence in depth: discovery already excludes them).
        """
        written = [c for c in result.candidates if registrable_domain(c.domain) not in self.excluded_domains]
        with self._tx() as conn:
            for c in written:
                row = conn.execute("SELECT record FROM review_candidates WHERE domain = ?", (c.domain,)).fetchone()
                # Never persist run-scoped review state: decisions live in their own table.
                fresh = c.model_copy(update={"review_decision": None})
                if row:
                    old = StoredCandidate.model_validate_json(row[0])
                    keep_mode = (old.candidate.classification_status is ClassificationStatus.COMPLETED
                                 and c.classification_status is not ClassificationStatus.COMPLETED)  # fmt: skip
                    stored = StoredCandidate(
                        domain=c.domain, first_seen_at=old.first_seen_at, last_seen_at=result.finished_at,
                        last_run_id=result.run_id, times_seen=old.times_seen + 1,
                        discovery_mode=old.discovery_mode if keep_mode else result.mode,
                        candidate=merge_candidate(old.candidate, fresh),
                    )  # fmt: skip
                else:
                    stored = StoredCandidate(domain=c.domain, first_seen_at=result.finished_at,
                                             last_seen_at=result.finished_at, last_run_id=result.run_id,
                                             times_seen=1, discovery_mode=result.mode, candidate=fresh)  # fmt: skip
                conn.execute(
                    "INSERT INTO review_candidates (domain, first_seen_at, last_seen_at, last_run_id, times_seen, record)"
                    " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(domain) DO UPDATE SET last_seen_at = excluded.last_seen_at,"
                    " last_run_id = excluded.last_run_id, times_seen = excluded.times_seen, record = excluded.record",
                    (stored.domain, stored.first_seen_at.isoformat(), stored.last_seen_at.isoformat(),
                     stored.last_run_id, stored.times_seen, stored.model_dump_json()),
                )  # fmt: skip
        return len(written)

    def get_candidate(self, domain: str) -> StoredCandidate | None:
        with self._read() as conn:
            row = conn.execute("SELECT record FROM review_candidates WHERE domain = ?", (domain,)).fetchone()
        return StoredCandidate.model_validate_json(row[0]) if row else None

    def list_candidates(self) -> list[StoredCandidate]:
        with self._read() as conn:
            rows = conn.execute("SELECT record FROM review_candidates ORDER BY domain").fetchall()
        return [StoredCandidate.model_validate_json(r[0]) for r in rows]

    # ------------------------------------------------------------------ decisions

    def get_decision(self, domain: str) -> DomainDecisionRecord | None:
        with self._read() as conn:
            row = conn.execute("SELECT record FROM domain_decisions WHERE domain = ?", (domain,)).fetchone()
        return DomainDecisionRecord.model_validate_json(row[0]) if row else None

    def list_decisions(self, decision: ReviewDecision | None = None) -> list[DomainDecisionRecord]:
        sql, args = "SELECT record FROM domain_decisions", ()
        if decision is not None:
            sql, args = sql + " WHERE decision = ?", (decision.value,)
        with self._read() as conn:
            rows = conn.execute(sql + " ORDER BY domain", args).fetchall()
        return [DomainDecisionRecord.model_validate_json(r[0]) for r in rows]

    def history(self, domain: str) -> list[DomainDecisionRecord]:
        """All versions, oldest first."""
        with self._read() as conn:
            rows = conn.execute("SELECT record FROM decision_history WHERE domain = ? ORDER BY version",
                                (domain,)).fetchall()  # fmt: skip
        return [DomainDecisionRecord.model_validate_json(r[0]) for r in rows]

    def create_decision(self, record: DomainDecisionRecord) -> DomainDecisionRecord:
        """First decision for a domain. Raises DecisionConflictError if one exists."""
        record = record.model_copy(update={"version": 1})
        with self._tx() as conn:
            try:
                conn.execute("INSERT INTO domain_decisions (domain, decision, version, record) VALUES (?, ?, ?, ?)",
                             (record.domain, record.decision.value, 1, record.model_dump_json()))  # fmt: skip
            except sqlite3.IntegrityError:
                raise DecisionConflictError(record.domain) from None
            self._append_history(conn, record)
        return record

    def update_decision(self, record: DomainDecisionRecord) -> DomainDecisionRecord:
        """Replace the current decision (explicit update). Previous versions stay in history."""
        with self._tx() as conn:
            row = conn.execute("SELECT version FROM domain_decisions WHERE domain = ?", (record.domain,)).fetchone()
            if row is None:
                raise DecisionNotFoundError(record.domain)
            record = record.model_copy(update={"version": row[0] + 1})
            conn.execute("UPDATE domain_decisions SET decision = ?, version = ?, record = ? WHERE domain = ?",
                         (record.decision.value, record.version, record.model_dump_json(), record.domain))  # fmt: skip
            self._append_history(conn, record)
        return record

    @staticmethod
    def _append_history(conn: sqlite3.Connection, record: DomainDecisionRecord) -> None:
        conn.execute("INSERT INTO decision_history (domain, version, record) VALUES (?, ?, ?)",
                     (record.domain, record.version, record.model_dump_json()))  # fmt: skip
