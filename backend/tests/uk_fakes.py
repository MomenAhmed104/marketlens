"""UK eligibility test helpers: records, exceptions, gate states and version-1 databases."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.schemas.uk_eligibility import (
    UkConfidence,
    UkEligibilityRecord,
    UkEligibilityStatus,
    UkEvidence,
    UkEvidenceKind,
    UkEvidenceSource,
    UkGateException,
)
from app.services import review_store
from app.services.uk_gate import UkGateState, gate_decision

S = UkEligibilityStatus
AT = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)

CONFIGURED_13 = (
    "andertons.co.uk", "guitarguitar.co.uk", "langleyguitarcentre.co.uk", "themusicdept.com", "gear4music.com",
    "thomann.co.uk", "bax-shop.co.uk", "projectmusic.co.uk", "the58sound.com", "perfectpitch.co.uk",
    "southlondonmusicshop.co.uk", "yamahamusiclondon.com", "gibson.com",
)  # fmt: skip


def strong(url="https://example.co.uk/visit-us", kind=UkEvidenceKind.PHYSICAL_STORE) -> UkEvidence:
    return UkEvidence(url=url, quote="12 High Street, London, SW1A 1AA", kind=kind,
                      source=UkEvidenceSource.REVIEWER_SUPPLIED, note="Read by the reviewer in a test")  # fmt: skip


def uk_record(domain: str, status: S = S.UK_ELIGIBLE, **kw) -> UkEligibilityRecord:
    default = {S.UK_ELIGIBLE: [strong(f"https://{domain}/visit-us")],
               S.NOT_UK_ELIGIBLE: [strong(f"https://{domain}/imprint", UkEvidenceKind.NON_UK_OPERATOR)],
               S.INSUFFICIENT_EVIDENCE: []}[status]  # fmt: skip
    evidence = kw.pop("evidence", default)
    base = dict(domain=domain, status=status, confidence=UkConfidence.HIGH, reasoning="test reasoning",
                evidence=evidence, reviewer="tester", decided_at=AT)  # fmt: skip
    return UkEligibilityRecord(**{**base, **kw})


def uk_exception(domain: str, search=True, benchmark=False, **kw) -> UkGateException:
    base = dict(domain=domain, allow_search=search, allow_benchmark=benchmark, reason="explicit test approval",
                reviewer="tester", decided_at=AT)  # fmt: skip
    return UkGateException(**{**base, **kw})


def uk_state(eligible: Iterable[str] = (), *, records: Iterable[UkEligibilityRecord] = (),
             exceptions: Iterable[UkGateException] = ()) -> UkGateState:  # fmt: skip
    return UkGateState.of([*(uk_record(d) for d in eligible), *records], exceptions)


# Discovered domains used by the discovery/review test fixtures.
DISCOVERED_TEST_DOMAINS = ("beckettsmusic.co.uk", "ebay.co.uk", "lookalike-music.co.uk", "guitarworld.com",
                           "youtube.com", "idealo.co.uk", "shop.co.uk", "someshop.co.uk")  # fmt: skip


def all_eligible(extra: Iterable[str] = ()) -> UkGateState:
    """Every configured competitor (and `extra`) explicitly assessed UK_ELIGIBLE (test seeding)."""
    return uk_state([*CONFIGURED_13, *DISCOVERED_TEST_DOMAINS, *extra])


def seed_store(store, eligible: Iterable[str] = (), exceptions: Iterable[UkGateException] = ()) -> None:
    for d in eligible:
        store.put_uk_eligibility(uk_record(d))
    for e in exceptions:
        store.put_uk_exception(e)


def gate(domain: str, state: UkGateState | None = None):
    return gate_decision(domain, state or uk_state([domain]))


def make_v1_db(path: Path) -> Path:
    """An empty database exactly as the version-1 code created it."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(review_store._SCHEMA)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    return path
