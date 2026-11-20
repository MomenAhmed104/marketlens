from datetime import UTC, datetime

import pytest

from app.agents.discovery.rules import Thresholds, apply_classification, decide, name_in_evidence, uk_signals
from app.agents.discovery.schemas import (
    CompanyClassification,
    DiscoveredCompetitor,
    EvidenceItem,
    EvidenceKind,
    UkRelevanceEvidenceKind as K,
    UkRelevanceQuote,
    UkSeller,
)
from app.schemas.common import DomainApprovalStatus as S
from app.schemas.competitor import CompanyType as T
from app.services.domains import registrable_domain

NOW = datetime(2026, 11, 10, tzinfo=UTC)
TH = Thresholds(0.85, 0.75, 2, 0.3)


def ev(url, title=None, snippet=None, kind=EvidenceKind.DISCOVERY_RESULT, product="SB-001", model=True):
    host = url.split("/")[2]
    return EvidenceItem(url=url, canonical_url=url, hostname=host, title=title, snippet=snippet, kind=kind,
                        query_text="q", product_id=product, provider="fake", provider_request_id="r",
                        retrieved_at=NOW, mentions_product_model=model)  # fmt: skip


def uk_pages(domain="beckettsmusic.co.uk"):
    """Profile pages holding the verified UK evidence that `uk_quotes` cites (two different pages)."""
    return [ev(f"https://{domain}/about", "About Beckett's Music", "Visit our shop at 12 High Street, Bedford MK40 1AB",
               kind=EvidenceKind.DOMAIN_PROFILE, model=False),
            ev(f"https://{domain}/delivery", "Delivery", "We deliver to all UK mainland addresses",
               kind=EvidenceKind.DOMAIN_PROFILE, model=False)]  # fmt: skip


def candidate(domain="beckettsmusic.co.uk", evidence=None, products=("SB-001",)):
    evidence = evidence or [
        ev(f"https://{domain}/boss-rc-5", "Boss RC-5 Loop Station - Beckett's Music", "£189.99 In stock"),
        ev(f"https://{domain}/boss-rc-1", "Boss RC-1 - Beckett's Music", "£99", model=False),
        *uk_pages(domain),
    ]
    c = DiscoveredCompetitor(domain=domain, hostnames=[domain], homepage_url=f"https://{domain}/",
                             evidence=evidence, products_matched=list(products), uk_signals=[],
                             discovered_at=NOW)  # fmt: skip
    c.uk_signals = uk_signals(domain, c.evidence)
    return c


def uk_quotes(domain="beckettsmusic.co.uk"):
    """Verified UK evidence from two different pages: a UK address and UK delivery."""
    return [UkRelevanceQuote(url=f"https://{domain}/about", quote="12 High Street, Bedford MK40 1AB", kind=K.UK_ADDRESS),
            UkRelevanceQuote(url=f"https://{domain}/delivery", quote="We deliver to all UK mainland addresses",
                             kind=K.UK_DELIVERY)]  # fmt: skip


def cls(domain="beckettsmusic.co.uk", **kw):
    base = dict(domain=domain, company_name="Beckett's Music", company_type=T.SPECIALIST_RETAILER,
                relevant_categories=["Effects"], uk_seller=UkSeller.YES, sells_new_musical_instruments=True,
                relevance=0.9, confidence=0.9, rationale="Sells Boss pedals in GBP.",
                evidence_urls=[f"https://{domain}/boss-rc-5"], uk_evidence=uk_quotes(domain))  # fmt: skip
    base.update(kw)
    return CompanyClassification(**base)


def decided(c_kw=None, cand=None):
    c = cand or candidate()
    apply_classification(c, cls(**(c_kw or {})), {"Effects", "Guitars", "Amplification", "Accessories"})
    decide(c, TH)
    return c


# --------------------------------------------------------------------------- signals


def test_uk_signals():
    assert uk_signals("shop.co.uk", []) == ["uk_domain"]
    items = [ev("https://x.com/a", "Boss RC-5", "£189 delivered to SW1A 1AA, United Kingdom")]
    assert uk_signals("x.com", items) == ["gbp_price", "uk_postcode", "uk_mention"]
    assert uk_signals("x.com", [ev("https://x.com/a", "Boss RC-5", "$189 USA")]) == []


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


# --------------------------------------------------------------------------- applying LLM output


def test_apply_discards_unsupported_claims():
    c = candidate()
    apply_classification(c, cls(evidence_urls=["https://beckettsmusic.co.uk/boss-rc-5", "https://invented.example/x"],
                                relevant_categories=["Effects", "Drones"], rationale="r" * 2000),
                         {"Effects"})  # fmt: skip
    assert c.cited_evidence_urls == ["https://beckettsmusic.co.uk/boss-rc-5"]
    assert c.relevant_categories == ["Effects"]
    assert len(c.rationale) == 600
    assert any("not present in the evidence" in n for n in c.validation_notes)
    assert c.name_verified


# --------------------------------------------------------------------------- decisions


def test_auto_approve_when_every_criterion_is_met():
    c = decided()
    # Auto-approval never grants benchmark permission; only a reviewer's explicit flag does.
    assert c.approval_status is S.AUTO_APPROVED and c.price_benchmark is False
    assert c.decision_reasons[0].startswith("Auto-approved")


@pytest.mark.parametrize(
    "overrides, expected_reason",
    [
        ({"company_type": T.MARKETPLACE}, "type is marketplace"),
        ({"company_type": T.MANUFACTURER}, "type is manufacturer"),
        ({"confidence": 0.6}, "confidence 0.60 < 0.85"),
        ({"relevance": 0.5}, "relevance 0.50 < 0.75"),
        ({"company_name": "Totally Invented Ltd"}, "company name not verified"),
        ({"company_name": None}, "company name not verified"),
        ({"uk_seller": UkSeller.UNKNOWN}, "UK selling is 'unknown'"),
        ({"evidence_urls": ["https://elsewhere.example/x"]}, "cited no valid evidence URL"),
        ({"sells_new_musical_instruments": False}, "does not sell new instruments"),
    ],
)
def test_needs_approval_when_a_criterion_fails(overrides, expected_reason):
    c = decided(overrides)
    assert c.approval_status is S.APPROVAL_REQUIRED and c.price_benchmark is False
    assert any(expected_reason in r for r in c.decision_reasons), c.decision_reasons


def test_insufficient_uk_evidence_or_single_url_or_no_product_needs_approval():
    weak = candidate("shop.example.com", evidence=[
        ev("https://shop.example.com/rc5", "Boss RC-5 - Example Shop", "ships to UK"),
        ev("https://shop.example.com/rc1", "Boss RC-1 - Example Shop", "UK delivery", model=False)])  # fmt: skip
    c = decided({"domain": "shop.example.com", "company_name": "Example Shop",
                 "evidence_urls": ["https://shop.example.com/rc5"], "uk_evidence": []}, weak)  # fmt: skip
    assert c.approval_status is S.APPROVAL_REQUIRED
    assert any("insufficient verified UK evidence" in r for r in c.decision_reasons)

    single = candidate(evidence=[ev("https://beckettsmusic.co.uk/boss-rc-5", "Boss RC-5 - Beckett's Music", "£189")])
    assert decided(cand=single).approval_status is S.APPROVAL_REQUIRED

    no_product = candidate(products=())
    c = decided(cand=no_product)
    assert any("no page mentioning a selected product" in r for r in c.decision_reasons)


def test_rejections():
    c = decided({"company_type": T.OTHER, "relevance": 0.1, "confidence": 0.9})
    assert c.approval_status is S.REJECTED and "Not a shop" in c.decision_reasons[0]

    # LLM 1 says it does not sell to the UK, but no verified quote says so: never rejected on
    # an unverified claim. uk_seller is reconciled to unknown and a person decides.
    us = candidate("usshop.com", evidence=[ev("https://usshop.com/rc5", "Boss RC-5 - US Shop", "$189")])
    c = decided({"domain": "usshop.com", "uk_seller": UkSeller.NO, "company_name": "US Shop",
                 "evidence_urls": ["https://usshop.com/rc5"], "uk_evidence": []}, us)  # fmt: skip
    assert c.approval_status is S.APPROVAL_REQUIRED and c.uk_seller is UkSeller.UNKNOWN

    # Says "no" while the verified evidence shows UK relevance: conflicting -> a person decides.
    c = decided({"uk_seller": UkSeller.NO})
    assert c.approval_status is S.APPROVAL_REQUIRED

    irrelevant = candidate(products=())
    assert decided({"relevance": 0.2}, irrelevant).approval_status is S.REJECTED


def test_unclassified_and_unevidenced_candidates():
    from app.agents.discovery.schemas import ClassificationStatus as CS

    for status, text in [(CS.MISSING, "LLM returned no classification"), (CS.FAILED, "LLM classification failed"),
                         (CS.SKIPPED_LLM_DISABLED, "LLM classification skipped (LLM disabled)")]:  # fmt: skip
        c = candidate()
        c.classification_status = status
        decide(c, TH)
        assert c.approval_status is S.APPROVAL_REQUIRED and c.decision_reasons[0].startswith(text)
        assert c.price_benchmark is False

    profile_only = candidate(evidence=[ev("https://beckettsmusic.co.uk/about", "About", kind=EvidenceKind.DOMAIN_PROFILE)])
    decide(profile_only, TH)
    assert profile_only.approval_status is S.REJECTED
