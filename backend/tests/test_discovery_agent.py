"""End-to-end discovery with fake search + fake LLM (no network, no keys)."""

import asyncio
from decimal import Decimal

import pytest

from app.agents.discovery.agent import CompetitorDiscoveryAgent
from app.agents.discovery.schemas import (
    ClassificationBatch,
    CompanyClassification,
    DiscoveryPlan,
    EvidenceKind,
    ProductQueryPlan,
    QuerySource,
    UkSeller,
)
from app.schemas.common import DomainApprovalStatus as S
from app.schemas.competitor import CompanyType as T
from app.schemas.search import SearchScope
from app.services.search.base import RawHit
from tests.discovery_fakes import FakeLLM, FakeSearch, llm_failure
from tests.search_fakes import make_settings
from tests.uk_fakes import all_eligible

GLOBAL_HITS = {
    "SB-001": [
        RawHit("https://www.stompbox.co.uk/product/boss-rc-5/", "Boss RC-5 - Stompbox", "£189.99"),
        RawHit("https://www.andertons.co.uk/boss-rc-5-loop-station-pedal/", "Boss RC-5 Loop Station Pedal", "£219"),
        RawHit("https://blog.andertons.co.uk/learn/loopers", "Best loopers", "RC-5 vs RC-500"),
        RawHit("https://beckettsmusic.co.uk/products/boss-rc-5", "Boss RC-5 Loop Station - Beckett's Music", "£189.00 in stock"),
        RawHit("https://beckettsmusic.co.uk/products/boss-rc-1", "Boss RC-1 - Beckett's Music", "£99"),
        RawHit("https://www.ebay.co.uk/itm/123", "Boss RC-5 Loop Station | eBay", "£150 used"),
        RawHit("https://www.youtube.com/watch?v=x", "Boss RC-5 review", "video"),
        RawHit("https://www.guitarworld.com/reviews/boss-rc-5", "Boss RC-5 review | Guitar World", "review"),
        RawHit("https://lookalike-music.co.uk/boss-rc-5", "Boss RC-5", "£180"),
    ],
    "SB-028": [
        RawHit("https://beckettsmusic.co.uk/products/epiphone-les-paul-60s-lh", "Epiphone Les Paul Standard 60s LH - Beckett's Music", "£599"),
        RawHit("https://www.gibson.com/en-gb/products/epiphone-les-paul-standard-60s-lh", "Epiphone LP Std 60s LH", "£599"),
    ],
}  # fmt: skip
PROFILE_HITS = {
    "beckettsmusic.co.uk": [RawHit("https://beckettsmusic.co.uk/pages/about", "About Beckett's Music",
                                   "Independent music shop in Bedford, MK40 1AB. UK delivery.")],
}  # fmt: skip

PLAN = DiscoveryPlan(products=[
    ProductQueryPlan(product_id="SB-001", global_queries=["Boss RC-5 loop pedal UK shop", "Boss RC-5 site:ebay.co.uk",
                                                          "Boss RC-5 in stock UK", "boss rc-5 loop pedal uk shop"],
                     targeted_query_variants=["Boss RC-5 Loop Station", "Boss looper pedal"]),
    ProductQueryPlan(product_id="SB-028", global_queries=["Epiphone Les Paul Standard 60s left handed UK"],
                     targeted_query_variants=["Epiphone Les Paul Standard '60s LH Iced Tea"]),
    ProductQueryPlan(product_id="SB-999", global_queries=["ghost"], targeted_query_variants=[]),
])  # fmt: skip


def c(domain, name, ctype, rel, conf, urls, uk=UkSeller.YES):
    return CompanyClassification(domain=domain, company_name=name, company_type=ctype, relevant_categories=["Effects", "Guitars"],
                                 uk_seller=uk, sells_new_musical_instruments=True, relevance=rel, confidence=conf,
                                 rationale="From evidence.", evidence_urls=urls)  # fmt: skip


CLASSIFICATIONS = ClassificationBatch(companies=[
    c("beckettsmusic.co.uk", "Beckett's Music", T.SPECIALIST_RETAILER, 0.9, 0.92,
      ["https://beckettsmusic.co.uk/products/boss-rc-5", "https://beckettsmusic.co.uk/pages/about"]),
    c("ebay.co.uk", "eBay", T.MARKETPLACE, 0.6, 0.9, ["https://www.ebay.co.uk/itm/123"]),
    c("guitarworld.com", "Guitar World", T.OTHER, 0.1, 0.9, ["https://www.guitarworld.com/reviews/boss-rc-5"],
      uk=UkSeller.UNKNOWN),
    # Name not present anywhere in the evidence: must not be auto-approved.
    c("lookalike-music.co.uk", "Lookalike Music Ltd", T.RETAILER, 0.9, 0.95, ["https://lookalike-music.co.uk/boss-rc-5"]),
    c("never-given.example", "Ghost", T.RETAILER, 0.9, 0.9, []),
])  # fmt: skip


def make_agent(catalogue, plan=PLAN, classifications=CLASSIFICATIONS, fail_texts=frozenset(), **settings):
    search = FakeSearch(GLOBAL_HITS, PROFILE_HITS, fail_texts)
    llm = FakeLLM(plan, classifications)
    s = make_settings(**settings)
    agent = CompetitorDiscoveryAgent(search, llm, s, catalogue.competitors,
                                     allowed_categories=[p.category for p in catalogue.products.values()],
                                     uk_state=all_eligible())  # fmt: skip (explicit UK seeding)
    return agent, search, llm


def run_agent(catalogue, ids=("SB-001", "SB-028"), **kw):
    agent, search, llm = make_agent(catalogue, **kw)
    products = [catalogue.products[i] for i in ids]
    return asyncio.run(agent.run(products)), search, llm


@pytest.fixture(scope="module")
def outcome(catalogue):
    return run_agent(catalogue)


def by_domain(result):
    return {c.domain: c for c in result.candidates}


def test_known_competitors_loaded_and_gibson_is_reference(outcome):
    result, _, _ = outcome
    assert len(result.known_competitors) == 13
    gibson = next(k for k in result.known_competitors if k.domain == "gibson.com")
    assert gibson.company_type is T.MANUFACTURER and gibson.price_benchmark is False
    assert all(not q.price_benchmark for q in result.targeted_queries if q.domain == "gibson.com")
    assert any(q.domain == "gibson.com" for q in result.targeted_queries)  # still searched, as a reference
    assert all(q.price_benchmark for q in result.targeted_queries if q.domain == "andertons.co.uk")


def test_dedup_against_known_competitors_own_site_and_platforms(outcome):
    result, _, _ = outcome
    cands = by_domain(result)
    assert "andertons.co.uk" not in cands and "gibson.com" not in cands  # incl. blog. subdomain and /en-gb/
    known = {k.domain: k for k in result.known_competitor_evidence}
    assert known["andertons.co.uk"].products_seen == ["SB-001"]
    assert {e.hostname for e in known["andertons.co.uk"].evidence} == {"andertons.co.uk", "blog.andertons.co.uk"}
    excluded = {x.domain: x.reason for x in result.excluded}
    assert excluded["stompbox.co.uk"] == "Own site (Stompbox)"
    assert "excluded platform" in excluded["youtube.com"]


def test_decisions(outcome):
    result, _, _ = outcome
    cands = by_domain(result)
    beck = cands["beckettsmusic.co.uk"]
    assert beck.approval_status is S.AUTO_APPROVED and beck.price_benchmark is True
    assert beck.name_verified and beck.company_name == "Beckett's Music"
    assert beck.products_matched == ["SB-001", "SB-028"]
    assert set(beck.uk_signals) >= {"uk_domain", "gbp_price", "uk_postcode"}
    assert cands["ebay.co.uk"].approval_status is S.APPROVAL_REQUIRED  # marketplace: always a person's call
    assert cands["ebay.co.uk"].price_benchmark is False
    assert cands["guitarworld.com"].approval_status is S.REJECTED
    look = cands["lookalike-music.co.uk"]
    assert look.approval_status is S.APPROVAL_REQUIRED and not look.name_verified
    assert any("not verified" in r for r in look.decision_reasons)
    # Ordering: auto-approved first, rejected last
    assert [x.approval_status for x in result.candidates][0] is S.AUTO_APPROVED
    assert result.candidates[-1].approval_status is S.REJECTED


def test_evidence_and_metadata_preserved(outcome):
    result, _, _ = outcome
    beck = by_domain(result)["beckettsmusic.co.uk"]
    first = next(e for e in beck.evidence if e.url.endswith("boss-rc-5"))
    assert first.provider == "fake-exa" and first.provider_request_id.startswith("req-")
    assert first.retrieved_at.year == 2026 and first.query_text and first.product_id == "SB-001"
    assert first.mentions_product_model and first.mentions_brand
    assert any(e.kind is EvidenceKind.DOMAIN_PROFILE for e in beck.evidence)
    assert beck.cited_evidence_urls == ["https://beckettsmusic.co.uk/products/boss-rc-5",
                                        "https://beckettsmusic.co.uk/pages/about"]  # fmt: skip
    # Same page found by several queries is kept once per product.
    assert len({(e.canonical_url, e.product_id) for e in beck.evidence}) == len(beck.evidence)


def test_targeted_queries_only_for_approved_domains(outcome):
    result, _, _ = outcome
    domains = {q.domain for q in result.targeted_queries}
    assert "beckettsmusic.co.uk" in domains
    assert not domains & {"ebay.co.uk", "lookalike-music.co.uk", "guitarworld.com"}
    assert len(domains) == 14  # 13 preconfigured + 1 auto-approved
    assert all(q.query.scope is SearchScope.DOMAIN and q.query.domains == (q.domain,) for q in result.targeted_queries)
    rc5 = [q.query.text for q in result.targeted_queries if q.domain == "andertons.co.uk" and q.product_id == "SB-001"]
    assert rc5 == ["Boss RC-5", "Boss RC-5 Loop Station"]  # baseline + valid variant; "Boss looper pedal" dropped


def test_plan_validation(outcome):
    result, search, _ = outcome
    texts = [q.query.text for q in result.global_queries if q.product_id == "SB-001"]
    assert texts[0] == "Boss RC-5 buy UK" and result.global_queries[0].source is QuerySource.BASELINE
    assert not any("site:" in t for t in texts)
    assert texts.count("Boss RC-5 loop pedal UK shop") == 1  # case-insensitive duplicate removed
    assert all(q.query.scope is SearchScope.GLOBAL and q.query.country == "GB" for q in result.global_queries)
    assert any("unknown product ids" in w for w in result.warnings)
    assert any("dropped targeted variant without the model" in w for w in result.warnings)
    assert any("not given" in w for w in result.warnings)


def test_usage_and_prompt_safety(outcome):
    result, search, llm = outcome
    u = result.usage
    assert u.search_requests == len(search.queries) and u.search_errors == 0
    assert u.search_cost_usd == Decimal("0.007") * len(search.queries)
    assert u.llm_calls == len(llm.calls) == 2 and u.llm_cost_usd == Decimal("0.004")
    assert u.llm_model == "fake/llm-1@2026"
    classify_prompt = llm.calls[1]["user"]
    assert "<untrusted_evidence>" in classify_prompt and "stompbox.co.uk" not in classify_prompt
    assert result.thresholds.calibrated is False


def test_llm_failures_degrade_to_manual_review(catalogue):
    result, search, _ = run_agent(catalogue, ids=("SB-001",), plan=llm_failure(), classifications=llm_failure())
    assert [q.source for q in result.global_queries] == [QuerySource.BASELINE]
    assert all(c.approval_status in (S.APPROVAL_REQUIRED,) for c in result.candidates)
    assert {q.domain for q in result.targeted_queries} == {k.domain for k in result.known_competitors}
    assert any("Planning LLM call failed" in w for w in result.warnings)
    assert any("Classification failed" in w for w in result.warnings)


def test_search_failures_are_recorded(catalogue):
    result, _, _ = run_agent(catalogue, ids=("SB-001",), fail_texts={"Boss RC-5 buy UK"})
    assert result.usage.search_errors == 1
    assert any("simulated outage" in w for w in result.warnings)
    assert "beckettsmusic.co.uk" in by_domain(result)  # other queries still produced evidence


def test_candidate_limit(catalogue):
    result, _, _ = run_agent(catalogue, ids=("SB-001",), discovery_max_candidates=1)
    assert [c.domain for c in result.candidates] == ["beckettsmusic.co.uk"]  # most product evidence ranks first
    assert any(x.reason.startswith("Not evaluated") for x in result.excluded)


def test_requires_products(catalogue):
    agent, _, _ = make_agent(catalogue)
    with pytest.raises(ValueError):
        asyncio.run(agent.run([]))


def test_failed_llm_calls_still_count_spent_tokens_and_cost(catalogue):
    from decimal import Decimal

    from app.services.llm import LLMOutputError, LLMUsage

    def truncated():
        return LLMOutputError("openrouter", "Output budget used up before any answer",
                              usage=LLMUsage(3000, 12000, Decimal("0.006"), 12000), request_ids=["gen-t"])  # fmt: skip

    result, _, _ = run_agent(catalogue, ids=("SB-001",), plan=truncated(), classifications=truncated())
    assert any("Planning LLM call failed" in w for w in result.warnings)
    assert result.usage.llm_calls >= 2 and result.usage.llm_completion_tokens >= 24000
    assert result.usage.llm_cost_usd >= Decimal("0.012")
