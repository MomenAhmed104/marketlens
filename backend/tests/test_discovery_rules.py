"""Deterministic discovery rules (Stage 8.1): basic validation and automatic search eligibility.

There is no UK verification: `serves_uk_customers`, relevance and confidence are informational
and never change eligibility, approval or exclusion.
"""

from datetime import UTC, datetime

import pytest

from app.agents.discovery.rules import (
    EligibilityPolicy,
    apply_classification,
    decide,
    is_search_eligible,
    name_in_evidence,
    policy_from_settings,
)
from app.agents.discovery.schemas import (
    CLASSIFICATION_VERSION,
    ClassificationStatus as CS,
    CompanyClassification,
    DiscoveredCompetitor,
    EvidenceItem,
    EvidenceKind,
    UkSeller,
)
from app.schemas.common import DomainApprovalStatus as S
from app.schemas.competitor import CompanyType as T
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.domains import registrable_domain
from tests.search_fakes import make_settings

NOW = datetime(2026, 11, 10, tzinfo=UTC)
POLICY = policy_from_settings(make_settings())
CATS = {"Effects", "Guitars", "Amplification", "Accessories"}


def ev(url, title=None, snippet=None, kind=EvidenceKind.DISCOVERY_RESULT, product="SB-001", model=True):
    host = url.split("/")[2]
    return EvidenceItem(url=url, canonical_url=url, hostname=host, title=title, snippet=snippet, kind=kind,
                        query_text="q", product_id=product, provider="fake", provider_request_id="r",
                        retrieved_at=NOW, mentions_product_model=model)  # fmt: skip


def profile_ev(domain, name="the shop"):
    """A business-profile page, as the discovery profile search returns for real candidates."""
    return ev(f"https://{domain}/about-us", f"About us - {name}", "Independent shop.", kind=EvidenceKind.DOMAIN_PROFILE,
              product=None, model=False)  # fmt: skip


def candidate(domain="beckettsmusic.co.uk", name="Beckett's Music", evidence=None, products=("SB-001",), profile=True):
    if evidence is None:  # the default record has what real discovery returns, a profile page included
        evidence = [
            ev(f"https://{domain}/boss-rc-5", f"Boss RC-5 Loop Station - {name}", "£189.99 In stock"),
            ev(f"https://{domain}/boss-rc-1", f"Boss RC-1 - {name}", "£99", model=False),
        ] + ([profile_ev(domain)] if profile else [])
    return DiscoveredCompetitor(domain=domain, hostnames=[domain], homepage_url=f"https://{domain}/",
                                evidence=evidence, products_matched=list(products), discovered_at=NOW)  # fmt: skip


def cls(domain="beckettsmusic.co.uk", **kw):
    base = dict(domain=domain, company_name="Beckett's Music", company_type=T.SPECIALIST_RETAILER,
                relevant_categories=["Effects"], serves_uk_customers=UkSeller.YES, sells_new_musical_instruments=True,
                relevance=0.9, confidence=0.9, rationale="Sells Boss pedals.",
                evidence_urls=[f"https://{domain}/boss-rc-5"])  # fmt: skip
    base.update(kw)
    return CompanyClassification(**base)


def classified(c_kw=None, cand=None):
    c = cand or candidate()
    apply_classification(c, cls(**(c_kw or {})), CATS)
    return c


def decided(c_kw=None, cand=None):
    c = classified(c_kw, cand)
    decide(c, POLICY)
    return c


def rejection(domain="beckettsmusic.co.uk"):
    return DomainDecisionRecord(domain=domain, decision=ReviewDecision.REJECTED, reviewer="alex", decided_at=NOW)


# --------------------------------------------------------------------------- basic validation (kept)


def test_name_verification_needs_evidence_text_not_hostname():
    items = [ev("https://beckettsmusic.co.uk/a", "Boss RC-5 - Beckett's Music")]
    assert name_in_evidence("Beckett's Music", items)
    assert name_in_evidence("BECKETTS MUSIC", items)
    # The hostname contains "beckettsmusic" but titles don't name it: not verified.
    assert not name_in_evidence("Beckett's Music", [ev("https://beckettsmusic.co.uk/a", "Boss RC-5", "£189")])
    assert not name_in_evidence("Bo", items)


def test_registrable_domain_groups_subdomains():
    assert registrable_domain("https://blog.andertons.co.uk/x") == "andertons.co.uk"
    assert registrable_domain("www.thomann.co.uk") == "thomann.co.uk"
    assert registrable_domain("shop.gibson.com") == "gibson.com"
    assert registrable_domain("bromoguitars.com.au") == "bromoguitars.com.au"


def test_apply_discards_unsupported_claims_and_records_the_version():
    c = candidate()
    apply_classification(c, cls(evidence_urls=["https://beckettsmusic.co.uk/boss-rc-5", "https://invented.example/x"],
                                relevant_categories=["Effects", "Drones"], rationale="r" * 2000,
                                serves_uk_customers_note="n" * 900),
                         {"Effects"})  # fmt: skip
    assert c.cited_evidence_urls == ["https://beckettsmusic.co.uk/boss-rc-5"]
    assert c.relevant_categories == ["Effects"]
    assert len(c.rationale) == 600 and len(c.serves_uk_customers_note) == 300
    assert any("not present in the evidence" in n for n in c.validation_notes)
    assert c.name_verified and c.classification_version == CLASSIFICATION_VERSION


def test_old_uk_seller_key_still_loads_into_the_informational_field():
    assert CompanyClassification.model_validate({**cls().model_dump(exclude={"serves_uk_customers"}),
                                                 "uk_seller": "no"}).serves_uk_customers is UkSeller.NO  # fmt: skip
    stored = candidate().model_dump(mode="json", exclude={"serves_uk_customers"}) | {"uk_seller": "yes", "uk_signals": ["uk_domain"],
                                                    "uk_assessment": {"sufficient": True}}  # fmt: skip
    c = DiscoveredCompetitor.model_validate(stored)
    assert c.serves_uk_customers is UkSeller.YES and c.classification_version is None
    assert "uk_assessment" not in c.model_dump() and "uk_signals" not in c.model_dump()


def test_strict_schema_has_no_uk_evidence_and_an_informational_uk_answer():
    from app.services.llm import strict_json_schema
    from app.agents.discovery.schemas import ClassificationBatch

    schema = strict_json_schema(ClassificationBatch)
    item = schema["$defs"]["CompanyClassification"]
    assert "uk_evidence" not in item["properties"] and "uk_seller" not in item["properties"]
    assert {"serves_uk_customers", "serves_uk_customers_note"} <= set(item["required"])


# --------------------------------------------------------------------------- automatic search eligibility


@pytest.mark.parametrize("ctype", [T.RETAILER, T.SPECIALIST_RETAILER])
def test_retailers_are_eligible(ctype):
    c = classified({"company_type": ctype})
    e = is_search_eligible(c, None, POLICY)
    assert e.eligible and e.reasons[0].startswith("Automatically searchable")


@pytest.mark.parametrize("ctype", [T.MANUFACTURER, T.MARKETPLACE, T.DIRECTORY, T.OTHER])
def test_other_types_are_never_eligible(ctype):
    e = is_search_eligible(classified({"company_type": ctype}), None, POLICY)
    assert not e.eligible and any(f"type is {ctype.value}" in r for r in e.reasons)


@pytest.mark.parametrize("status", [CS.MISSING, CS.FAILED, CS.SKIPPED_LLM_DISABLED])
def test_missing_classification_is_never_eligible(status):
    c = candidate()
    c.classification_status = status
    e = is_search_eligible(c, None, POLICY)
    assert not e.eligible and any("no completed LLM 1 classification" in r for r in e.reasons)


def test_unknown_type_is_never_eligible():
    c = classified()
    c.company_type = None
    assert not is_search_eligible(c, None, POLICY).eligible


def test_classification_before_stage_8_1_needs_rediscovery():
    c = classified()
    c.classification_version = None  # as stored by every earlier run and the backfill
    e = is_search_eligible(c, None, POLICY)
    assert not e.eligible and any("needs rediscovery" in r for r in e.reasons)


@pytest.mark.parametrize("domain", ["Beckettsmusic.co.uk", "shop.beckettsmusic.co.uk", "becketts music.co.uk",
                                    "https://beckettsmusic.co.uk/", "nodot"])
def test_invalid_or_unnormalised_domain_is_never_eligible(domain):
    c = classified()
    c.domain = domain
    e = is_search_eligible(c, None, POLICY)
    assert not e.eligible and any("not a valid normalised domain" in r for r in e.reasons)


def test_company_name_must_be_in_the_evidence():
    for kw in ({"company_name": "Totally Invented Ltd"}, {"company_name": None}):
        e = is_search_eligible(classified(kw), None, POLICY)
        assert not e.eligible and any("company name not found" in r for r in e.reasons)


def test_a_cited_url_must_be_in_the_evidence():
    e = is_search_eligible(classified({"evidence_urls": ["https://elsewhere.example/x"]}), None, POLICY)
    assert not e.eligible and any("no cited URL" in r for r in e.reasons)
    e = is_search_eligible(classified({"evidence_urls": []}), None, POLICY)
    assert not e.eligible


def test_excluded_and_marketplace_domains_are_never_eligible():
    for domain in ("pmtonline.co.uk", "stompbox.co.uk"):
        c = classified({"domain": domain, "company_name": "Shop"}, candidate(domain, "Shop"))
        e = is_search_eligible(c, None, POLICY)
        assert not e.eligible and "excluded domain" in e.reasons
    c = classified({"domain": "ebay.co.uk", "company_name": "eBay", "company_type": T.RETAILER},
                   candidate("ebay.co.uk", "eBay"))  # fmt: skip
    e = is_search_eligible(c, None, POLICY)
    assert not e.eligible and "known marketplace domain" in e.reasons


def test_a_human_rejection_always_wins():
    c = classified()
    assert is_search_eligible(c, None, POLICY).eligible
    e = is_search_eligible(c, rejection(), POLICY)
    assert not e.eligible and e.reasons == ["rejected by a reviewer"]


def test_international_retailer_qualifies_without_uk_domain_gbp_or_address():
    c = candidate("musikhaus-example.de", "Musikhaus Example", evidence=[
        ev("https://musikhaus-example.de/boss-rc-5", "Boss RC-5 - Musikhaus Example", "EUR 179, ships worldwide"),
        profile_ev("musikhaus-example.de", "Musikhaus Example")])
    c = classified({"domain": "musikhaus-example.de", "company_name": "Musikhaus Example",
                    "serves_uk_customers": UkSeller.UNKNOWN,
                    "evidence_urls": ["https://musikhaus-example.de/boss-rc-5"]}, c)  # fmt: skip
    assert is_search_eligible(c, None, POLICY).eligible


@pytest.mark.parametrize("answer", list(UkSeller))
def test_serves_uk_customers_never_affects_eligibility(answer):
    assert is_search_eligible(classified({"serves_uk_customers": answer}), None, POLICY).eligible


@pytest.mark.parametrize("kw", [{"relevance": 0.0, "confidence": 0.0}, {"relevance": 0.01}, {"confidence": 0.05}])
def test_scores_never_affect_eligibility(kw):
    assert is_search_eligible(classified(kw), None, POLICY).eligible


# --------------------------------------------------------------------------- used-only retailers


@pytest.mark.parametrize("ctype", [T.RETAILER, T.SPECIALIST_RETAILER])
def test_a_used_only_retailer_is_never_searched_automatically(ctype):
    c = decided({"company_type": ctype, "sells_new_musical_instruments": False})
    e = is_search_eligible(c, None, POLICY)
    assert not e.eligible and any("sells only used instruments" in r for r in e.reasons)
    assert c.approval_status is S.APPROVAL_REQUIRED and c.price_benchmark is False
    assert any("sells only used instruments" in r for r in c.decision_reasons)


def test_new_and_used_retailer_qualifies():
    assert is_search_eligible(classified({"sells_new_musical_instruments": True}), None, POLICY).eligible


def test_unknown_new_instrument_status_never_blocks_on_its_own():
    c = classified({"sells_new_musical_instruments": None})
    assert c.sells_new_musical_instruments is None and is_search_eligible(c, None, POLICY).eligible


def test_unknown_status_still_needs_every_other_rule():
    c = classified({"sells_new_musical_instruments": None, "company_type": T.MANUFACTURER})
    assert not is_search_eligible(c, None, POLICY).eligible


def test_a_used_only_retailer_can_be_approved_without_benchmark_permission():
    from app.agents.discovery.rules import apply_review_decision

    for bench in (False, True):
        c = decided({"sells_new_musical_instruments": False})
        apply_review_decision(c, DomainDecisionRecord(domain=c.domain, decision=ReviewDecision.APPROVED,
                                                      company_type=T.SPECIALIST_RETAILER, benchmark_eligible=bench,
                                                      reviewer="sam", decided_at=NOW))  # fmt: skip
        assert c.approval_status is S.APPROVED and c.price_benchmark is bench  # only the explicit flag


def test_classification_contract_accepts_null_for_new_instruments():
    assert cls(sells_new_musical_instruments=None).sells_new_musical_instruments is None


def test_eligibility_is_deterministic_and_does_not_mutate():
    c = classified()
    before = c.model_dump()
    assert is_search_eligible(c, None, POLICY) == is_search_eligible(c, None, POLICY)
    assert c.model_dump() == before


def test_default_policy_excludes_nothing_but_still_validates():
    c = classified()
    assert is_search_eligible(c, None, EligibilityPolicy()).eligible


# --------------------------------------------------------------------------- run decision


def test_eligible_candidate_is_auto_approved_without_benchmark_permission():
    c = decided()
    assert c.approval_status is S.AUTO_APPROVED and c.price_benchmark is False
    assert c.decision_reasons[0].startswith("Automatically searchable")


def test_ineligible_candidate_needs_review_with_every_reason():
    c = decided({"company_type": T.MANUFACTURER, "company_name": "Invented"})
    assert c.approval_status is S.APPROVAL_REQUIRED and c.price_benchmark is False
    assert any("type is manufacturer" in r for r in c.decision_reasons)
    assert any("company name not found" in r for r in c.decision_reasons)
    assert all(r.startswith("Needs review: ") for r in c.decision_reasons)


def test_low_relevance_is_never_rejected_automatically():
    c = decided({"company_type": T.OTHER, "relevance": 0.05}, candidate(products=()))
    assert c.approval_status is S.APPROVAL_REQUIRED


def test_unclassified_and_unevidenced_candidates():
    for status, text in [(CS.MISSING, "LLM returned no classification"), (CS.FAILED, "LLM classification failed"),
                         (CS.SKIPPED_LLM_DISABLED, "LLM classification skipped (LLM disabled)")]:  # fmt: skip
        c = candidate()
        c.classification_status = status
        decide(c, POLICY)
        assert c.approval_status is S.APPROVAL_REQUIRED and c.price_benchmark is False
        assert c.decision_reasons[0].startswith(text) and "not searched automatically" in c.decision_reasons[0]

    profile_only = candidate(evidence=[ev("https://beckettsmusic.co.uk/about", "About Beckett's Music",
                                          kind=EvidenceKind.DOMAIN_PROFILE)])  # fmt: skip
    decide(profile_only, POLICY)
    assert profile_only.approval_status is S.REJECTED and profile_only.decision_reasons == ["No discovery evidence"]


def test_one_discovery_page_is_enough():
    """The former 2-discovery-URL and 2-independent-UK-page requirements are gone."""
    single = candidate(evidence=[ev("https://beckettsmusic.co.uk/boss-rc-5", "Boss RC-5 - Beckett's Music", "£189"),
                                 profile_ev("beckettsmusic.co.uk")])  # one product page + its profile page
    assert decided(cand=single).approval_status is S.AUTO_APPROVED
