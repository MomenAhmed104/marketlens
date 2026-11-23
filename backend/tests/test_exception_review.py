"""Exception-based human review for discovered competitors (Stage 9.2, offline).

Strong evidence + an eligible retailer qualifies automatically; insufficient evidence or a
conflicting identity / business type goes to human review with the reason and its evidence;
exclusions, human decisions, benchmark separation and the search cap work as before. The rules
are industry-neutral: nothing here depends on musical instruments.
"""

from datetime import UTC, datetime

from app.agents.discovery.rules import (
    EligibilityPolicy,
    decide,
    find_identity_conflicts,
    is_search_eligible,
    non_retail_signals,
)
from app.agents.discovery.schemas import EvidenceKind
from app.agents.intelligence import eligible_search_targets
from app.schemas.common import DomainApprovalStatus as S
from app.schemas.competitor import CompanyType as T
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.review_store import ReviewStore, merge_candidate
from tests.search_fakes import make_settings
from tests.test_discovery_rules import CATS, candidate, cls, ev, profile_ev
from app.agents.discovery.rules import apply_classification

NOW = datetime(2026, 11, 23, tzinfo=UTC)
POLICY = EligibilityPolicy(require_profile_evidence=True)


def shop(domain="shop-example.co.uk", name="Shop Example", evidence=None, ctype=T.RETAILER):
    evidence = evidence if evidence is not None else [
        ev(f"https://{domain}/widget-x1", f"Widget X1 - {name}", "£49 In stock"),
        profile_ev(domain, name),
    ]
    c = candidate(domain, name, evidence=evidence)
    cited = [evidence[0].url] if evidence else []  # LLM 1 cites the first page of its evidence
    apply_classification(c, cls(domain=domain, company_name=name, company_type=ctype, evidence_urls=cited), CATS)
    return c


def decided(c, policy=POLICY):
    decide(c, policy)
    return c


# --------------------------------------------------------------------------- automatic qualification


def test_strong_evidence_and_an_eligible_retailer_qualify_automatically():
    c = decided(shop())
    assert c.approval_status is S.AUTO_APPROVED and c.price_benchmark is False
    assert "Automatically searchable" in c.decision_reasons[0]


def test_the_rules_are_industry_neutral():
    # A kitchenware retailer qualifies on the same evidence rules; nothing is instrument-specific.
    c = decided(shop("kitchen-example.co.uk", "Kitchen Example", evidence=[
        ev("https://kitchen-example.co.uk/cast-iron-pan-28", "Cast Iron Pan 28cm - Kitchen Example", "£39"),
        profile_ev("kitchen-example.co.uk", "Kitchen Example")]))  # fmt: skip
    assert c.approval_status is S.AUTO_APPROVED


# --------------------------------------------------------------------------- insufficient evidence


def test_no_product_evidence_needs_review_with_the_reason():
    c = decided(shop(evidence=[ev("https://shop-example.co.uk/news", "News - Shop Example", "Opening hours", model=False),
                               profile_ev("shop-example.co.uk", "Shop Example")]))  # fmt: skip
    assert c.approval_status is S.APPROVAL_REQUIRED
    assert any("insufficient evidence" in r and "names a searched product" in r for r in c.decision_reasons)


def test_no_business_profile_page_needs_review_when_discovery_looks_for_one():
    only_product = [ev("https://shop-example.co.uk/widget-x1", "Widget X1 - Shop Example", "£49")]
    c = decided(shop(evidence=only_product))
    assert c.approval_status is S.APPROVAL_REQUIRED
    assert any("no business-profile page" in r for r in c.decision_reasons)
    # Without a profile search configured, the same evidence is not "insufficient".
    assert decided(shop(evidence=list(only_product)), EligibilityPolicy()).approval_status is S.AUTO_APPROVED


# --------------------------------------------------------------------------- conflicts


def test_non_retail_wording_on_its_own_pages_needs_review_with_the_quote_and_url():
    for snippet, kind in (("We sell to trade customers only via our dealer network.", "trade-only seller"),
                          ("Find a dealer near you for our full range.", "points buyers to other sellers"),
                          ("Rather than selling product we show where to buy it.", "compares prices or does not sell"),
                          ("Join thousands of third-party sellers on our platform.", "hosts third-party sellers")):  # fmt: skip
        url = "https://shop-example.co.uk/about"
        c = decided(shop(evidence=[ev("https://shop-example.co.uk/widget-x1", "Widget X1 - Shop Example", "£49"),
                                   ev(url, "About - Shop Example", snippet, kind=EvidenceKind.DOMAIN_PROFILE,
                                      product=None, model=False)]))  # fmt: skip
        assert c.approval_status is S.APPROVAL_REQUIRED, snippet
        conflicts = [r for r in c.decision_reasons if "conflicting business type" in r]
        assert any(kind in r and url in r and '"' in r for r in conflicts), conflicts  # quote and evidence URL


def test_ordinary_retail_wording_is_not_a_conflict():
    c = shop(evidence=[ev("https://shop-example.co.uk/widget-x1", "Widget X1 - Shop Example", "£49"),
                       ev("https://shop-example.co.uk/about", "About us", "We are authorised dealers for 40 brands and "
                          "deliver across the UK.", kind=EvidenceKind.DOMAIN_PROFILE, product=None, model=False)])  # fmt: skip
    assert non_retail_signals(c) == [] and decided(c).approval_status is S.AUTO_APPROVED


def test_conflicting_identity_needs_review_and_survives_a_rerun_without_llm():
    a = shop("first-shop.co.uk", "Same Name Music")
    b = shop("second-shop.co.uk", "Same Name Music")
    c = shop("copy-andertons.com", "Andertons")
    find_identity_conflicts([a, b, c], {"andertons": "andertons.co.uk"})
    for x in (a, b, c):
        decided(x)
        assert x.approval_status is S.APPROVAL_REQUIRED
        assert any(r.startswith("Needs review: conflicting identity") for r in x.decision_reasons)
    assert "second-shop.co.uk" in a.identity_conflicts[0] and "andertons.co.uk" in c.identity_conflicts[0]
    # The conflict travels with the classification it came from (kept by a later no-LLM rerun).
    rerun = candidate("first-shop.co.uk", "Same Name Music")
    assert merge_candidate(a, rerun).identity_conflicts == a.identity_conflicts


def test_a_unique_name_has_no_identity_conflict():
    a = shop("first-shop.co.uk", "First Shop")
    find_identity_conflicts([a, shop("second-shop.co.uk", "Second Shop")], {"andertons": "andertons.co.uk"})
    assert a.identity_conflicts == [] and decided(a).approval_status is S.AUTO_APPROVED


# --------------------------------------------------------------------------- exclusions


def test_unsuitable_businesses_are_excluded_by_the_existing_rules():
    assert not is_search_eligible(shop(ctype=T.MARKETPLACE), None, POLICY).eligible
    assert not is_search_eligible(shop(ctype=T.DIRECTORY), None, POLICY).eligible
    assert not is_search_eligible(shop(ctype=T.MANUFACTURER), None, POLICY).eligible
    excluded = EligibilityPolicy(excluded_domains=frozenset({"shop-example.co.uk"}), require_profile_evidence=True)
    assert "excluded domain" in is_search_eligible(shop(), None, excluded).reasons
    market = EligibilityPolicy(marketplace_labels=("ebay",), require_profile_evidence=True)
    assert "known marketplace domain" in is_search_eligible(shop("ebay.co.uk", "eBay"), None, market).reasons


# --------------------------------------------------------------------------- human decisions, benchmark, cap, history


def decision(domain, kind=ReviewDecision.APPROVED, bench=False):
    return DomainDecisionRecord(domain=domain, decision=kind, company_type=T.RETAILER, benchmark_eligible=bench,
                                reviewer="sam", decided_at=NOW)  # fmt: skip


def targets(catalogue, decisions, candidates, **settings):
    s = make_settings(pipeline_search_auto_approved=True, **settings)
    return eligible_search_targets(catalogue.competitors, decisions, s, candidates=candidates)


def test_a_human_rejection_overrides_automatic_eligibility(catalogue):
    good = shop()
    assert is_search_eligible(good, None, POLICY).eligible
    assert not is_search_eligible(good, decision(good.domain, ReviewDecision.REJECTED), POLICY).eligible
    t, skipped = targets(catalogue, [decision(good.domain, ReviewDecision.REJECTED)], [good])
    assert good.domain not in {x.domain for x in t} and good.domain in {x.domain for x in skipped}


def test_a_human_approval_keeps_its_existing_behaviour_even_for_a_review_case(catalogue):
    conflicted = shop(evidence=[ev("https://shop-example.co.uk/widget-x1", "Widget X1", "£49")])  # no profile page
    decide(conflicted, POLICY)
    assert conflicted.approval_status is S.APPROVAL_REQUIRED
    t, _ = targets(catalogue, [decision(conflicted.domain, bench=True)], [conflicted])
    got = next(x for x in t if x.domain == conflicted.domain)
    assert got.approval_status is S.APPROVED and got.price_benchmark is True  # only the reviewer's explicit flag


def test_automatic_qualification_never_grants_benchmark_eligibility(catalogue):
    c = decided(shop())
    t, _ = targets(catalogue, [], [c])
    auto = [x for x in t if x.approval_status is S.AUTO_APPROVED]
    assert [x.domain for x in auto] == [c.domain] and auto[0].price_benchmark is False and c.price_benchmark is False


def test_the_per_run_cap_still_applies(catalogue):
    many = [decided(shop(f"shop{i}.co.uk", f"Shop {i}")) for i in range(4)]
    t, skipped = targets(catalogue, [], many, max_auto_search_targets=2)
    assert sum(x.approval_status is S.AUTO_APPROVED for x in t) == 2
    assert sum("per-run cap" in s.reason for s in skipped) == 2


def test_reviewer_history_is_untouched_by_automatic_eligibility(tmp_path, catalogue):
    store = ReviewStore(tmp_path / "review.sqlite3")
    store.create_decision(decision("shop-example.co.uk", ReviewDecision.REJECTED))
    before = (store.list_decisions(), store.history("shop-example.co.uk"))
    c = decided(shop())
    targets(catalogue, store.list_decisions(), [c])
    is_search_eligible(c, store.get_decision(c.domain), POLICY)
    assert (store.list_decisions(), store.history("shop-example.co.uk")) == before


def test_every_review_outcome_names_the_evidence_behind_it():
    c = decided(shop(ctype=T.MANUFACTURER))  # an existing exclusion reason, not only the new ones
    assert c.approval_status is S.APPROVAL_REQUIRED
    assert all(r.startswith("Needs review: ") for r in c.decision_reasons)
    assert c.decision_reasons[-1].startswith("Needs review: evidence reviewed: https://shop-example.co.uk/widget-x1")
    assert not any("evidence reviewed" in r for r in decided(shop()).decision_reasons)  # qualified: no review line
