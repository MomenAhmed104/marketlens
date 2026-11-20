"""UK relevance evidence wired into LLM 1 classification, evidence selection, prompts,
persistence and the discovery API (Option B). Fake search and fake LLM only: no network, no keys.

LLM 1 cites quotes with an evidence kind and gives no UK verdict; Python verifies them and
decides whether the evidence is sufficient. UK evidence is separate from company type,
competitor class, approval and benchmarking.
"""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.agents.discovery import prompts
from app.agents.discovery.agent import PROFILE_QUERY, CompetitorDiscoveryAgent
from app.agents.discovery.rules import (
    MAX_LLM_UK_QUOTES,
    MAX_STORED_UK_TEXT,
    Thresholds,
    apply_classification,
    apply_review_decision,
    classify_competitor,
    decide,
)
from app.agents.discovery.schemas import (
    ClassificationBatch,
    ClassificationStatus,
    CompanyClassification,
    DiscoveredCompetitor,
    EvidenceKind,
    UkEvidenceAssessment,
)
from app.api.discovery import get_agent_factory
from app.main import create_app
from app.schemas.common import DomainApprovalStatus as S
from app.schemas.competitor import CompanyType as T
from app.schemas.review import ReviewDecision
from app.services.llm import LLMOutputError, OpenRouterClient
from app.services.review_store import ReviewStore, merge_candidate
from app.services.search.base import RawHit
from tests.discovery_fakes import FakeLLM, FakeSearch
from tests.search_fakes import FAKE_KEY, Recorder, http_client, make_settings
from tests.test_discovery_agent import CLASSIFICATIONS, GLOBAL_HITS, PLAN, PROFILE_HITS
from tests.test_discovery_rules import ev
from tests.test_llm_openrouter import completion
from tests.test_review_store import decision, run

NOW = datetime(2026, 11, 18, tzinfo=UTC)
TH = Thresholds(0.85, 0.75, 2, 0.3)
CATS = {"Effects", "Guitars", "Amplification", "Accessories"}


def profile(url, title, snippet):
    return ev(url, title, snippet, kind=EvidenceKind.DOMAIN_PROFILE, product=None, model=False)


def cand(domain, evidence, products=("SB-001",)):
    return DiscoveredCompetitor(domain=domain, hostnames=[domain], homepage_url=f"https://{domain}/",
                                evidence=evidence, products_matched=list(products), uk_signals=["uk_domain"],
                                discovered_at=NOW)  # fmt: skip


def core(domain, urls, **kw):
    base = dict(domain=domain, company_name=None, company_type="specialist_retailer", relevant_categories=["Effects"],
                uk_seller="yes", sells_new_musical_instruments=True, relevance=0.9, confidence=0.9,
                rationale="From evidence.", evidence_urls=urls, competitor_class="DIRECT_COMPETITOR")  # fmt: skip
    base.update(kw)
    return base


def quote(url, text, kind):
    return {"url": url, "quote": text, "kind": kind}


# Three shapes of business, as their stored evidence would look.
UK_DOMAIN = "beckettsmusic.co.uk"
UK_PRODUCT = f"https://{UK_DOMAIN}/products/boss-rc-5"
UK_ABOUT = f"https://{UK_DOMAIN}/pages/about"
UK_DELIVERY = f"https://{UK_DOMAIN}/pages/delivery"
UK_EVIDENCE = [
    ev(UK_PRODUCT, "Boss RC-5 Loop Station - Beckett's Music", "£189.00 in stock"),
    profile(UK_ABOUT, "About Beckett's Music", "Visit our shop in Bedford: 12 High Street, Bedford, MK40 1AB. "
                                               "Beckett's Music Ltd, registered in England and Wales, company number "
                                               "01234567. Free UK delivery over £50."),
    profile(UK_DELIVERY, "Delivery", "We deliver to all UK mainland addresses within 2 working days."),
]
# Three lines from one page (one source) and a delivery statement from another (a second source).
UK_QUOTES = [quote(UK_ABOUT, "Visit our shop in Bedford", "UK_PHYSICAL_STORE"),
             quote(UK_ABOUT, "12 High Street, Bedford, MK40 1AB", "UK_ADDRESS"),
             quote(UK_ABOUT, "registered in England and Wales, company number 01234567", "UK_COMPANY_REGISTRATION"),
             quote(UK_DELIVERY, "We deliver to all UK mainland addresses", "UK_DELIVERY")]
UK_ONE_PAGE = UK_QUOTES[:3]

ABROAD_DOMAIN = "musicstore-eu.de"
ABROAD_PRODUCT = f"https://www.{ABROAD_DOMAIN}/en_GB/boss-rc-5"
ABROAD_IMPRINT = f"https://www.{ABROAD_DOMAIN}/en_GB/imprint"
ABROAD_SHIPPING = f"https://www.{ABROAD_DOMAIN}/en_GB/shipping"
ABROAD_EVIDENCE = [
    ev(ABROAD_PRODUCT, "Boss RC-5 | Musicstore", "€199 incl. VAT"),
    profile(ABROAD_IMPRINT, "Imprint", "Musicstore EU GmbH, Istanbulstrasse 22, 51103 Cologne, Germany."),
    profile(ABROAD_SHIPPING, "Shipping", "We deliver to the United Kingdom within 3-5 working days."),
]
ABROAD_QUOTES = [quote(ABROAD_IMPRINT, "Musicstore EU GmbH, Istanbulstrasse 22, 51103 Cologne, Germany",
                       "NON_UK_OPERATOR"),
                 quote(ABROAD_SHIPPING, "We deliver to the United Kingdom within 3-5 working days", "UK_DELIVERY")]

US_DOMAIN = "usguitars.example"
US_PRODUCT = f"https://www.{US_DOMAIN}/boss-rc-5"
US_SHIP = f"https://www.{US_DOMAIN}/shipping"
US_EVIDENCE = [
    ev(US_PRODUCT, "Boss RC-5 | US Guitars", "$199"),
    profile(US_SHIP, "Shipping", "US Guitars Inc., Nashville, TN. We do not ship to the United Kingdom."),
]
US_QUOTES = [quote(US_SHIP, "We do not ship to the United Kingdom", "NO_UK_SERVICE")]


def classified(domain, evidence, quotes=None, **kw):
    """Apply one LLM 1 answer. `quotes=None` leaves `uk_evidence` out entirely (an old-style answer)."""
    c = cand(domain, evidence)
    extra = {} if quotes is None else {"uk_evidence": quotes}
    cls = CompanyClassification.model_validate(core(domain, [evidence[0].url], **extra, **kw))
    apply_classification(c, cls, CATS)
    return c


# --------------------------------------------------------------------------- evidence shapes via apply_classification


def test_uk_based_retailer_has_sufficient_evidence_from_two_pages():
    a = classified(UK_DOMAIN, UK_EVIDENCE, UK_QUOTES).uk_assessment
    assert a.sufficient and not a.contradicted and not a.mixed_location and a.independent_sources == 2
    assert len(a.evidence) == 4 and all(q.verified and q.supports for q in a.evidence)


def test_lines_from_one_page_are_one_source():
    a = classified(UK_DOMAIN, UK_EVIDENCE, UK_ONE_PAGE).uk_assessment
    assert a.sufficient and a.independent_sources == 1


def test_overseas_retailer_serving_the_uk_has_sufficient_evidence():
    a = classified(ABROAD_DOMAIN, ABROAD_EVIDENCE, ABROAD_QUOTES).uk_assessment
    assert a.sufficient and not a.contradicted and a.independent_sources == 2


def test_overseas_retailer_not_serving_the_uk_is_contradicted():
    a = classified(US_DOMAIN, US_EVIDENCE, US_QUOTES).uk_assessment
    assert a.contradicted and not a.sufficient and a.independent_sources == 0


def test_co_uk_domain_and_gbp_price_alone_are_insufficient():
    weak = [quote(UK_PRODUCT, "Boss RC-5 Loop Station - Beckett's Music", "WEAK_SIGNAL"),
            quote(UK_PRODUCT, "£189.00 in stock", "UK_PHYSICAL_STORE")]  # a price mislabelled as a shop
    a = classified(UK_DOMAIN, UK_EVIDENCE, weak).uk_assessment
    assert not a.sufficient and a.evidence == []  # only supporting quotes are stored as evidence
    assert len(a.unverified_claims) == 2 and any("discarded 2" in r for r in a.reasons)


def test_invented_quotes_are_discarded_and_reported():
    fake = [quote(UK_ABOUT, "Our flagship store on Oxford Street, London W1D 1BS", "UK_PHYSICAL_STORE")]
    a = classified(UK_DOMAIN, UK_EVIDENCE, fake).uk_assessment
    assert not a.sufficient and a.evidence == [] and not a.unverified_claims[0].verified
    assert any("not found in the evidence" in r for r in a.reasons)


def test_negative_delivery_cited_as_uk_delivery_does_not_count():
    bad = [ABROAD_QUOTES[0], quote(US_SHIP, "We do not ship to the United Kingdom", "UK_DELIVERY")]
    assert not classified(ABROAD_DOMAIN, ABROAD_EVIDENCE + US_EVIDENCE, bad).uk_assessment.sufficient


def test_quotes_beyond_five_are_ignored():
    filler = [quote(UK_ABOUT, f"Visit our shop in Bedford{'!' * i}", "UK_PHYSICAL_STORE") for i in range(1, 3)]
    quotes = [*filler, *UK_QUOTES]  # 6 quotes: the 6th (delivery) is beyond the limit
    a = classified(UK_DOMAIN, UK_EVIDENCE, quotes).uk_assessment
    assert MAX_LLM_UK_QUOTES == 5 and any("beyond the first 5" in r for r in a.reasons)
    assert all(q.kind.value != "UK_DELIVERY" for q in a.evidence) and a.independent_sources == 1


def test_long_quotes_are_verified_in_full_then_clipped_for_storage():
    long_text = "Visit our shop in Bedford " + "with guitars, amps and pedals " * 30
    evidence = [UK_EVIDENCE[0], profile(UK_ABOUT, "About", long_text)]
    a = classified(UK_DOMAIN, evidence, [quote(UK_ABOUT, long_text.strip(), "UK_PHYSICAL_STORE")]).uk_assessment
    q = a.evidence[0]
    assert q.verified and q.supports and len(q.quote) == MAX_STORED_UK_TEXT and q.quote.endswith("…")
    assert a.sufficient


# --------------------------------------------------------------------------- missing and malformed UK answers


def test_no_uk_evidence_is_assessed_as_insufficient_never_assumed():
    c = classified(UK_DOMAIN, UK_EVIDENCE)
    assert c.classification_status is ClassificationStatus.COMPLETED
    assert c.uk_assessment is not None and not c.uk_assessment.sufficient and c.uk_assessment.evidence == []


def test_malformed_uk_answer_keeps_the_core_classification_and_records_why():
    c = classified(UK_DOMAIN, UK_EVIDENCE, "lots")
    assert c.company_type is T.SPECIALIST_RETAILER and c.proposed_competitor_class.value == "DIRECT_COMPETITOR"
    a = c.uk_assessment
    assert not a.sufficient and [r for r in a.reasons if r.startswith("UK answer cleaned")]
    assert not any("UK answer" in n for n in c.validation_notes)  # UK notes stay in the UK assessment


# --------------------------------------------------------------------------- the real structured-output parsing path


def or_client(rec):
    return OpenRouterClient(http_client("openrouter", rec), FAKE_KEY, "test/model")


def parse(*contents):
    rec = Recorder(*(completion(c) for c in contents))
    client = or_client(rec)

    async def go():
        try:
            return await client.complete_structured(system="s", user="u", output_model=ClassificationBatch,
                                                    schema_name="company_classification")  # fmt: skip
        finally:
            await client.aclose()

    return asyncio.run(go()), rec


def test_parsing_path_requires_uk_evidence_and_no_verdict():
    result, rec = parse(json.dumps({"companies": [core(UK_DOMAIN, [UK_PRODUCT], uk_evidence=UK_QUOTES)]}))
    schema = rec.body()["response_format"]["json_schema"]
    assert schema["strict"] is True
    company = schema["schema"]["$defs"]["CompanyClassification"]
    assert "uk_evidence" in company["required"]
    assert not {"uk_relevance", "uk_confidence"} & set(company["properties"])
    assert len(result.value.companies[0].uk_evidence) == 4 and result.attempts == 1


def test_parsing_path_malformed_uk_fields_fall_back_without_a_repair_call():
    bad = core(UK_DOMAIN, [UK_PRODUCT], uk_evidence=[{"url": UK_ABOUT, "kind": "UK_ADDRESS"}, UK_QUOTES[0]])
    other = core("other.co.uk", ["https://other.co.uk/x"], uk_evidence=[])
    result, rec = parse(json.dumps({"companies": [bad, other]}))
    assert len(rec.requests) == 1 and result.attempts == 1  # no repair round trip for a UK problem
    first, second = result.value.companies
    assert first.company_type is T.SPECIALIST_RETAILER and len(first.uk_evidence) == 1
    assert len(first.uk_validation_warnings) == 1 and second.uk_validation_warnings == []
    c = cand(UK_DOMAIN, UK_EVIDENCE)
    apply_classification(c, first, CATS)
    assert sum(r.startswith("UK answer cleaned") for r in c.uk_assessment.reasons) == 1


def test_parsing_path_missing_uk_evidence_falls_back():
    result, _ = parse(json.dumps({"companies": [core(UK_DOMAIN, [UK_PRODUCT])]}))
    c = result.value.companies[0]
    assert (c.uk_evidence, c.uk_validation_warnings) == ([], [])


def test_parsing_path_invalid_core_fields_still_fail_after_repair():
    bad = json.dumps({"companies": [core(UK_DOMAIN, [UK_PRODUCT], company_type="shop", uk_evidence=[])]})
    rec = Recorder(completion(bad), completion(bad))
    client = or_client(rec)

    async def go():
        try:
            await client.complete_structured(system="s", user="u", output_model=ClassificationBatch,
                                             schema_name="company_classification")  # fmt: skip
        finally:
            await client.aclose()

    with pytest.raises(LLMOutputError, match="schema validation"):
        asyncio.run(go())
    assert len(rec.requests) == 2  # one repair attempt, exactly as before


def test_parsing_path_core_failure_then_valid_repair_keeps_uk_evidence():
    bad = json.dumps({"companies": [core(UK_DOMAIN, [UK_PRODUCT], relevance=3)]})
    good = json.dumps({"companies": [core(UK_DOMAIN, [UK_PRODUCT], uk_evidence=UK_QUOTES)]})
    result, _ = parse(bad, good)
    assert result.attempts == 2 and len(result.value.companies[0].uk_evidence) == 4


# --------------------------------------------------------------------------- approval with UK evidence


def _outcome(quotes):
    # Two discovery results (the existing evidence-URL rule) plus the profile pages.
    second = ev(f"https://{UK_DOMAIN}/products/boss-rc-1", "Boss RC-1 - Beckett's Music", "£99", model=False)
    c = classified(UK_DOMAIN, [*UK_EVIDENCE, second], quotes, company_name="Beckett's Music",
                   evidence_urls=[UK_PRODUCT, UK_ABOUT])  # fmt: skip
    decide(c, TH, ("amazon", "ebay"))
    classify_competitor(c, ("amazon", "ebay"))
    return c


def test_verified_uk_evidence_from_two_pages_auto_approves_without_a_benchmark():
    c = _outcome(UK_QUOTES)
    assert c.approval_status is S.AUTO_APPROVED and c.price_benchmark is False
    assert c.uk_seller.value == "yes" and c.competitor_class.value == "DIRECT_COMPETITOR"


def test_verified_uk_evidence_from_one_page_needs_review():
    c = _outcome(UK_ONE_PAGE)
    assert c.approval_status is S.APPROVAL_REQUIRED
    assert any("independent page(s) < 2" in r for r in c.decision_reasons)


@pytest.mark.parametrize("quotes", [
    [],  # no quotes
    [quote(UK_ABOUT, "Our flagship store on Oxford Street, London W1D 1BS", "UK_PHYSICAL_STORE")],  # invented
    5,  # malformed
])
def test_unverified_uk_evidence_blocks_auto_approval_but_never_benchmark_rules(quotes):
    c = _outcome(quotes)
    assert c.uk_seller.value == "unknown" and c.approval_status is S.APPROVAL_REQUIRED and not c.price_benchmark
    assert any("insufficient verified UK evidence" in r for r in c.decision_reasons)
    assert c.competitor_class.value == "NEEDS_REVIEW"  # UK selling not confirmed (existing safeguard)


def test_a_reviewer_decision_still_overrides_regardless_of_uk_evidence():
    c = classified(UK_DOMAIN, UK_EVIDENCE, UK_QUOTES)
    decide(c, TH)
    before = c.uk_assessment.model_copy(deep=True)
    apply_review_decision(c, decision(UK_DOMAIN, decision=ReviewDecision.REJECTED))
    assert c.approval_status is S.REJECTED and not c.price_benchmark
    assert c.uk_assessment == before  # recorded, unchanged, and no say in the decision


def test_marketplace_and_manufacturer_rules_are_unchanged_by_uk_evidence():
    for domain, ctype in (("ebay.co.uk", "retailer"), ("boss.info", "manufacturer")):
        ev_ = [ev(f"https://{domain}/boss-rc-5", "Boss RC-5", "£150"), *UK_EVIDENCE[1:]]
        c = classified(domain, ev_, UK_QUOTES, company_type=ctype)
        decide(c, TH, ("ebay",))
        assert c.approval_status is S.APPROVAL_REQUIRED and not c.price_benchmark


# --------------------------------------------------------------------------- evidence selection and prompts


def test_profile_evidence_is_kept_when_there_are_many_product_hits():
    products = [ev(f"https://{UK_DOMAIN}/p/{i}", f"Boss pedal {i}", "£99 in stock") for i in range(10)]
    profiles = [profile(f"https://{UK_DOMAIN}/pages/x{i}", "Page", "Nothing here") for i in range(2)]
    profiles += [profile(UK_ABOUT, "About us", "Visit our shop: 12 High Street, Bedford, MK40 1AB. UK delivery."),
                 profile(f"https://{UK_DOMAIN}/pages/delivery", "Delivery", "We deliver across the UK and ship "
                                                                            "to Ireland. Returns within 30 days.")]
    picked = prompts._pick_evidence(cand(UK_DOMAIN, products + profiles))
    assert len(picked) == 8
    kinds = [e.kind for e in picked]
    assert kinds.count(EvidenceKind.DOMAIN_PROFILE) == 3 and kinds.count(EvidenceKind.DISCOVERY_RESULT) == 5
    assert {UK_ABOUT, f"https://{UK_DOMAIN}/pages/delivery"} <= {e.url for e in picked}  # the most useful ones


def test_unused_slots_go_to_the_other_kind_and_empty_items_are_never_sent():
    few_products = [ev(f"https://{UK_DOMAIN}/p/{i}", f"Boss pedal {i}", "£99") for i in range(2)]
    many_profiles = [profile(f"https://{UK_DOMAIN}/pages/{i}", "About", "Our shop, address and delivery")
                     for i in range(7)]  # fmt: skip
    blank = [profile(f"https://{UK_DOMAIN}/pages/blank", None, "   ")]
    picked = prompts._pick_evidence(cand(UK_DOMAIN, few_products + many_profiles + blank))
    assert len(picked) == 8 and sum(e.kind is EvidenceKind.DOMAIN_PROFILE for e in picked) == 6
    assert all(e.url != f"https://{UK_DOMAIN}/pages/blank" for e in picked)
    only_products = [ev(f"https://{UK_DOMAIN}/p/{i}", f"Boss pedal {i}", "£99") for i in range(12)]
    assert len(prompts._pick_evidence(cand(UK_DOMAIN, only_products))) == 8


def test_snippet_lengths_sent_to_llm1_depend_on_the_evidence_kind():
    long = "x" * 600
    c = cand(UK_DOMAIN, [ev(UK_PRODUCT, "Boss RC-5", long), profile(UK_ABOUT, "About", long)])
    data = json.loads(prompts.classify_user([c], [], ["Effects"]).split("<untrusted_evidence>\n", 1)[1]
                      .rsplit("\n</untrusted_evidence>", 1)[0])  # fmt: skip
    lengths = {e["found_by"]: len(e["snippet"]) for e in data["domains"][0]["evidence"]}
    assert lengths == {"discovery_result": prompts.SNIPPET_CHARS, "domain_profile": prompts.PROFILE_SNIPPET_CHARS}
    assert (prompts.SNIPPET_CHARS, prompts.PROFILE_SNIPPET_CHARS, prompts.MAX_EVIDENCE_PER_CANDIDATE,
            prompts.MAX_PROFILE_EVIDENCE) == (280, 400, 8, 3)


def test_classify_prompt_asks_for_uk_evidence_and_no_verdict():
    p = prompts.CLASSIFY_SYSTEM
    for word in ("uk_evidence", "UK_PHYSICAL_STORE", "UK_ADDRESS", "UK_COMPANY_REGISTRATION", "UK_DELIVERY",
                 "NON_UK_OPERATOR", "NO_UK_SERVICE", "WEAK_SIGNAL"):
        assert word in p, word
    for gone in ("uk_relevance", "uk_confidence", "UK_BASED", "SERVES_UK_FROM_ABROAD", "NOT_SERVING_UK"):
        assert gone not in p, gone
    assert "up to 5 quotes" in p and "{max_uk_quotes}" not in p
    assert "Do not give a verdict" in p and "a business abroad that serves the UK is still relevant" in p
    assert "Prefer quotes from different evidence pages" in p
    assert "Code sets the final uk_seller from the verified uk_evidence" in p
    assert "GBP prices or a .uk domain on their own are not enough" in p  # uk_seller does not trust them
    # Existing instructions are still there.
    assert all(k in p for k in ("DIRECT_COMPETITOR", "RELATED_RETAILER", "NEEDS_REVIEW", "uk_seller: yes only if"))


def test_plan_prompt_targets_uk_shops_and_shops_abroad_that_deliver_to_the_uk():
    p = prompts.PLAN_SYSTEM.format(max_global=3, max_targeted=2)
    assert "shops based abroad that deliver to the UK" in p and "UK delivery" in p and "UK stockist" in p
    assert "Do not name specific shops" in p and "site:" in p  # still forbids operators


def test_profile_query_looks_for_uk_business_evidence_without_operators():
    q = PROFILE_QUERY.lower()
    assert all(w in q for w in ("about", "contact", "address", "showroom", "registered", "delivery", "uk"))
    assert "site:" not in q and len(PROFILE_QUERY) < 200


# --------------------------------------------------------------------------- agent end to end


def without_uk_evidence(batch):
    return ClassificationBatch(companies=[c.model_copy(update={"uk_evidence": []}) for c in batch.companies])


def agent_run(catalogue, classifications, llm=True):
    search = FakeSearch(GLOBAL_HITS, PROFILE_HITS)
    agent = CompetitorDiscoveryAgent(search, FakeLLM(PLAN, classifications) if llm else None, make_settings(),
                                     catalogue.competitors, [p.category for p in catalogue.products.values()])
    return asyncio.run(agent.run([catalogue.products["SB-001"]])), search


def test_agent_uses_verified_uk_evidence_and_changes_nothing_else(catalogue):
    with_uk, search = agent_run(catalogue, CLASSIFICATIONS)
    without_uk, _ = agent_run(catalogue, without_uk_evidence(CLASSIFICATIONS))
    a, b = ({c.domain: c for c in r.candidates} for r in (with_uk, without_uk))
    beck = a["beckettsmusic.co.uk"]
    assert beck.uk_assessment.sufficient and beck.uk_assessment.independent_sources == 2
    assert beck.approval_status is S.AUTO_APPROVED and beck.price_benchmark is False
    assert b["beckettsmusic.co.uk"].approval_status is S.APPROVAL_REQUIRED  # same answer without UK quotes
    for d in set(a) - {"beckettsmusic.co.uk"}:  # nobody else had UK quotes: outcomes are identical
        assert (a[d].approval_status, a[d].price_benchmark, a[d].competitor_class) == \
            (b[d].approval_status, b[d].price_benchmark, b[d].competitor_class), d
    assert any(q.text == PROFILE_QUERY and q.domains == ("beckettsmusic.co.uk",) for q in search.queries)


def test_agent_without_llm_records_no_uk_assessment_and_keeps_new_domains_pending(catalogue):
    result, search = agent_run(catalogue, CLASSIFICATIONS, llm=False)
    assert result.candidates and all(c.uk_assessment is None for c in result.candidates)
    assert all(c.classification_status is ClassificationStatus.SKIPPED_LLM_DISABLED for c in result.candidates)
    assert all(c.approval_status is S.APPROVAL_REQUIRED for c in result.candidates)
    assert any(q.text == PROFILE_QUERY for q in search.queries)  # profile evidence is still collected
    assert {t.approval_status for t in result.search_targets} == {S.PRECONFIGURED}  # configured path only


def test_classify_prompt_sent_by_the_agent_includes_profile_evidence(catalogue):
    search = FakeSearch(GLOBAL_HITS, PROFILE_HITS)
    llm = FakeLLM(PLAN, CLASSIFICATIONS)
    agent = CompetitorDiscoveryAgent(search, llm, make_settings(), catalogue.competitors,
                                     [p.category for p in catalogue.products.values()])
    asyncio.run(agent.run([catalogue.products["SB-001"]]))
    classify = next(call for call in llm.calls if call["schema"] == "company_classification")
    assert "Independent music shop in Bedford" in classify["user"] and '"found_by": "domain_profile"' in classify["user"]
    assert "uk_evidence" in classify["system"] and "uk_relevance" not in classify["system"]


# --------------------------------------------------------------------------- persistence and merging


def assessment(sufficient=True, sources=2):
    return UkEvidenceAssessment(sufficient=sufficient, independent_sources=sources, reasons=["test"])


def stored_candidate(status=ClassificationStatus.COMPLETED, uk=None):
    c = cand(UK_DOMAIN, UK_EVIDENCE)
    return c.model_copy(update={"classification_status": status, "uk_assessment": uk})


def test_no_llm_rerun_keeps_the_stored_uk_assessment():
    old = stored_candidate(uk=assessment())
    new = stored_candidate(ClassificationStatus.SKIPPED_LLM_DISABLED)
    assert merge_candidate(old, new).uk_assessment == assessment()


def test_failed_or_missing_classification_keeps_the_stored_uk_assessment():
    for status in (ClassificationStatus.FAILED, ClassificationStatus.MISSING):
        assert merge_candidate(stored_candidate(uk=assessment()), stored_candidate(status)).uk_assessment == assessment()


def test_new_completed_classification_replaces_the_uk_assessment():
    new_a = assessment(sufficient=False, sources=0)
    merged = merge_candidate(stored_candidate(uk=assessment()), stored_candidate(uk=new_a))
    assert merged.uk_assessment == new_a


def test_completed_classification_without_an_assessment_keeps_the_stored_one():
    assert merge_candidate(stored_candidate(uk=assessment()), stored_candidate(uk=None)).uk_assessment == assessment()


def test_store_round_trip_and_rerun_keep_assessment_and_decisions(tmp_path):
    store = ReviewStore(tmp_path / "review.sqlite3")
    store.record_run(run([stored_candidate(uk=assessment())], run_id="run-1"))
    store.create_decision(decision(UK_DOMAIN, benchmark_eligible=True))
    before = store.list_decisions()
    store.record_run(run([stored_candidate(ClassificationStatus.SKIPPED_LLM_DISABLED)], run_id="run-2"))
    s = store.get_candidate(UK_DOMAIN)
    assert s.times_seen == 2 and s.candidate.uk_assessment == assessment()
    assert store.list_decisions() == before  # decisions untouched by discovery runs


# --------------------------------------------------------------------------- discovery API


def test_discovery_api_returns_the_uk_assessment_additively():
    app = create_app(make_settings())

    def factory_dep():
        async def factory():
            cat = app.state.catalogue
            return CompetitorDiscoveryAgent(FakeSearch(GLOBAL_HITS, PROFILE_HITS), FakeLLM(PLAN, CLASSIFICATIONS),
                                            make_settings(), cat.competitors,
                                            [p.category for p in cat.products.values()])  # fmt: skip

        return factory

    app.dependency_overrides[get_agent_factory] = factory_dep
    with TestClient(app) as client:
        r = client.post("/api/discovery/runs", json={"product_ids": ["SB-001"]})
    assert r.status_code == 200
    cands = {c["domain"]: c for c in r.json()["candidates"]}
    beck = cands["beckettsmusic.co.uk"]
    assert beck["uk_assessment"]["sufficient"] is True and beck["uk_assessment"]["independent_sources"] == 2
    assert "relevance" not in beck["uk_assessment"] and "confidence" not in beck["uk_assessment"]
    assert beck["approval_status"] == "AUTO_APPROVED" and beck["uk_seller"] == "yes"  # existing fields unchanged
    assert beck["uk_review"]["sufficient"] is True
    assert cands["ebay.co.uk"]["uk_assessment"]["sufficient"] is False  # classified without UK quotes


# --------------------------------------------------------------------------- RawHit sanity (fixtures above)


def test_fixture_profile_hits_contain_the_quoted_text():
    about, delivery = PROFILE_HITS["beckettsmusic.co.uk"]
    hit: RawHit = about
    assert "Independent music shop in Bedford, MK40 1AB" in hit.snippet
    assert "We deliver to all UK mainland addresses" in delivery.snippet


# --------------------------------------------------------------------------- backfill stays class-fields-only


from tests.test_classification_backfill import ANSWERS, db_copy, records, source_db, without_class_fields  # noqa: E402,F401
from tests.test_classification_backfill import FakeLLM as BackfillLLM  # noqa: E402
from tests.test_classification_backfill import write as backfill_write  # noqa: E402


def with_uk_answer(answer):
    if not isinstance(answer, CompanyClassification):
        return answer
    return CompanyClassification.model_validate({
        **answer.model_dump(mode="json"),
        "uk_evidence": [quote(f"https://{answer.domain}/boss-rc-5", "Boss RC-5 Loop Station", "UK_PHYSICAL_STORE")],
    })  # fmt: skip


def test_backfill_with_uk_answers_still_writes_only_the_five_class_fields(catalogue, db_copy):
    old = records(db_copy)
    report = backfill_write(catalogue, db_copy, llm=BackfillLLM({d: with_uk_answer(a) for d, a in ANSWERS.items()}))
    new = records(db_copy)
    written = {e.domain for e in report.entries if e.write_status and e.write_status.value == "WRITTEN"}
    assert written == {"amazon.co.uk", "beckettsmusic.co.uk", "boss.info"}
    assert {d for d in new if "candidate" not in new[d]} == {"corrupt.co.uk"}  # the fixture's invalid row
    for d in (d for d in new if "candidate" in new[d]):
        assert without_class_fields(new[d]) == without_class_fields(old[d]), d
        assert new[d]["candidate"].get("uk_assessment") == old[d]["candidate"].get("uk_assessment"), d
    assert not any("UK answer" in n for e in report.entries for n in e.validation_notes)
