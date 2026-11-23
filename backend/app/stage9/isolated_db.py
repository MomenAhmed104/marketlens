"""Isolated review databases for Stage 9 evaluation runs.

Evaluation runs write discovered candidates and reviewer decisions to the review database. They
must never touch the real (`data/review/competitor_review.sqlite3`) or demo databases, so every
Stage 9 run uses its own file:

  - `create_copy(source, dest)`: an exact copy (schema, version and rows) made with SQLite's
    backup API from a READ-ONLY connection to the source; the source is never opened for writing;
  - `create_empty(dest)`: a fresh database with the current schema (ReviewStore creates it);
  - `apply_decisions(dest, approvals)`: reviewer decisions written to the copy only;
  - `reset(dest, source=None)`: delete and recreate (copy again, or empty) between scenarios.

Guards: the destination must not be the source, must not be inside a protected directory
(default: data/review/, where the real and demo databases live) and is never overwritten except
by `reset`. The harness passes the destination as REVIEW_DB_PATH, so the pipeline, the review
API and the target selection all use it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.config import REPO_ROOT
from app.schemas.competitor import CompanyType
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.review_store import ReviewStore

PROTECTED_DIRS: tuple[Path, ...] = (REPO_ROOT / "data" / "review",)


class IsolationError(Exception):
    pass


class Approval(BaseModel):
    """A reviewer decision to apply to an evaluation database before a run."""

    domain: str
    decision: ReviewDecision = ReviewDecision.APPROVED
    company_name: str | None = None
    company_type: CompanyType | None = None
    benchmark_eligible: bool = False
    reviewer: str = Field(default="stage9-evaluation", min_length=1)
    notes: str | None = None


def _resolved(p: Path) -> Path:
    return Path(p).expanduser().resolve()


def check_destination(dest: Path, *, source: Path | None = None, protected: Iterable[Path] = PROTECTED_DIRS) -> Path:
    d = _resolved(dest)
    if source is not None and d == _resolved(source):
        raise IsolationError("The evaluation database cannot be the source database")
    for root in protected:
        r = _resolved(root)
        if d == r or r in d.parents:
            raise IsolationError(f"{d} is inside a protected directory ({r}); use an evaluation folder instead")
    return d


def create_copy(source: Path, dest: Path, *, protected: Iterable[Path] = PROTECTED_DIRS) -> Path:
    """Copy `source` to a new file `dest`, reading the source through a read-only connection."""
    src = _resolved(source)
    if not src.is_file():
        raise IsolationError(f"Source database {src} does not exist")
    d = check_destination(dest, source=src, protected=protected)
    if d.exists():
        raise IsolationError(f"{d} already exists; use reset() to recreate it")
    d.parent.mkdir(parents=True, exist_ok=True)
    reader = sqlite3.connect(f"{src.as_uri()}?mode=ro", uri=True)
    try:
        writer = sqlite3.connect(d)
        try:
            reader.backup(writer)
        finally:
            writer.close()
    finally:
        reader.close()
    return d


def create_empty(dest: Path, excluded_domains: tuple[str, ...] = (), *,
                 protected: Iterable[Path] = PROTECTED_DIRS) -> Path:  # fmt: skip
    d = check_destination(dest, protected=protected)
    if d.exists():
        raise IsolationError(f"{d} already exists; use reset() to recreate it")
    d.parent.mkdir(parents=True, exist_ok=True)
    ReviewStore(d, excluded_domains)  # creates the current schema
    return d


def apply_decisions(dest: Path, approvals: Iterable[Approval], excluded_domains: tuple[str, ...] = (), *,
                    protected: Iterable[Path] = PROTECTED_DIRS) -> list[DomainDecisionRecord]:  # fmt: skip
    d = check_destination(dest, protected=protected)
    if not d.is_file():
        raise IsolationError(f"{d} does not exist; create it first")
    store = ReviewStore(d, excluded_domains)
    existing = {x.domain: x for x in store.list_decisions()}
    out = []
    for a in approvals:
        if (have := existing.get(a.domain)) is not None:
            # Re-applying a scenario's decisions is idempotent; a different decision is an error.
            if (have.decision, have.company_type, have.benchmark_eligible) != (a.decision, a.company_type,
                                                                                a.benchmark_eligible):
                raise IsolationError(f"{a.domain} already has a different decision in {d}; reset the database")
            out.append(have)
            continue
        record = DomainDecisionRecord(domain=a.domain, decision=a.decision, company_name=a.company_name,
                                      company_type=a.company_type, benchmark_eligible=a.benchmark_eligible,
                                      reviewer=a.reviewer, decided_at=datetime.now(UTC), notes=a.notes)  # fmt: skip
        out.append(store.create_decision(record))
    return out


def reset(dest: Path, source: Path | None = None, excluded_domains: tuple[str, ...] = (), *,
          protected: Iterable[Path] = PROTECTED_DIRS) -> Path:  # fmt: skip
    """Delete the evaluation database (if any) and recreate it from `source`, or empty."""
    d = check_destination(dest, source=source, protected=protected)
    for suffix in ("", "-journal", "-wal", "-shm"):
        p = Path(str(d) + suffix)
        if p.exists():
            p.unlink()
    if source is not None:
        return create_copy(source, d, protected=protected)
    return create_empty(d, excluded_domains, protected=protected)
