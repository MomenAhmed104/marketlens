"""UK relevance evidence in competitor review and approval (Option B). Deterministic; no network, no keys.

Three separate concepts, never merged: verified UK evidence (with its uncertainty), domain
approval, and benchmark permission. There is no UK category, recommendation or override:
the reviewer's judgement is the approve/reject decision and its notes.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.agents.discovery.rules import (
    AUTO_APPROVE_MIN_UK_SOURCES,
    Thresholds,
    apply_classification,
    apply_review_decision,
    decide,
    reconcile_uk_seller,
    uk_review,
)
from app.agents.discovery.schemas import (
    ClassificationStatus,
    CompanyClassification,
    UkEvidenceAssessment,
    UkSeller,
    VerifiedUkQuote,
)
from app.agents.discovery.schemas import UkRelevanceEvidenceKind as K
from app.main import create_app
from app.schemas.common import DomainApprovalStatus as S
from app.schemas.competitor import CompanyType as T
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from tests.discovery_fakes import FakeLLM
from tests.pipeline_fakes import harness
from tests.search_fakes import make_settings
from tests.test_discovery_agent import CLASSIFICATIONS, PLAN
from tests.test_discovery_rules import ev
from tests.test_review_store import decision, run
from tests.test_uk_discovery_integration import (
    ABROAD_DOMAIN,
    ABROAD_EVIDENCE,
    ABROAD_QUOTES,
    CATS,
    UK_ABOUT,
    UK_DOMAIN,
    UK_EVIDENCE,
    UK_ONE_PAGE,
    UK_PRODUCT,
    UK_QUOTES,
    US_DOMAIN,
    US_EVIDENCE,
    US_QUOTES,
    cand,
    classified,
    core,
    quote,
)

NOW = datetime(2026, 11, 19, tzinfo=UTC)
TH = Thresholds(0.85, 0.75, 2, 0.3)
WEAK = [quote(UK_PRODUCT, "£189.00 in stock", "UK_PHYSICAL_STORE"),
        quote(UK_PRODUCT, "Boss RC-5 Loop Station - Beckett's Music", "WEAK_SIGNAL")]  # fmt: skip


def vq(kind, supports=True, quote_="a quote of some length", url="https://x.example/a"):
    return VerifiedUkQuote(url=url, quote=quote_, kind=kind, verified=supports, supports=supports)


def approved(domain=UK_DOMAIN, bench=False, ctype=T.RETAILER, notes="Checked the shop page myself"):
    return DomainDecisionRecord(domain=domain, decision=ReviewDecision.APPROVED, company_type=ctype, reviewer="Sam",
                                decided_at=NOW, benchmark_eligible=bench, notes=notes)  # fmt: skip


# --------------------------------------------------------------------------- the review view


def test_uk_based_evidence_is_shown_as_verified():
    r = uk_review(classified(UK_DOMAIN, UK_EVIDENCE, UK_QUOTES).uk_assessment)
    assert (r.assessed, r.sufficient, r.contradicted, r.mixed_location, r.independent_sources) == (
        True, True, False, False, 2)
    assert len(r.verified_evidence) == 4 and r.missing_evidence == []
    assert "Approval is still a reviewer decision" in r.summary


def test_overseas_evidence_is_shown_as_verified():
    r = uk_review(classified(ABROAD_DOMAIN, ABROAD_EVIDENCE, ABROAD_QUOTES).uk_assessment)
    assert r.sufficient and not r.contradicted and r.independent_sources == 2
    assert {q.kind for q in r.verified_evidence} == {K.NON_UK_OPERATOR, K.UK_DELIVERY}


def test_contradiction_is_shown_and_stays_reviewable():
    r = uk_review(classified(US_DOMAIN, US_EVIDENCE, US_QUOTES).uk_assessment)
    assert r.contradicted and not r.sufficient and "Contradictory UK evidence" in r.summary
    assert r.verified_evidence[0].kind is K.NO_UK_SERVICE and r.missing_evidence


def test_contradiction_alongside_uk_evidence_shows_both():
    quotes = [*ABROAD_QUOTES, *US_QUOTES]
    r = uk_review(classified(ABROAD_DOMAIN, ABROAD_EVIDENCE + US_EVIDENCE, quotes).uk_assessment)
    assert r.sufficient and r.contradicted and "while other verified evidence shows UK relevance" in r.summary


def test_insufficient_evidence_says_what_is_missing():
    r = uk_review(classified(UK_DOMAIN, UK_EVIDENCE, []).uk_assessment)
    assert r.assessed and not r.sufficient and "Insufficient verified UK evidence" in r.summary
    assert any("UK address with a postcode" in m for m in r.missing_evidence)
    assert any("not enough" in m for m in r.missing_evidence)


def test_missing_assessment_is_shown_as_not_assessed():
    r = uk_review(None)
    assert (r.assessed, r.sufficient, r.contradicted, r.independent_sources) == (False, False, False, 0)
    assert r.missing_evidence and "Not assessed" in r.summary and r.verified_evidence == []


def test_weak_signals_are_shown_only_as_unverified_claims():
    r = uk_review(classified(UK_DOMAIN, UK_EVIDENCE, WEAK).uk_assessment)
    assert not r.sufficient and r.verified_evidence == [] and len(r.unverified_claims) == 2
    assert all(not q.supports for q in r.unverified_claims)


def test_invented_quotes_are_unverified_claims_never_evidence():
    fake = quote(UK_ABOUT, "Our flagship store on Oxford Street, London W1D 1BS", "UK_PHYSICAL_STORE")
    r = uk_review(classified(UK_DOMAIN, UK_EVIDENCE, [fake, UK_QUOTES[0]]).uk_assessment)
    assert [q.quote for q in r.verified_evidence] == ["Visit our shop in Bedford"]
    assert [q.verified for q in r.unverified_claims] == [False]
    assert r.sufficient and r.independent_sources == 1


def test_missing_evidence_points_at_the_gap_for_a_business_abroad():
    r = uk_review(classified(ABROAD_DOMAIN, ABROAD_EVIDENCE, ABROAD_QUOTES[:1]).uk_assessment)
    assert not r.sufficient and any("delivers or ships to the UK" in m for m in r.missing_evidence)


def test_mixed_location_is_flagged():
    a = UkEvidenceAssessment(evidence=[vq(K.UK_ADDRESS), vq(K.NON_UK_OPERATOR, url="https://x.example/b")])
    r = uk_review(a)
    assert r.sufficient and r.mixed_location and "based outside the UK" in r.summary


@pytest.mark.parametrize("a", [
    UkEvidenceAssessment(sufficient=True, independent_sources=3),  # flags, but no evidence stored at all
    UkEvidenceAssessment(sufficient=True, evidence=[vq(K.UK_DELIVERY)]),  # delivery alone is not sufficient
    UkEvidenceAssessment(sufficient=True, evidence=[vq(K.UK_ADDRESS, supports=False)]),  # not supporting
    UkEvidenceAssessment(sufficient=True, evidence=[vq(K.NON_UK_OPERATOR)]),  # operator without delivery
])
def test_stored_flags_not_backed_by_their_evidence_are_never_trusted(a):
    r = uk_review(a)
    assert not r.sufficient and "Insufficient verified UK evidence" in r.summary and r.missing_evidence


def test_review_view_is_deterministic():
    a = classified(UK_DOMAIN, UK_EVIDENCE, UK_QUOTES).uk_assessment
    assert uk_review(a) == uk_review(a) and uk_review(a, approved()) == uk_review(a, approved())


# --------------------------------------------------------------------------- reviewer decisions


def test_approval_despite_a_contradiction_keeps_the_contradiction_visible():
    c = classified(US_DOMAIN, US_EVIDENCE, US_QUOTES)
    before = c.uk_assessment.model_copy(deep=True)
    d = approved(US_DOMAIN, notes="They ship to the UK through a reseller; checked by phone")
    apply_review_decision(c, d)
    r = uk_review(c.uk_assessment, d)
    assert c.approval_status is S.APPROVED and c.as_search_target().approval_status is S.APPROVED
    assert c.price_benchmark is False  # approval never implies a benchmark
    assert r.contradicted and "Sam approved it on 2026-11-19 despite the contradictory evidence" in r.summary
    assert c.uk_assessment == before and r.verified_evidence[0].kind is K.NO_UK_SERVICE


def test_approval_without_sufficient_evidence_is_noted():
    r = uk_review(classified(UK_DOMAIN, UK_EVIDENCE, []).uk_assessment, approved())
    assert "approved it on 2026-11-19 without sufficient verified UK evidence" in r.summary


def test_a_rejection_or_verified_approval_adds_no_note():
    a = classified(UK_DOMAIN, UK_EVIDENCE, UK_QUOTES).uk_assessment
    rejected = approved().model_copy(update={"decision": ReviewDecision.REJECTED})
    assert "approved it" not in uk_review(a, approved()).summary
    assert "approved it" not in uk_review(classified(US_DOMAIN, US_EVIDENCE, US_QUOTES).uk_assessment,
                                          rejected).summary  # fmt: skip


@pytest.mark.parametrize("quotes", [UK_QUOTES, US_QUOTES, [], WEAK])
def test_decisions_set_approval_and_benchmark_whatever_the_uk_evidence(quotes):
    for dec, bench in ((ReviewDecision.APPROVED, False), (ReviewDecision.APPROVED, True),
                       (ReviewDecision.REJECTED, False)):
        c = classified(UK_DOMAIN, UK_EVIDENCE + US_EVIDENCE, quotes)
        d = DomainDecisionRecord(domain=UK_DOMAIN, decision=dec, company_type=T.RETAILER, reviewer="Sam",
                                 decided_at=NOW, benchmark_eligible=bench)  # fmt: skip
        apply_review_decision(c, d)
        expected = S.APPROVED if dec is ReviewDecision.APPROVED else S.REJECTED
        assert (c.approval_status, c.price_benchmark) == (expected, bench), (dec, bench)


# --------------------------------------------------------------------------- uk_seller reconciliation


def summary(sufficient=False, contradicted=False):
    return UkEvidenceAssessment(sufficient=sufficient, contradicted=contradicted)


@pytest.mark.parametrize(("proposed", "a", "expected"), [
    (UkSeller.YES, summary(sufficient=True), UkSeller.YES),
    (UkSeller.UNKNOWN, summary(sufficient=True), UkSeller.UNKNOWN),  # never upgraded
    (UkSeller.NO, summary(sufficient=True), UkSeller.UNKNOWN),  # a contradiction is not resolved either way
    (UkSeller.YES, summary(contradicted=True), UkSeller.NO),
    (UkSeller.UNKNOWN, summary(contradicted=True), UkSeller.NO),
    (UkSeller.YES, summary(sufficient=True, contradicted=True), UkSeller.UNKNOWN),  # both verified: a person decides
    (UkSeller.YES, summary(), UkSeller.UNKNOWN),  # .co.uk / GBP alone: not a verified yes
    (UkSeller.NO, summary(), UkSeller.UNKNOWN),  # an unverified no never rejects
])
def test_reconcile_uk_seller(proposed, a, expected):
    final, why = reconcile_uk_seller(proposed, a)
    assert final is expected and (why is None) == (final is proposed)


def test_co_uk_and_gbp_alone_never_make_uk_seller_yes():
    c = classified(UK_DOMAIN, UK_EVIDENCE, WEAK, uk_seller="yes")
    assert c.uk_seller is UkSeller.UNKNOWN
    assert any("uk_seller 'yes' set to 'unknown'" in n for n in c.validation_notes)


def test_an_answer_without_uk_evidence_never_keeps_an_unverified_yes():
    c = classified(UK_DOMAIN, UK_EVIDENCE, None, uk_seller="yes")
    assert c.uk_assessment is not None and not c.uk_assessment.sufficient and c.uk_seller is UkSeller.UNKNOWN


# --------------------------------------------------------------------------- approval (decide)


def full(domain, evidence, quotes, **kw):
    second = evidence[0].model_copy(update={"url": evidence[0].url + "-2", "canonical_url": evidence[0].url + "-2"})
    c = classified(domain, [*evidence, second], quotes, company_name=kw.pop("company_name", "Beckett's Music"),
                   evidence_urls=[evidence[0].url, evidence[1].url], **kw)  # fmt: skip
    return c


def test_verified_evidence_from_two_pages_auto_approves_with_every_other_rule():
    assert AUTO_APPROVE_MIN_UK_SOURCES == 2
    c = full(UK_DOMAIN, UK_EVIDENCE, UK_QUOTES)
    decide(c, TH)
    assert c.approval_status is S.AUTO_APPROVED and c.price_benchmark is False


def test_evidence_from_a_single_page_needs_review():
    c = full(UK_DOMAIN, UK_EVIDENCE, UK_ONE_PAGE)
    decide(c, TH)
    assert c.approval_status is S.APPROVAL_REQUIRED
    assert any("UK evidence from 1 independent page(s) < 2" in r for r in c.decision_reasons)


def test_insufficient_evidence_needs_review():
    c = full(UK_DOMAIN, UK_EVIDENCE, WEAK)
    decide(c, TH)
    assert c.approval_status is S.APPROVAL_REQUIRED
    assert any("insufficient verified UK evidence" in r for r in c.decision_reasons)


def test_contradicted_evidence_goes_to_review_and_is_never_rejected_automatically():
    c = full(US_DOMAIN, US_EVIDENCE, US_QUOTES, company_name=None)
    decide(c, TH)
    assert c.approval_status is S.APPROVAL_REQUIRED and c.price_benchmark is False
    assert any("contradictory UK evidence" in r for r in c.decision_reasons)


def test_contradiction_blocks_auto_approval_even_with_sufficient_uk_evidence():
    c = full(ABROAD_DOMAIN, ABROAD_EVIDENCE + US_EVIDENCE, [*ABROAD_QUOTES, *US_QUOTES], company_name="Musicstore")
    decide(c, TH)
    assert c.uk_assessment.sufficient and c.approval_status is S.APPROVAL_REQUIRED


def test_mixed_location_blocks_auto_approval():
    shop = ev(f"https://{ABROAD_DOMAIN}/london", "London showroom",
              "Visit our showroom at 1 Denmark Street, London WC2H 8LP")  # fmt: skip
    quotes = [*ABROAD_QUOTES, quote(shop.url, "Visit our showroom at 1 Denmark Street, London WC2H 8LP", "UK_ADDRESS")]
    c = full(ABROAD_DOMAIN, [*ABROAD_EVIDENCE, shop], quotes, company_name="Musicstore")
    decide(c, TH)
    assert c.uk_assessment.mixed_location and c.approval_status is S.APPROVAL_REQUIRED
    assert any("mixed UK evidence" in r for r in c.decision_reasons)


def test_unverified_no_uk_selling_is_never_an_automatic_rejection():
    c = classified(US_DOMAIN, US_EVIDENCE, [], uk_seller="no")
    c.uk_signals = []
    decide(c, TH)
    assert c.uk_seller is UkSeller.UNKNOWN and c.approval_status is S.APPROVAL_REQUIRED


def test_overseas_retailer_without_uk_domain_gbp_or_postcode_can_auto_approve():
    """Weak signals are no longer an auto-approval requirement (user decision, 19 Nov 2026)."""
    c = full(ABROAD_DOMAIN, ABROAD_EVIDENCE, ABROAD_QUOTES, company_name="Musicstore")
    c.uk_signals = []  # no .uk domain, no GBP price, no UK postcode
    decide(c, TH)
    assert c.approval_status is S.AUTO_APPROVED and c.price_benchmark is False
    assert not any("strong UK signal" in r for r in c.decision_reasons)


def test_llm_disabled_candidates_stay_pending():
    c = cand(UK_DOMAIN, UK_EVIDENCE).model_copy(update={"classification_status":
                                                        ClassificationStatus.SKIPPED_LLM_DISABLED})
    decide(c, TH)
    assert c.approval_status is S.APPROVAL_REQUIRED and c.uk_assessment is None and c.price_benchmark is False


# --------------------------------------------------------------------------- benchmark separation


@pytest.mark.parametrize("quotes", [UK_QUOTES, ABROAD_QUOTES, US_QUOTES, WEAK])
def test_uk_evidence_never_grants_benchmark_permission_on_its_own(quotes):
    for ctype in ("retailer", "specialist_retailer", "manufacturer", "marketplace", "directory", "other"):
        c = full(UK_DOMAIN, UK_EVIDENCE + ABROAD_EVIDENCE + US_EVIDENCE, quotes, company_type=ctype)
        decide(c, TH, ("ebay",))
        assert c.price_benchmark is False, ctype


def test_manufacturer_and_marketplace_rules_are_unchanged():
    for domain, ctype in (("boss.info", "manufacturer"), ("ebay.co.uk", "retailer"), ("ebay.co.uk", "marketplace")):
        ev_ = [UK_EVIDENCE[0].model_copy(update={"url": f"https://{domain}/boss-rc-5",
                                                 "canonical_url": f"https://{domain}/boss-rc-5"}), *UK_EVIDENCE[1:]]
        c = full(domain, ev_, UK_QUOTES, company_type=ctype)
        decide(c, TH, ("ebay",))
        assert c.approval_status is S.APPROVAL_REQUIRED and c.price_benchmark is False, (domain, ctype)


def test_reviewer_benchmark_flag_alone_decides_benchmarking_whatever_the_uk_evidence():
    for quotes in (UK_QUOTES, ABROAD_QUOTES, US_QUOTES, WEAK):
        c = classified(ABROAD_DOMAIN, ABROAD_EVIDENCE + UK_EVIDENCE + US_EVIDENCE, quotes)
        apply_review_decision(c, decision(ABROAD_DOMAIN, benchmark_eligible=False))
        assert c.price_benchmark is False
        apply_review_decision(c, decision(ABROAD_DOMAIN, benchmark_eligible=True))
        assert c.price_benchmark is True  # retailer type + explicit flag, as before


@pytest.mark.parametrize("ctype", ["retailer", "specialist_retailer"])
def test_auto_approved_uk_based_retailer_gets_no_benchmark_permission(ctype):
    c = full(UK_DOMAIN, UK_EVIDENCE, UK_QUOTES, company_type=ctype)
    decide(c, TH)
    assert c.approval_status is S.AUTO_APPROVED and c.price_benchmark is False
    assert c.as_search_target().price_benchmark is False  # what the pipeline would search with


@pytest.mark.parametrize("ctype", ["retailer", "specialist_retailer"])
def test_auto_approved_overseas_retailer_serving_the_uk_gets_no_benchmark_permission(ctype):
    c = full(ABROAD_DOMAIN, ABROAD_EVIDENCE, ABROAD_QUOTES, company_type=ctype, company_name="Musicstore")
    decide(c, TH)
    assert c.uk_assessment.sufficient and c.approval_status is S.AUTO_APPROVED and c.price_benchmark is False


def test_manual_benchmark_permission_is_unchanged_after_auto_approval():
    c = full(UK_DOMAIN, UK_EVIDENCE, UK_QUOTES)
    decide(c, TH)
    assert c.price_benchmark is False
    apply_review_decision(c, decision(UK_DOMAIN, company_type=T.SPECIALIST_RETAILER, benchmark_eligible=True))
    assert c.approval_status is S.APPROVED and c.price_benchmark is True  # the reviewer's explicit flag
    apply_review_decision(c, decision(UK_DOMAIN, company_type=T.MANUFACTURER, benchmark_eligible=True))
    assert c.price_benchmark is True  # manufacturers stay benchmark types with an explicit flag
    apply_review_decision(c, decision(UK_DOMAIN, company_type=T.RETAILER, benchmark_eligible=False))
    assert c.price_benchmark is False


def test_reviewer_cannot_make_a_marketplace_a_benchmark():
    with pytest.raises(ValueError, match="benchmark_eligible not allowed"):
        decision("ebay.co.uk", company_type=T.MARKETPLACE, benchmark_eligible=True)


# --------------------------------------------------------------------------- review API


def stored_cand(domain, evidence, quotes):
    c = classified(domain, evidence, quotes)
    c.classification_status = ClassificationStatus.COMPLETED
    return c


@pytest.fixture
def api(tmp_path):
    app = create_app(make_settings(review_db_path=tmp_path / "review.sqlite3"))
    client = TestClient(app)
    client.__enter__()
    store = app.state.review_store
    weak = cand("weak.co.uk", [UK_EVIDENCE[0].model_copy(update={"url": "https://weak.co.uk/a",
                                                                "canonical_url": "https://weak.co.uk/a"})])
    store.record_run(run([stored_cand(UK_DOMAIN, UK_EVIDENCE, UK_QUOTES),
                          stored_cand(ABROAD_DOMAIN, ABROAD_EVIDENCE, ABROAD_QUOTES),
                          stored_cand(US_DOMAIN, US_EVIDENCE, US_QUOTES), weak]))  # fmt: skip
    store.create_decision(decision(US_DOMAIN, decision=ReviewDecision.REJECTED, company_type=None))
    yield client
    client.__exit__(None, None, None)


def listing(client):
    return {c["domain"]: c for c in client.get("/api/review/candidates").json()}


def test_review_list_carries_the_uk_evidence_view_for_every_candidate(api):
    cands = listing(api)
    view = {d: (c["uk_review"]["assessed"], c["uk_review"]["sufficient"], c["uk_review"]["contradicted"])
            for d, c in cands.items()}  # fmt: skip
    assert view == {UK_DOMAIN: (True, True, False), ABROAD_DOMAIN: (True, True, False),
                    US_DOMAIN: (True, False, True), "weak.co.uk": (False, False, False)}
    uk = cands[UK_DOMAIN]["uk_review"]
    assert uk["independent_sources"] == 2
    assert {q["kind"] for q in uk["verified_evidence"]} == {"UK_PHYSICAL_STORE", "UK_ADDRESS",
                                                            "UK_COMPANY_REGISTRATION", "UK_DELIVERY"}
    assert all(q["url"].startswith("https://") and q["quote"] for q in uk["verified_evidence"])
    assert not {"recommendation", "basis", "relevance", "confidence", "override"} & set(uk)


def test_existing_review_fields_are_unchanged(api):
    c = listing(api)[UK_DOMAIN]
    for key in ("domain", "homepage_url", "review_status", "decision", "search_eligible", "benchmark_eligible",
                "discovery_mode", "classification_status", "llm_classified", "automatic_status",
                "proposed_company_name", "name_verified", "proposed_company_type", "confidence", "relevance",
                "uk_seller", "uk_signals", "products_matched", "known_marketplace", "competitor_class",
                "proposed_competitor_class", "evidence_count", "first_seen_at", "last_seen_at", "times_seen",
                "last_run_id"):
        assert key in c, key
    assert listing(api)[US_DOMAIN]["review_status"] == "REJECTED"  # contradicted domains are still listed


def test_detail_matches_the_list(api):
    detail = api.get(f"/api/review/candidates/{ABROAD_DOMAIN}").json()
    assert detail["uk_review"] == listing(api)[ABROAD_DOMAIN]["uk_review"]


def test_approving_a_contradicted_domain_keeps_the_evidence_and_records_the_note(api):
    api.put(f"/api/review/decisions/{US_DOMAIN}", json={
        "reviewer": "Sam", "decision": "APPROVED", "company_type": "retailer",
        "notes": "Confirmed by phone that they now ship to the UK"})  # fmt: skip
    uk = listing(api)[US_DOMAIN]["uk_review"]
    assert uk["contradicted"] and "despite the contradictory evidence" in uk["summary"]
    assert uk["verified_evidence"][0]["kind"] == "NO_UK_SERVICE"
    history = api.get(f"/api/review/candidates/{US_DOMAIN}").json()["history"]
    assert [h["decision"] for h in history] == ["REJECTED", "APPROVED"]
    assert history[-1]["notes"] == "Confirmed by phone that they now ship to the UK"
    cfg = api.get("/api/intelligence/config").json()
    assert US_DOMAIN in {t["domain"] for t in cfg["search_targets"]}  # the reviewer's call is respected


def test_a_new_assessment_never_overwrites_a_decision(api):
    api.post(f"/api/review/candidates/{UK_DOMAIN}/approve", json={"reviewer": "Sam", "company_type": "retailer"})
    store = api.app.state.review_store
    before = store.list_decisions()
    store.record_run(run([stored_cand(UK_DOMAIN, UK_EVIDENCE, UK_ONE_PAGE)], run_id="run-2"))
    assert store.list_decisions() == before
    assert listing(api)[UK_DOMAIN]["uk_review"]["independent_sources"] == 1  # the new verified assessment


def test_uk_review_is_never_persisted(api):
    raw = api.app.state.review_store.get_candidate(UK_DOMAIN).candidate
    assert raw.uk_review is None and raw.uk_assessment is not None


# --------------------------------------------------------------------------- discovery events and API


@pytest.mark.parametrize("with_llm", [True, False])
def test_competitor_discovered_events_carry_the_uk_evidence_view(catalogue, tmp_path, with_llm):
    kw = {"llm1": lambda: FakeLLM(PLAN, CLASSIFICATIONS)} if with_llm else {}
    h = harness(catalogue, tmp_path, **kw)
    _, result, events = asyncio.run(h.run(product_ids=["SB-001"]))
    payloads = {e.payload["domain"]: e.payload for e in events if e.type == "competitor_discovered"}
    cands = {c.domain: c for c in result.discovery.candidates}
    for domain, p in payloads.items():
        r = cands[domain].uk_review
        assert (p["uk_assessed"], p["uk_evidence_sufficient"], p["uk_evidence_contradicted"], p["uk_mixed_location"],
                p["uk_independent_sources"]) == (r.assessed, r.sufficient, r.contradicted, r.mixed_location,
                                                 r.independent_sources)  # fmt: skip
        assert not {"evidence", "verified_evidence", "unverified_claims"} & set(p)  # quotes stay in the run result
        assert "competitor_class" in p and "approval_status" in p  # existing contract kept
        assert not {"uk_relevance", "uk_confidence", "uk_recommendation", "uk_review_basis"} & set(p)
    beck = payloads["beckettsmusic.co.uk"]
    if with_llm:
        assert (beck["uk_assessed"], beck["uk_evidence_sufficient"], beck["uk_independent_sources"]) == (True, True, 2)
    else:
        assert (beck["uk_assessed"], beck["uk_evidence_sufficient"]) == (False, False)


def test_discovery_result_and_review_api_agree(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS))
    _, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
    from app.api.review import _summary

    store = h.review
    for c in result.discovery.candidates:
        s = store.get_candidate(c.domain)
        assert _summary(s, store.get_decision(c.domain), ())["uk_review"] == c.uk_review, c.domain


def test_reruns_keep_reviewer_decisions(catalogue, tmp_path):
    d = DomainDecisionRecord(domain="beckettsmusic.co.uk", decision=ReviewDecision.REJECTED, reviewer="Sam",
                             decided_at=NOW, notes="Not a competitor for us")  # fmt: skip
    h = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS), decisions=[d])
    for _ in range(2):
        _, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
        beck = next(c for c in result.discovery.candidates if c.domain == "beckettsmusic.co.uk")
        assert beck.approval_status is S.REJECTED and beck.uk_review.sufficient  # evidence still shown
    assert h.review.get_decision("beckettsmusic.co.uk") == d.model_copy(update={"version": 1})


def test_core_and_uk_fields_from_the_llm_together():
    # Helper sanity: a full classification with every kind of UK answer still parses.
    for quotes in (UK_QUOTES, ABROAD_QUOTES, US_QUOTES, WEAK):
        CompanyClassification.model_validate(core(UK_DOMAIN, [UK_PRODUCT], uk_evidence=quotes))
    c = cand(UK_DOMAIN, UK_EVIDENCE)
    apply_classification(c, CompanyClassification.model_validate(core(UK_DOMAIN, [UK_PRODUCT],
                                                                      uk_evidence=UK_QUOTES)), CATS)  # fmt: skip
    assert c.uk_assessment.sufficient
