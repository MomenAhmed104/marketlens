"""Trusted competitor records for matching: never take them from a run payload or file.

A Phase 5 run result (an API body or a saved file) can be edited, so each candidate's
`competitor` (benchmark flag, approval and company type) is rebuilt from trusted
server-side data: the catalogue's configured competitors (workbook flags) and the
reviewer's decisions. A domain that is not a current search target, or whose page URL is
on another domain, can never be a price benchmark.

Used by `POST /api/matching/evaluate` and the matching CLI.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from app.agents.discovery.schemas import SearchTarget
from app.agents.intelligence import eligible_search_targets
from app.agents.intelligence.schemas import IntelligenceRunResult
from app.core.config import Settings
from app.schemas.competitor import CompetitorDomain
from app.schemas.review import DomainDecisionRecord
from app.services import sqlite_backup
from app.services.domains import registrable_domain
from app.services.review_store import SUPPORTED_SCHEMA_VERSIONS, ReviewStore


class TrustedStateUnavailable(RuntimeError):
    """The review database (reviewer decisions) could not be read safely."""


def _domain(value: str) -> str | None:
    try:
        return registrable_domain(value)
    except ValueError:
        return None


def trusted_competitors(run: IntelligenceRunResult, competitors: list[CompetitorDomain],
                        decisions: list[DomainDecisionRecord], settings: Settings, *,
                        benchmarks_allowed: bool = True) -> tuple[IntelligenceRunResult, int]:  # fmt: skip
    """The run with every candidate's competitor replaced from trusted state. Returns (run, replaced).

    `benchmarks_allowed=False` (the trusted review data could not be read) fails closed:
    no candidate can be a price benchmark, configured competitors included."""
    targets, _ = eligible_search_targets(competitors, decisions, settings)
    if not benchmarks_allowed:
        targets = [t.model_copy(update={"price_benchmark": False}) for t in targets]
    by_domain = {t.domain: t for t in targets}
    replaced = 0
    products = []
    for pr in run.products:
        candidates = []
        for c in pr.candidates:
            claimed = _domain(c.competitor.domain)
            page = _domain(urlsplit(c.product_url).hostname or "")
            server = by_domain.get(claimed) if claimed and claimed == page else None
            if server is None:  # not a searchable target now, or the page is elsewhere: never a benchmark
                server = SearchTarget(domain=claimed or c.competitor.domain, company_name=None, company_type=None,
                                      approval_status=c.competitor.approval_status, price_benchmark=False)  # fmt: skip
            if server != c.competitor:
                replaced += 1
            candidates.append(c.model_copy(update={"competitor": server}))
        products.append(pr.model_copy(update={"candidates": candidates}))
    return run.model_copy(update={"products": products}), replaced


REPLACED_WARNING = ("{n} candidate competitor record(s) differed from server state and were replaced: "
                    "benchmark eligibility is always recomputed server-side")  # fmt: skip


def load_review_state(db_path: Path) -> list[DomainDecisionRecord]:
    """Reviewer decisions from an existing review database, without writing it.

    The schema version is checked on a read-only connection first, so `ReviewStore`
    (which would initialise a brand-new or version-0 file) never writes. Raises
    TrustedStateUnavailable if the file is missing, unreadable, at an unsupported
    version, or holds records that fail validation."""
    path = Path(db_path)
    if not path.is_file():
        raise TrustedStateUnavailable(f"review database not found: {path}")
    try:
        with closing(sqlite_backup.connect_read_only(path)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise TrustedStateUnavailable(f"review database {path.name} has unsupported schema version {version}")
        store = ReviewStore(path)
        return store.list_decisions()
    except TrustedStateUnavailable:
        raise
    except (sqlite3.Error, ValidationError, ValueError, OSError) as e:
        raise TrustedStateUnavailable(f"review database {path.name} could not be read: "
                                      f"{type(e).__name__}") from None  # fmt: skip
