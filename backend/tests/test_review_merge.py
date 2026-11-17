"""Merging rediscovered candidates keeps LLM-backed (incl. backfilled) competitor classes. No network."""

from datetime import UTC, datetime, timedelta

import pytest

from app.agents.discovery.rules import apply_classification, classify_competitor
from app.agents.discovery.schemas import ClassificationStatus as CS
from app.agents.discovery.schemas import CompetitorClass, DiscoveredCompetitor
from app.schemas.competitor import CompanyType as T
from app.schemas.review import ReviewDecision
from app.services.review_store import CLASS_FIELDS, ReviewStore, has_llm_competitor_class, merge_candidate
from tests.test_discovery_rules import cls
from tests.test_review_store import candidate, decision, run

D, R, N = CompetitorClass.DIRECT_COMPETITOR, CompetitorClass.RELATED_RETAILER, CompetitorClass.NEEDS_REVIEW
MARKETPLACES = ("amazon", "ebay", "reverb", "etsy", "gumtree")
BACKFILLED_AT = datetime(2026, 11, 15, 22, 33, tzinfo=UTC)
LATER = BACKFILLED_AT + timedelta(days=1)


def backfilled(domain: str, proposed: CompetitorClass, final: CompetitorClass, reasons: list[str]) -> DiscoveredCompetitor:
    """As the backfill left it: only the five class fields set; status still says no LLM."""
    return candidate(domain, urls=(f"https://{domain}/a",), classification_status=CS.SKIPPED_LLM_DISABLED,
                     proposed_competitor_class=proposed, competitor_class=final, competitor_class_reasons=reasons,
                     classified_at=BACKFILLED_AT, classification_model="openai/gpt-6-luna")  # fmt: skip


def rediscovered(domain: str, status: CS = CS.SKIPPED_LLM_DISABLED) -> DiscoveredCompetitor:
    """A later run without a completed LLM classification, with the class the live rules give it."""
    c = candidate(domain, urls=(f"https://{domain}/b",), classification_status=status)
    classify_competitor(c, MARKETPLACES)
    return c


def classes(c: DiscoveredCompetitor) -> dict:
    return {f: getattr(c, f) for f in CLASS_FIELDS}


CASES = {
    "retailer": backfilled("shop.example.co.uk", D, D, ["LLM 1 proposed DIRECT_COMPETITOR (type retailer)",
                                                        "Accepted: DIRECT_COMPETITOR"]),
    "marketplace": backfilled("amazon.co.uk", R, R, ["Known marketplace domain (amazon.co.uk): always RELATED_RETAILER, "
                                                     "overriding the LLM 1 proposal (RELATED_RETAILER)"]),
    "manufacturer": backfilled("nuxaudio.com", N, N, ["LLM 1 proposed NEEDS_REVIEW (type manufacturer)",
                                                      "Accepted: NEEDS_REVIEW"]),
}  # fmt: skip


@pytest.mark.parametrize("kind", CASES)
@pytest.mark.parametrize("status", [CS.SKIPPED_LLM_DISABLED, CS.FAILED, CS.MISSING])
def test_backfilled_class_survives_a_rediscovery_without_llm_classification(kind, status):
    old = CASES[kind]
    new = rediscovered(old.domain, status)
    assert new.competitor_class in (N, R) and new.proposed_competitor_class is None  # what it would have become
    merged = merge_candidate(old, new)
    assert classes(merged) == classes(old)  # all five fields kept together
    # Everything else still follows the newer run, as before.
    assert merged.classification_status is status
    assert {e.url for e in merged.evidence} == {f"https://{old.domain}/a", f"https://{old.domain}/b"}


def test_a_new_successful_llm_classification_replaces_the_old_class():
    old = CASES["retailer"]
    new = candidate(old.domain, urls=(f"https://{old.domain}/a",))
    apply_classification(new, cls(old.domain, company_type=T.MANUFACTURER, competitor_class=D,
                                  evidence_urls=[f"https://{old.domain}/a"]), {"Effects"})  # fmt: skip
    new.classified_at, new.classification_model = LATER, "openai/gpt-6-luna"
    classify_competitor(new, MARKETPLACES)  # manufacturer safeguard: D -> N
    merged = merge_candidate(old, new)
    assert classes(merged) == classes(new)
    assert (merged.proposed_competitor_class, merged.competitor_class, merged.classified_at) == (D, N, LATER)
    assert merged.classification_status is CS.COMPLETED


def test_marketplace_override_still_applies_to_a_new_llm_classification():
    old = CASES["marketplace"]
    new = candidate("amazon.co.uk", urls=("https://amazon.co.uk/a",))
    apply_classification(new, cls("amazon.co.uk", company_type=T.RETAILER, competitor_class=D,
                                  evidence_urls=["https://amazon.co.uk/a"]), {"Effects"})  # fmt: skip
    new.classified_at = LATER
    classify_competitor(new, MARKETPLACES)
    merged = merge_candidate(old, new)
    assert (merged.proposed_competitor_class, merged.competitor_class, merged.classified_at) == (D, R, LATER)


def test_a_record_without_a_previous_llm_class_behaves_as_before():
    # Old record classified only by the no-LLM rules (no proposal): the newer run wins, as before.
    old = rediscovered("old.example.co.uk")
    assert not has_llm_competitor_class(old)
    new = rediscovered("old.example.co.uk", CS.FAILED)
    new.competitor_class_reasons = ["newer reasons"]
    assert classes(merge_candidate(old, new)) == classes(new)
    # Old record from before classification existed (all five fields empty): unchanged behaviour.
    legacy = candidate("legacy.example.co.uk")
    assert legacy.competitor_class is None
    newer = rediscovered("legacy.example.co.uk")
    assert classes(merge_candidate(legacy, newer)) == classes(newer)


def test_completed_llm_classification_still_keeps_all_its_fields():
    old = candidate("done.example.co.uk", urls=("https://done.example.co.uk/a",))
    apply_classification(old, cls("done.example.co.uk", competitor_class=D,
                                  evidence_urls=["https://done.example.co.uk/a"]), {"Effects"})  # fmt: skip
    old.classified_at = BACKFILLED_AT
    classify_competitor(old, MARKETPLACES)
    merged = merge_candidate(old, rediscovered("done.example.co.uk"))
    assert merged.classification_status is CS.COMPLETED and merged.company_type is old.company_type
    assert classes(merged) == classes(old)


def test_class_is_never_inferred_from_scores():
    # High relevance/confidence on a record without an LLM proposal never makes it a kept class.
    old = candidate("scores.example.co.uk", relevance=0.99, confidence=0.99, competitor_class=D)
    assert not has_llm_competitor_class(old)
    new = rediscovered("scores.example.co.uk")
    assert merge_candidate(old, new).competitor_class is N


def test_store_rediscovery_keeps_class_and_leaves_decisions_untouched(tmp_path):
    store = ReviewStore(tmp_path / "review.sqlite3")
    olds = list(CASES.values())
    store.record_run(run(olds, run_id="run-1"))
    store.create_decision(decision("shop.example.co.uk", benchmark_eligible=True))
    store.update_decision(decision("shop.example.co.uk", benchmark_eligible=True, notes="re-checked"))
    store.create_decision(decision("amazon.co.uk", company_type=T.MARKETPLACE))
    store.create_decision(decision("nuxaudio.com", decision=ReviewDecision.REJECTED, company_type=T.MANUFACTURER))
    decisions_before = store.list_decisions()
    history_before = {d: store.history(d) for d in CASES_DOMAINS}

    store.record_run(run([rediscovered(c.domain) for c in olds], run_id="run-2", at=LATER))

    for old in olds:
        s = store.get_candidate(old.domain)
        assert classes(s.candidate) == classes(old)
        assert s.times_seen == 2 and s.last_run_id == "run-2"  # the merge itself still happened
    assert store.list_decisions() == decisions_before
    assert {d: store.history(d) for d in CASES_DOMAINS} == history_before
    assert store.get_decision("shop.example.co.uk").benchmark_eligible is True
    assert store.get_decision("nuxaudio.com").decision is ReviewDecision.REJECTED


CASES_DOMAINS = [c.domain for c in CASES.values()]
