"""Persistent competitor review store (Phase 4): SQLite via the standard library.

Three tables:
- `review_candidates`: one row per discovered domain with the latest evidence
  snapshot (merged across runs), so pending domains survive restarts and every
  decision stays traceable to its evidence. Full discovery run results are
  NOT stored here; they remain temporary.
- `domain_decisions`: the current decision per domain. The domain is the
  primary key, so duplicate records are impossible.
- `decision_history`: append-only; every version of every decision.

Schema version 2 adds UK eligibility (separate from approval, class and benchmark):
- `uk_eligibility` / `uk_eligibility_history`: the reviewer's UK evidence status
  per domain (configured or discovered), versioned, append-only history.
- `uk_gate_exceptions` / `uk_gate_exception_history`: explicit user-approved
  search/benchmark exceptions, versioned, append-only history.
- `uk_eligibility_proposals`: audit suggestions; never used by the gate.

A new (empty) database is created at version 2. An existing version-1 database
is opened without any write: UK reads return nothing (every domain is then
INSUFFICIENT_EVIDENCE and blocked) and UK writes raise MigrationRequiredError
until `python -m app.services.review_migration --apply` has migrated it.

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
from app.schemas.uk_eligibility import UkEligibilityProposal, UkEligibilityRecord, UkGateException
from app.services.domains import registrable_domain
from app.services.uk_gate import UkGateState

SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)
V1_TABLES = ("review_candidates", "domain_decisions", "decision_history")
UK_TABLES = ("uk_eligibility", "uk_eligibility_history", "uk_gate_exceptions", "uk_gate_exception_history",
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

# Version 2 additions. Applied to new databases directly and by the explicit 1 -> 2 migration.
_SCHEMA_V2 = """
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


class MigrationRequiredError(RuntimeError):
    """The database is at schema version 1; UK eligibility needs the explicit 1 -> 2 migration."""


class VersionConflictError(Exception):
    """The stored record changed since the caller read it (expected_version mismatch)."""

    def __init__(self, domain: str, current: int | None):
        super().__init__(f"{domain}: current version is {current}")
        self.domain, self.current = domain, current


CLASS_FIELDS = ("proposed_competitor_class", "competitor_class", "competitor_class_reasons",
                "classified_at", "classification_model")  # fmt: skip


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
    A new completed LLM classification replaces both."""
    merged = new.model_copy(deep=True)
    seen = {(e.canonical_url, e.product_id, e.kind) for e in new.evidence}
    extra = [e for e in old.evidence if (e.canonical_url, e.product_id, e.kind) not in seen]
    merged.evidence = (new.evidence + extra)[:MAX_EVIDENCE_PER_CANDIDATE]
    merged.hostnames = sorted(set(old.hostnames) | set(new.hostnames))
    merged.products_matched = sorted(set(old.products_matched) | set(new.products_matched))
    merged.uk_signals = sorted(set(old.uk_signals) | set(new.uk_signals))
    if (old.classification_status is ClassificationStatus.COMPLETED
            and new.classification_status is not ClassificationStatus.COMPLETED):  # fmt: skip
        for field in ("classification_status", "company_name", "company_type", "relevant_categories", "uk_seller",
                      "sells_new_musical_instruments", "relevance", "confidence", "rationale",
                      "cited_evidence_urls", "name_verified", "validation_notes", *CLASS_FIELDS):  # fmt: skip
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
                # New database: every table at the current version. executescript commits on
                # its own; every statement is idempotent.
                conn.executescript(_SCHEMA + _SCHEMA_V2)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.commit()
                current = SCHEMA_VERSION
            # An existing database is never written here: no silent migration.
        self.schema_version = current

    @property
    def uk_supported(self) -> bool:
        return self.schema_version >= 2

    def _require_uk(self) -> None:
        if not self.uk_supported:
            raise MigrationRequiredError(
                f"Review database {self.path.name} is at schema version {self.schema_version}; UK eligibility "
                "needs the explicit migration (python -m app.services.review_migration --apply)")  # fmt: skip

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

    # ------------------------------------------------------------------ UK eligibility (schema v2)

    def uk_gate_state(self) -> UkGateState:
        """A consistent snapshot of UK records and exceptions. Empty (all blocked) on a version-1 database."""
        if not self.uk_supported:
            return UkGateState.empty()
        with self._read() as conn:
            records = [UkEligibilityRecord.model_validate_json(r[0])
                       for r in conn.execute("SELECT record FROM uk_eligibility ORDER BY domain")]  # fmt: skip
            exceptions = [UkGateException.model_validate_json(r[0])
                          for r in conn.execute("SELECT record FROM uk_gate_exceptions ORDER BY domain")]  # fmt: skip
        return UkGateState.of(records, exceptions)

    def get_uk_eligibility(self, domain: str) -> UkEligibilityRecord | None:
        return self._get_uk("uk_eligibility", UkEligibilityRecord, domain)

    def list_uk_eligibility(self) -> list[UkEligibilityRecord]:
        return list(self.uk_gate_state().eligibility.values())

    def uk_eligibility_history(self, domain: str) -> list[UkEligibilityRecord]:
        return self._history("uk_eligibility_history", UkEligibilityRecord, domain)

    def put_uk_eligibility(self, record: UkEligibilityRecord,
                           expected_version: int | None = None) -> UkEligibilityRecord:  # fmt: skip
        """Create (version 1) or update (version + 1) a domain's UK record; history is append-only.
        `expected_version` is the version the caller saw (0 = none yet); a mismatch raises VersionConflictError."""
        return self._put("uk_eligibility", "uk_eligibility_history", record, expected_version,
                         {"status": record.status.value})  # fmt: skip

    def get_uk_exception(self, domain: str) -> UkGateException | None:
        return self._get_uk("uk_gate_exceptions", UkGateException, domain)

    def list_uk_exceptions(self) -> list[UkGateException]:
        return list(self.uk_gate_state().exceptions.values())

    def uk_exception_history(self, domain: str) -> list[UkGateException]:
        return self._history("uk_gate_exception_history", UkGateException, domain)

    def put_uk_exception(self, record: UkGateException, expected_version: int | None = None) -> UkGateException:
        return self._put("uk_gate_exceptions", "uk_gate_exception_history", record, expected_version,
                         {"allow_search": int(record.allow_search), "allow_benchmark": int(record.allow_benchmark)})

    def list_uk_proposals(self) -> list[UkEligibilityProposal]:
        if not self.uk_supported:
            return []
        with self._read() as conn:
            rows = conn.execute("SELECT record FROM uk_eligibility_proposals ORDER BY domain").fetchall()
        return [UkEligibilityProposal.model_validate_json(r[0]) for r in rows]

    def get_uk_proposal(self, domain: str) -> UkEligibilityProposal | None:
        return self._get_uk("uk_eligibility_proposals", UkEligibilityProposal, domain)

    def put_uk_proposals(self, proposals: list[UkEligibilityProposal]) -> int:
        """Store audit proposals (replacing earlier proposals for the same domains). Never a decision."""
        self._require_uk()
        with self._tx() as conn:
            for p in proposals:
                conn.execute("INSERT INTO uk_eligibility_proposals (domain, record) VALUES (?, ?) "
                             "ON CONFLICT(domain) DO UPDATE SET record = excluded.record",
                             (p.domain, p.model_dump_json()))  # fmt: skip
        return len(proposals)

    def _get_uk(self, table: str, model: type[BaseModel], domain: str):
        if not self.uk_supported:
            return None
        with self._read() as conn:
            row = conn.execute(f"SELECT record FROM {table} WHERE domain = ?", (domain,)).fetchone()
        return model.model_validate_json(row[0]) if row else None

    def _history(self, table: str, model: type[BaseModel], domain: str) -> list:
        if not self.uk_supported:
            return []
        with self._read() as conn:
            rows = conn.execute(f"SELECT record FROM {table} WHERE domain = ? ORDER BY version", (domain,)).fetchall()
        return [model.model_validate_json(r[0]) for r in rows]

    def _put(self, table: str, history: str, record, expected_version: int | None, columns: dict):
        self._require_uk()
        with self._tx() as conn:
            row = conn.execute(f"SELECT version FROM {table} WHERE domain = ?", (record.domain,)).fetchone()
            current = row[0] if row else None
            if expected_version is not None and expected_version != (current or 0):
                raise VersionConflictError(record.domain, current)
            record = record.model_copy(update={"version": (current or 0) + 1})
            cols = {**columns, "version": record.version, "record": record.model_dump_json()}
            updates = ", ".join(f"{c} = excluded.{c}" for c in cols)
            conn.execute(f"INSERT INTO {table} (domain, {', '.join(cols)}) VALUES (?{', ?' * len(cols)}) "
                         f"ON CONFLICT(domain) DO UPDATE SET {updates}", (record.domain, *cols.values()))  # fmt: skip
            conn.execute(f"INSERT INTO {history} (domain, version, record) VALUES (?, ?, ?)",
                         (record.domain, record.version, record.model_dump_json()))  # fmt: skip
        return record
