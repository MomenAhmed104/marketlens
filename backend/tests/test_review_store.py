"""Persistent review store (Phase 4): SQLite, no network."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.agents.discovery.schemas import (
    ClassificationStatus,
    DiscoveredCompetitor,
    DiscoveryMode,
    DiscoveryRunResult,
    DiscoveryThresholds,
    DiscoveryUsage,
    EvidenceItem,
    EvidenceKind,
)
from app.schemas.competitor import CompanyType as T
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.review_store import DecisionConflictError, DecisionNotFoundError, ReviewStore

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


def evidence(url: str, product="SB-001") -> EvidenceItem:
    return EvidenceItem(url=url, canonical_url=url, hostname="shop.example.co.uk", title="Boss RC-5", snippet="£189",
                        kind=EvidenceKind.DISCOVERY_RESULT, query_text="Boss RC-5 buy UK", product_id=product,
                        provider="exa", provider_request_id="req-1", retrieved_at=NOW)  # fmt: skip


def candidate(domain="example.co.uk", urls=("https://example.co.uk/a",), **kw) -> DiscoveredCompetitor:
    return DiscoveredCompetitor(domain=domain, hostnames=[domain], homepage_url=f"https://{domain}/",
                                evidence=[evidence(u) for u in urls], products_matched=["SB-001"],
                                uk_signals=["uk_domain"], discovered_at=NOW, **kw)  # fmt: skip


def run(cands, run_id="run-1", mode=DiscoveryMode.LLM_DISABLED, at=NOW) -> DiscoveryRunResult:
    return DiscoveryRunResult(
        run_id=run_id, mode=mode, started_at=at, finished_at=at, product_ids=["SB-001"], search_provider="exa",
        known_competitors=[], known_competitor_evidence=[], global_queries=[], candidates=cands, excluded=[],
        targeted_queries=[], usage=DiscoveryUsage(), warnings=[],
        thresholds=DiscoveryThresholds(auto_approve_min_confidence=0.85, auto_approve_min_relevance=0.75,
                                       auto_approve_min_evidence_urls=2, reject_max_relevance=0.3),
    )  # fmt: skip


def decision(domain="example.co.uk", **kw) -> DomainDecisionRecord:
    values = dict(domain=domain, decision=ReviewDecision.APPROVED, company_type=T.RETAILER, reviewer="alex",
                  decided_at=NOW, evidence_urls=["https://example.co.uk/a"])  # fmt: skip
    values.update(kw)
    return DomainDecisionRecord(**values)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "review.sqlite3"


def test_decisions_and_candidates_survive_a_restart(db):
    store = ReviewStore(db)
    store.record_run(run([candidate()]))
    store.create_decision(decision(benchmark_eligible=True, notes="UK shop"))
    del store

    reopened = ReviewStore(db)  # e.g. backend restart
    d = reopened.get_decision("example.co.uk")
    assert d.decision is ReviewDecision.APPROVED and d.benchmark_eligible and d.reviewer == "alex"
    assert d.notes == "UK shop" and d.decided_at == NOW and d.evidence_urls == ["https://example.co.uk/a"]
    assert [c.domain for c in reopened.list_candidates()] == ["example.co.uk"]
    assert reopened.get_candidate("example.co.uk").candidate.evidence[0].url == "https://example.co.uk/a"


def test_duplicate_decision_is_a_conflict_and_update_is_versioned(db):
    store = ReviewStore(db)
    store.create_decision(decision())
    with pytest.raises(DecisionConflictError):
        store.create_decision(decision(decision=ReviewDecision.REJECTED, company_type=None))
    assert store.get_decision("example.co.uk").decision is ReviewDecision.APPROVED  # unchanged

    updated = store.update_decision(decision(decision=ReviewDecision.REJECTED, reviewer="sam", notes="Used only"))
    assert updated.version == 2 and store.get_decision("example.co.uk").decision is ReviewDecision.REJECTED
    hist = store.history("example.co.uk")
    assert [(h.version, h.decision, h.reviewer) for h in hist] == [
        (1, ReviewDecision.APPROVED, "alex"), (2, ReviewDecision.REJECTED, "sam")]
    assert len(store.list_decisions()) == 1  # one record per domain
    assert store.list_decisions(ReviewDecision.APPROVED) == []


def test_update_without_existing_decision_fails(db):
    with pytest.raises(DecisionNotFoundError):
        ReviewStore(db).update_decision(decision())


def test_record_run_merges_evidence_and_keeps_completed_classification(db):
    store = ReviewStore(db)
    classified = candidate(urls=("https://example.co.uk/a",), classification_status=ClassificationStatus.COMPLETED,
                           company_name="Example Music", company_type=T.SPECIALIST_RETAILER, confidence=0.9,
                           relevance=0.8)  # fmt: skip
    store.record_run(run([classified], "run-1", DiscoveryMode.LLM_ENABLED))
    later = NOW + timedelta(hours=1)
    unclassified = candidate(urls=("https://example.co.uk/b",),
                             classification_status=ClassificationStatus.SKIPPED_LLM_DISABLED)  # fmt: skip
    store.record_run(run([unclassified], "run-2", DiscoveryMode.LLM_DISABLED, at=later))

    s = store.get_candidate("example.co.uk")
    assert s.times_seen == 2 and s.last_run_id == "run-2" and s.first_seen_at == NOW and s.last_seen_at == later
    assert {e.url for e in s.candidate.evidence} == {"https://example.co.uk/a", "https://example.co.uk/b"}
    # A no-LLM run never wipes an earlier evidence-based classification...
    assert s.candidate.classification_status is ClassificationStatus.COMPLETED
    assert s.candidate.company_type is T.SPECIALIST_RETAILER and s.candidate.confidence == 0.9
    assert s.discovery_mode is DiscoveryMode.LLM_ENABLED


def test_unclassified_candidate_stays_unclassified(db):
    store = ReviewStore(db)
    store.record_run(run([candidate(classification_status=ClassificationStatus.SKIPPED_LLM_DISABLED)]))
    c = store.get_candidate("example.co.uk").candidate
    assert c.company_type is None and c.confidence is None and c.relevance is None and c.company_name is None


def test_run_scoped_review_state_is_not_persisted_in_the_candidate(db):
    store = ReviewStore(db)
    store.record_run(run([candidate(review_decision=decision())]))
    assert store.get_candidate("example.co.uk").candidate.review_decision is None


@pytest.mark.parametrize("ctype", [None, T.MARKETPLACE, T.DIRECTORY, T.OTHER])
def test_only_benchmark_types_can_be_benchmark_eligible(ctype):
    with pytest.raises(ValidationError, match="benchmark_eligible not allowed"):
        decision(company_type=ctype, benchmark_eligible=True)
    assert decision(company_type=ctype).benchmark_eligible is False


def test_rejected_cannot_be_benchmark_eligible():
    with pytest.raises(ValidationError, match="rejected"):
        decision(decision=ReviewDecision.REJECTED, benchmark_eligible=True)


@pytest.mark.parametrize("ctype", [T.RETAILER, T.SPECIALIST_RETAILER, T.MANUFACTURER])
def test_retailers_and_manufacturers_can_be_benchmark_eligible(ctype):
    assert decision(company_type=ctype, benchmark_eligible=True).benchmark_eligible
