"""Explicit competitor classification (LLM 1 proposal + deterministic safeguards). No network."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.agents.discovery import prompts
from app.agents.discovery.agent import CompetitorDiscoveryAgent
from app.agents.discovery.rules import apply_classification, apply_review_decision, classify_competitor, decide
from app.agents.discovery.schemas import (
    ClassificationBatch,
    ClassificationStatus,
    CompanyClassification,
    CompetitorClass,
    DiscoveredCompetitor,
    DiscoveryMode,
    EvidenceKind,
    UkRelevanceEvidenceKind,
    UkRelevanceQuote,
    UkSeller,
)
from app.main import create_app
from app.schemas.common import DomainApprovalStatus as S
from app.schemas.competitor import CompanyType as T
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.llm import strict_json_schema
from app.services.review_store import ReviewStore
from app.services.search.base import RawHit
from tests.discovery_fakes import FakeLLM, FakeSearch
from tests.search_fakes import make_settings
from tests.test_discovery_agent import BECKETTS_UK, GLOBAL_HITS, PLAN, PROFILE_HITS
from tests.test_discovery_rules import TH, candidate, cls, ev, uk_pages, uk_quotes
from tests.test_review_store import candidate as stored_candidate
from tests.test_review_store import run as run_result

D, R, N = CompetitorClass.DIRECT_COMPETITOR, CompetitorClass.RELATED_RETAILER, CompetitorClass.NEEDS_REVIEW
CATS = {"Effects", "Guitars", "Amplification", "Accessories"}
MARKETPLACES = ("amazon", "ebay", "reverb", "etsy", "gumtree")
NOW = datetime(2026, 11, 15, tzinfo=UTC)


def classified(cand: DiscoveredCompetitor | None = None, **kw) -> DiscoveredCompetitor:
    c = cand or candidate()
    apply_classification(c, cls(domain=c.domain, **kw), CATS)
    classify_competitor(c, MARKETPLACES)
    return c


def downgrade_reasons(c: DiscoveredCompetitor) -> list[str]:
    return [r for r in c.competitor_class_reasons if r.startswith("Downgraded")]


# --------------------------------------------------------------------------- safeguards


def test_valid_direct_competitor_is_accepted():
    c = classified(competitor_class=D)
    assert c.proposed_competitor_class is D and c.competitor_class is D
    assert downgrade_reasons(c) == [] and c.competitor_class_reasons[-1] == "Accepted: DIRECT_COMPETITOR"


def test_related_retailer_is_accepted():
    c = classified(competitor_class=R, company_type=T.RETAILER)
    assert c.competitor_class is R and downgrade_reasons(c) == []


KNOWN_MARKETPLACES = ["amazon.co.uk", "ebay.co.uk", "reverb.com", "etsy.com", "gumtree.com"]


@pytest.mark.parametrize("domain", KNOWN_MARKETPLACES)
@pytest.mark.parametrize("proposed", [D, R, N])
@pytest.mark.parametrize("ctype", [T.MARKETPLACE, T.RETAILER, T.MANUFACTURER, T.OTHER])
def test_known_marketplace_is_always_related_retailer(domain, proposed, ctype):
    c = classified(candidate(domain), competitor_class=proposed, company_type=ctype, uk_seller=UkSeller.UNKNOWN)
    assert c.proposed_competitor_class is proposed and c.competitor_class is R
    assert c.competitor_class_reasons == [
        f"Known marketplace domain ({domain}): always RELATED_RETAILER, overriding the LLM 1 proposal ({proposed.value})"
    ]


def test_amazon_proposed_as_needs_review_becomes_related_retailer():
    c = classified(candidate("amazon.co.uk"), competitor_class=N, company_type=T.MARKETPLACE)
    assert (c.proposed_competitor_class, c.competitor_class) == (N, R)
    assert "overriding the LLM 1 proposal (NEEDS_REVIEW)" in c.competitor_class_reasons[0]


def test_ebay_proposed_as_direct_becomes_related_retailer():
    c = classified(candidate("ebay.co.uk"), competitor_class=D, company_type=T.RETAILER)
    assert (c.proposed_competitor_class, c.competitor_class) == (D, R)
    assert "Known marketplace domain (ebay.co.uk)" in c.competitor_class_reasons[0]


def test_known_marketplace_without_llm_classification_is_related_retailer():
    c = candidate("gumtree.com")
    c.classification_status = ClassificationStatus.SKIPPED_LLM_DISABLED
    classify_competitor(c, MARKETPLACES)
    assert c.competitor_class is R
    assert "overriding the LLM 1 proposal (no proposal (SKIPPED_LLM_DISABLED))" in c.competitor_class_reasons[0]


def test_other_marketplace_type_rules_are_unchanged():
    # Not a known marketplace domain, but LLM 1 typed it marketplace: the existing rules apply.
    c = classified(candidate("musicswap.co.uk"), competitor_class=D, company_type=T.MARKETPLACE)
    assert c.competitor_class is R and "a marketplace can never be a Direct Competitor" in downgrade_reasons(c)[0]
    assert classified(candidate("musicswap.co.uk"), competitor_class=N, company_type=T.MARKETPLACE).competitor_class is N


def test_manufacturer_proposed_as_direct_is_downgraded():
    c = classified(candidate("bossmusic.com"), competitor_class=D, company_type=T.MANUFACTURER)
    assert c.competitor_class is N
    assert "type manufacturer cannot be a Direct Competitor" in downgrade_reasons(c)[0]


@pytest.mark.parametrize("ctype", [T.DIRECTORY, T.OTHER])
@pytest.mark.parametrize("proposed", [D, R])
def test_directory_and_other_types_cannot_be_direct_or_related(ctype, proposed):
    c = classified(competitor_class=proposed, company_type=ctype)
    assert c.competitor_class is N and len(downgrade_reasons(c)) == 1


@pytest.mark.parametrize("uk", [UkSeller.UNKNOWN, UkSeller.NO])
@pytest.mark.parametrize("proposed", [D, R])
def test_unconfirmed_uk_selling_gives_needs_review(uk, proposed):
    # An LLM "no" without a verified no-UK-service quote is reconciled to "unknown" (never trusted).
    c = classified(competitor_class=proposed, uk_seller=uk)
    assert c.competitor_class is N and "UK selling is 'unknown'" in downgrade_reasons(c)[0]


def test_unverified_uk_selling_claim_gives_needs_review():
    # LLM 1 says "yes" but cites no verified UK evidence: uk_seller is unknown, so not Direct.
    c = classified(competitor_class=D, uk_evidence=[])
    assert c.uk_seller is UkSeller.UNKNOWN and c.competitor_class is N


def test_direct_needs_a_strong_uk_signal():
    # .com domain, USD price only: LLM says UK seller, but code finds no strong UK signal.
    # Verified overseas evidence (based abroad, delivers to the UK) so uk_seller is "yes"; still no .uk,
    # GBP or postcode signal. This classification safeguard is unchanged (approval no longer uses it).
    imprint, shipping = "https://loopshop.com/imprint", "https://loopshop.com/shipping"
    cand = candidate("loopshop.com", evidence=[
        ev("https://loopshop.com/boss-rc-5", "Boss RC-5 - Loop Shop", "$199"),
        ev(imprint, "Imprint", "Loop Shop LLC, 100 Main Street, Austin, Texas, USA", kind=EvidenceKind.DOMAIN_PROFILE),
        ev(shipping, "Shipping", "We ship to the United Kingdom with DHL Express", kind=EvidenceKind.DOMAIN_PROFILE)])
    quotes = [UkRelevanceQuote(url=imprint, quote="Loop Shop LLC, 100 Main Street, Austin, Texas, USA",
                               kind=UkRelevanceEvidenceKind.NON_UK_OPERATOR),
              UkRelevanceQuote(url=shipping, quote="We ship to the United Kingdom with DHL Express",
                               kind=UkRelevanceEvidenceKind.UK_DELIVERY)]  # fmt: skip
    c = classified(cand, competitor_class=D, evidence_urls=["https://loopshop.com/boss-rc-5"], uk_evidence=quotes)
    assert c.uk_seller is UkSeller.YES
    assert c.competitor_class is N and "no strong UK signal" in downgrade_reasons(c)[0]


def test_invalid_cited_evidence_gives_needs_review():
    c = classified(competitor_class=D, evidence_urls=["https://elsewhere.example/made-up"])
    assert c.cited_evidence_urls == [] and c.competitor_class is N
    assert "no valid cited evidence URL" in downgrade_reasons(c)[-1]
    assert classified(competitor_class=R, evidence_urls=[]).competitor_class is N


def test_direct_needs_product_match_or_supported_category():
    c = classified(candidate(products=()), competitor_class=D, relevant_categories=["Hi-fi"])  # not in the catalogue
    assert c.relevant_categories == [] and c.competitor_class is N
    assert "no selected product found on the domain and no supported catalogue category" in downgrade_reasons(c)[0]
    # A supported category alone is enough overlap.
    assert classified(candidate(products=()), competitor_class=D).competitor_class is D


@pytest.mark.parametrize("status", [ClassificationStatus.SKIPPED_LLM_DISABLED, ClassificationStatus.FAILED,
                                    ClassificationStatus.MISSING])  # fmt: skip
def test_missing_classification_gives_needs_review(status):
    c = candidate()
    c.classification_status = status
    classify_competitor(c, MARKETPLACES)
    assert c.competitor_class is N and c.proposed_competitor_class is None
    assert status.value in c.competitor_class_reasons[0]


def test_code_never_upgrades_a_needs_review_proposal():
    c = classified(competitor_class=N)  # strong evidence, but LLM 1 was unsure
    assert c.competitor_class is N and downgrade_reasons(c) == []


# --------------------------------------------------------------------------- independence


def test_classification_is_independent_of_approval_and_benchmark():
    domain = "beckettsmusic.co.uk"  # only one discovery URL: auto-approval requires 2
    c = candidate(evidence=[ev(f"https://{domain}/boss-rc-5", "Boss RC-5 Loop Station - Beckett's Music", "£189.99"),
                            *uk_pages(domain)])
    apply_classification(c, cls(competitor_class=D), CATS)
    decide(c, TH, MARKETPLACES)
    before = (c.approval_status, c.price_benchmark, list(c.decision_reasons))
    classify_competitor(c, MARKETPLACES)
    assert c.competitor_class is D and c.approval_status is S.APPROVAL_REQUIRED
    assert (c.approval_status, c.price_benchmark, c.decision_reasons) == before

    rejected = DomainDecisionRecord(domain=c.domain, decision=ReviewDecision.REJECTED, company_type=T.RETAILER,
                                    reviewer="sam", decided_at=NOW, evidence_urls=[])  # fmt: skip
    apply_review_decision(c, rejected)
    assert c.approval_status is S.REJECTED and c.price_benchmark is False
    assert c.competitor_class is D  # a reviewer decision does not rewrite the classification


def test_benchmark_eligibility_does_not_depend_on_class():
    approved = DomainDecisionRecord(domain="x.co.uk", decision=ReviewDecision.APPROVED, company_type=T.RETAILER,
                                    benchmark_eligible=True, reviewer="sam", decided_at=NOW, evidence_urls=[])  # fmt: skip
    c = classified(candidate("x.co.uk"), competitor_class=R)
    apply_review_decision(c, approved)
    assert c.competitor_class is R and c.price_benchmark is True


def test_direct_does_not_use_the_auto_approval_relevance_threshold():
    c = classified(competitor_class=D, relevance=0.4, confidence=0.5)
    assert c.competitor_class is D


# --------------------------------------------------------------------------- LLM contract


def test_strict_schema_requires_competitor_class():
    schema = strict_json_schema(ClassificationBatch)
    item = schema["$defs"]["CompanyClassification"]
    assert "competitor_class" in item["required"]
    assert set(schema["$defs"]["CompetitorClass"]["enum"]) == {"DIRECT_COMPETITOR", "RELATED_RETAILER", "NEEDS_REVIEW"}
    assert all(k in prompts.CLASSIFY_SYSTEM for k in ("DIRECT_COMPETITOR", "RELATED_RETAILER", "NEEDS_REVIEW"))


# --------------------------------------------------------------------------- agent end to end (fake LLM)


def batch(**classes: CompetitorClass) -> ClassificationBatch:
    def item(domain, name, ctype, urls, competitor_class):
        return CompanyClassification(domain=domain, company_name=name, company_type=ctype,
                                     relevant_categories=["Effects"], uk_seller=UkSeller.YES,
                                     sells_new_musical_instruments=True, relevance=0.9, confidence=0.9,
                                     rationale="From evidence.", evidence_urls=urls, competitor_class=competitor_class,
                                     uk_evidence=[UkRelevanceQuote(url=u, quote=q, kind=k) for u, q, k in BECKETTS_UK]
                                     if domain == "beckettsmusic.co.uk" else [])
    return ClassificationBatch(companies=[
        item("beckettsmusic.co.uk", "Beckett's Music", T.SPECIALIST_RETAILER,
             ["https://beckettsmusic.co.uk/products/boss-rc-5"], classes.get("becketts", D)),
        item("ebay.co.uk", "eBay", T.MARKETPLACE, ["https://www.ebay.co.uk/itm/123"], classes.get("ebay", D)),
    ])  # fmt: skip


def run_discovery(catalogue, classifications, hits=GLOBAL_HITS, **settings):
    llm = FakeLLM(PLAN, classifications)
    agent = CompetitorDiscoveryAgent(FakeSearch(hits, PROFILE_HITS, frozenset()), llm, make_settings(**settings),
                                     catalogue.competitors,
                                     allowed_categories=[p.category for p in catalogue.products.values()])  # fmt: skip
    return asyncio.run(agent.run([catalogue.products["SB-001"]]))


def test_agent_applies_safeguards_and_records_metadata(catalogue):
    result = run_discovery(catalogue, batch())
    by = {c.domain: c for c in result.candidates}
    becketts, ebay = by["beckettsmusic.co.uk"], by["ebay.co.uk"]
    assert becketts.competitor_class is D and becketts.classification_model == "fake/llm-1@2026"
    assert becketts.classified_at is not None
    assert ebay.proposed_competitor_class is D and ebay.competitor_class is R
    # unclassified candidates still get a final class
    assert by["lookalike-music.co.uk"].competitor_class is N
    assert not [w for w in result.warnings if "Classification failed" in w]
    # the 13 configured competitors are never classified
    assert {c.domain for c in result.candidates}.isdisjoint({k.domain for k in catalogue.competitors})


def test_pmt_is_excluded_before_classification(catalogue):
    hits = {"SB-001": [*GLOBAL_HITS["SB-001"],
                       RawHit("https://www.pmtonline.co.uk/boss-rc-5", "Boss RC-5 | PMT Online", "£189 in stock")]}
    result = run_discovery(catalogue, batch(), hits=hits)
    assert "pmtonline.co.uk" not in {c.domain for c in result.candidates}
    (pmt,) = [x for x in result.excluded if x.domain == "pmtonline.co.uk"]
    assert pmt.reason == "Excluded competitor (removed by user decision)"
    assert "pmtonline.co.uk" not in {t.domain for t in result.search_targets}


# --------------------------------------------------------------------------- persistence


def llm_classified(domain="example.co.uk") -> DiscoveredCompetitor:
    c = stored_candidate(domain)
    c.evidence += uk_pages(domain)  # verified UK evidence for the cited quotes
    apply_classification(c, cls(domain=domain, competitor_class=R, evidence_urls=[f"https://{domain}/a"]), CATS)
    c.classified_at, c.classification_model = NOW, "openai/gpt-6-luna"
    classify_competitor(c, MARKETPLACES)
    return c


def test_classification_survives_a_later_no_llm_run(tmp_path):
    store = ReviewStore(tmp_path / "r.sqlite3")
    store.record_run(run_result([llm_classified()], run_id="llm", mode=DiscoveryMode.LLM_ENABLED))
    later = stored_candidate("example.co.uk", urls=("https://example.co.uk/b",))
    later.classification_status = ClassificationStatus.SKIPPED_LLM_DISABLED
    classify_competitor(later, MARKETPLACES)  # a no-LLM run computes NEEDS_REVIEW
    store.record_run(run_result([later], run_id="nollm"))
    kept = store.get_candidate("example.co.uk")
    c = kept.candidate
    assert (c.proposed_competitor_class, c.competitor_class) == (R, R)
    assert c.classification_model == "openai/gpt-6-luna" and c.classified_at == NOW
    assert c.competitor_class_reasons[-1] == "Accepted: RELATED_RETAILER"
    assert kept.discovery_mode is DiscoveryMode.LLM_ENABLED and kept.times_seen == 2
    assert {"https://example.co.uk/a", "https://example.co.uk/b"} <= {e.url for e in c.evidence}


def test_old_records_without_classification_still_load(tmp_path):
    store = ReviewStore(tmp_path / "r.sqlite3")
    store.record_run(run_result([stored_candidate()]))
    with sqlite3.connect(store.path) as conn:  # simulate a record stored before this change
        (rec,) = conn.execute("SELECT record FROM review_candidates").fetchone()
        data = json.loads(rec)
        for k in ("proposed_competitor_class", "competitor_class", "competitor_class_reasons", "classified_at",
                  "classification_model"):
            data["candidate"].pop(k)
        conn.execute("UPDATE review_candidates SET record = ?", (json.dumps(data),))
    c = store.get_candidate("example.co.uk").candidate
    assert c.competitor_class is None and c.competitor_class_reasons == []


def test_pmt_cannot_enter_the_review_queue(tmp_path):
    store = ReviewStore(tmp_path / "r.sqlite3", ("pmtonline.co.uk",))
    written = store.record_run(run_result([stored_candidate("pmtonline.co.uk"), stored_candidate("other.co.uk")]))
    assert written == 1 and [s.domain for s in store.list_candidates()] == ["other.co.uk"]


def test_review_api_exposes_classification_and_hides_pmt(tmp_path):
    s = make_settings(review_db_path=tmp_path / "r.sqlite3")
    assert s.discovery_excluded_competitor_domains == ("pmtonline.co.uk",)
    with TestClient(create_app(s)) as client:
        store = client.app.state.review_store
        store.record_run(run_result([llm_classified(), stored_candidate("pmtonline.co.uk")]))
        rows = client.get("/api/review/candidates").json()
        assert [r["domain"] for r in rows] == ["example.co.uk"]
        assert rows[0]["competitor_class"] == "RELATED_RETAILER" and rows[0]["proposed_competitor_class"] == "RELATED_RETAILER"
        detail = client.get("/api/review/candidates/example.co.uk").json()
        assert detail["classification_model"] == "openai/gpt-6-luna"
        assert detail["competitor_class_reasons"][-1] == "Accepted: RELATED_RETAILER"
        assert client.get("/api/review/candidates/pmtonline.co.uk").status_code == 404
        # approval still works exactly as before and does not touch the class
        r = client.post("/api/review/candidates/example.co.uk/approve",
                        json={"reviewer": "sam", "company_type": "retailer", "benchmark_eligible": True})
        assert r.status_code == 201, r.text
        after = client.get("/api/review/candidates/example.co.uk").json()
        assert after["benchmark_eligible"] is True and after["competitor_class"] == "RELATED_RETAILER"
