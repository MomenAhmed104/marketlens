"""Discovery agent + persisted reviewer decisions (Phase 4): search eligibility."""

import asyncio
from datetime import UTC, datetime

from app.agents.discovery.agent import CompetitorDiscoveryAgent
from app.agents.discovery.rules import is_known_marketplace
from app.agents.discovery.schemas import ClassificationBatch
from app.schemas.common import DomainApprovalStatus as S
from app.schemas.competitor import CompanyType as T
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from tests.discovery_fakes import FakeLLM, FakeSearch
from tests.search_fakes import make_settings
from tests.test_discovery_agent import CLASSIFICATIONS, GLOBAL_HITS, PLAN, PROFILE_HITS, c
from tests.uk_fakes import all_eligible

NOW = datetime(2026, 11, 12, tzinfo=UTC)


def dec(domain, decision=ReviewDecision.APPROVED, ctype=T.RETAILER, bench=False) -> DomainDecisionRecord:
    return DomainDecisionRecord(domain=domain, decision=decision, company_type=ctype, benchmark_eligible=bench,
                                reviewer="Alex", decided_at=NOW)  # fmt: skip


def run(catalogue, decisions, llm=None, ids=("SB-001", "SB-028"), **settings):
    agent = CompetitorDiscoveryAgent(
        FakeSearch(GLOBAL_HITS, PROFILE_HITS), llm, make_settings(**settings), catalogue.competitors,
        [p.category for p in catalogue.products.values()], llm_disabled_reason=None if llm else "test",
        review_decisions=decisions, uk_state=all_eligible(),  # explicit UK seeding
    )  # fmt: skip
    return asyncio.run(agent.run([catalogue.products[i] for i in ids]))


def targeted_domains(result):
    return {q.domain for q in result.targeted_queries}


def test_without_decisions_only_the_13_known_competitors_are_searched(catalogue):
    result = run(catalogue, [])
    known = {k.domain for k in catalogue.competitors}
    assert len(known) == 13
    assert targeted_domains(result) == known == {t.domain for t in result.search_targets}


def test_pending_and_rejected_domains_never_enter_targeted_searches(catalogue):
    result = run(catalogue, [dec("guitarworld.com", ReviewDecision.REJECTED, T.OTHER)])
    cands = {x.domain: x for x in result.candidates}
    assert cands["guitarworld.com"].approval_status is S.REJECTED
    assert cands["beckettsmusic.co.uk"].approval_status is S.APPROVAL_REQUIRED
    assert not targeted_domains(result) & {"guitarworld.com", "beckettsmusic.co.uk", "ebay.co.uk"}


def test_rejection_overrides_llm_auto_approval(catalogue):
    llm_only = run(catalogue, [], llm=FakeLLM(PLAN, CLASSIFICATIONS))
    assert {x.domain: x for x in llm_only.candidates}["beckettsmusic.co.uk"].approval_status is S.AUTO_APPROVED

    result = run(catalogue, [dec("beckettsmusic.co.uk", ReviewDecision.REJECTED)], llm=FakeLLM(PLAN, CLASSIFICATIONS))
    b = {x.domain: x for x in result.candidates}["beckettsmusic.co.uk"]
    assert b.approval_status is S.REJECTED and b.automatic_approval_status is S.AUTO_APPROVED
    assert b.price_benchmark is False and "beckettsmusic.co.uk" not in targeted_domains(result)


def test_approved_domains_are_searched_with_their_reviewed_benchmark_flag(catalogue):
    result = run(catalogue, [dec("beckettsmusic.co.uk", ctype=T.SPECIALIST_RETAILER, bench=True),
                             dec("ebay.co.uk", ctype=T.MARKETPLACE),
                             dec("lookalike-music.co.uk", ctype=None)])  # fmt: skip
    targets = {t.domain: t for t in result.search_targets}
    assert targets["beckettsmusic.co.uk"].price_benchmark is True
    assert targets["beckettsmusic.co.uk"].approval_status is S.APPROVED
    assert targets["ebay.co.uk"].price_benchmark is False and targets["ebay.co.uk"].company_type is T.MARKETPLACE
    # Approved with type kept unknown: searched, type stays None (never coerced to retailer), no benchmark.
    assert targets["lookalike-music.co.uk"].company_type is None
    assert targets["lookalike-music.co.uk"].price_benchmark is False
    q = [q for q in result.targeted_queries if q.domain == "lookalike-music.co.uk"]
    assert q and all(x.company_type is None and not x.price_benchmark for x in q)
    assert all(x.query.domains == ("lookalike-music.co.uk",) for x in q)


def test_approved_domain_is_searched_even_when_not_rediscovered(catalogue):
    result = run(catalogue, [dec("someshop.co.uk", ctype=T.RETAILER, bench=True)])
    assert "someshop.co.uk" not in {x.domain for x in result.candidates}
    assert "someshop.co.uk" in targeted_domains(result)


def test_decisions_on_preconfigured_competitors_are_ignored(catalogue):
    result = run(catalogue, [dec("gibson.com", ReviewDecision.REJECTED, T.MANUFACTURER),
                             dec("andertons.co.uk", ReviewDecision.REJECTED)])  # fmt: skip
    targets = {t.domain: t for t in result.search_targets}
    assert targets["gibson.com"].approval_status is S.PRECONFIGURED and targets["gibson.com"].price_benchmark is False
    assert targets["andertons.co.uk"].approval_status is S.PRECONFIGURED and targets["andertons.co.uk"].price_benchmark


def test_known_marketplace_is_never_auto_approved_even_if_llm_says_retailer(catalogue):
    as_retailer = ClassificationBatch(companies=[
        c("ebay.co.uk", "eBay", T.RETAILER, 0.95, 0.95, ["https://www.ebay.co.uk/itm/123"])])  # fmt: skip
    result = run(catalogue, [], llm=FakeLLM(PLAN, as_retailer), ids=("SB-001",),
                 discovery_auto_approve_min_evidence_urls=1)  # fmt: skip
    ebay = {x.domain: x for x in result.candidates}["ebay.co.uk"]
    assert ebay.approval_status is S.APPROVAL_REQUIRED and ebay.price_benchmark is False
    assert any("known marketplace" in r for r in ebay.decision_reasons)
    assert "ebay.co.uk" not in targeted_domains(result)


def test_marketplace_labels():
    labels = make_settings().marketplace_domain_labels
    assert all(is_known_marketplace(d, labels) for d in ("amazon.co.uk", "amazon.com", "ebay.co.uk", "reverb.com"))
    assert not is_known_marketplace("andertons.co.uk", labels)
