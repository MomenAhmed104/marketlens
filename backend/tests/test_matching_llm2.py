"""Python's final decisions with LLM 2 input (Stage 4) and the Task C view (Stage 5).

Approved decisions: EQUIVALENT_VARIANT only for a cosmetic finish/colour difference
(U-1); LLM 2 disagreement can only give REVIEW_REQUIRED (U-4); direct vs
alternative-product prices kept apart (D-28, D-32). No network, no LLM.
"""

from decimal import Decimal

import pytest

from app.agents.intelligence.schemas import (
    PROPOSAL_STATUS,
    IssueKind,
    IssueRecord,
    MatchProposalRecord,
    ProposalStatus,
    ProposedMatch,
    VerifiedDifference,
    VerifiedReason,
)
from app.schemas.common import AvailabilityStatus, DomainApprovalStatus
from app.schemas.common import MatchStatus as M
from app.schemas.competitor import CompanyType as T
from app.services.matching import match_candidate
from app.services.matching.matcher import evaluate_product
from app.services.matching.schemas import CheckResult, Condition, PriceKind, ProposalAgreement
from tests.matching_fakes import NOW, candidate, target

CORT_BLACK = "https://www.thomann.co.uk/cort-g250-se-black"
CORT_BURGUNDY = "https://www.thomann.co.uk/cort-g250-se-vivid-burgundy"
AND_RC5 = "https://www.andertons.co.uk/boss-rc-5-loop-station-pedal"


@pytest.fixture
def run(catalogue):
    def _run(pid, cand):
        return match_candidate(catalogue.products[pid], cand, NOW, 24)

    return _run


def cort_black(**kw):
    kw.setdefault("llm", True)
    return candidate(CORT_BLACK, "Cort G250 SE Electric Guitar Black", product_title="Cort G250 SE Electric Guitar Black",
                     brand="Cort", model="G250 SE", **kw)  # fmt: skip


def rc5(**kw):
    return candidate(AND_RC5, "Boss RC-5 Loop Station", llm=True, product_title="Boss RC-5 Loop Station",
                     brand="BOSS", model="RC-5", price=kw.pop("price", "189.00"), quote="£189.00 In stock", **kw)  # fmt: skip


def with_proposal(c, classification, reasons=(("reason", "Boss RC-5 Loop Station"),), diffs=(),
                  status=ProposalStatus.COMPLETED):  # fmt: skip
    c.match_proposal = MatchProposalRecord(
        classification=classification, corresponding_status=PROPOSAL_STATUS[classification],
        reasons=[VerifiedReason(text=t, quote=q, verified=True) for t, q in reasons],
        differing_attributes=[VerifiedDifference(attribute=a, catalogue_value=cv, page_value=pv, quote=q,
                                                 verified=True, cosmetic=a == "finish") for a, cv, pv, q in diffs],
    )  # fmt: skip
    c.match_proposal_status = status
    return c


def variant(m, attr):
    return next(v for v in m.variants if v.attribute == attr)


# --------------------------------------------------------------------------- EQUIVALENT_VARIANT (U-1)


def test_finish_only_difference_on_otherwise_identical_product_is_equivalent_variant(run):
    m = run("SB-021", cort_black(price="269.00", quote="£269.00"))
    assert m.match_status is M.EQUIVALENT_VARIANT and variant(m, "finish").result is CheckResult.CONFLICT
    assert any("only the cosmetic finish" in r for r in m.match_reasons)
    # Not a direct benchmark: an alternative-product price, no difference.
    assert m.comparison.eligible is False and m.comparison.difference_gbp is None
    assert "Match status is EQUIVALENT_VARIANT, not EXACT_MATCH" in m.comparison.reasons
    assert m.price_kind is PriceKind.ALTERNATIVE and m.alternative_price.shown
    assert m.alternative_price.price == Decimal("269.00") and m.alternative_price.benchmark_eligible is False


def test_exact_finish_stays_exact(run):
    m = run("SB-021", candidate(CORT_BURGUNDY, "Cort G250 SE Electric Guitar Vivid Burgundy"))
    assert m.match_status is M.EXACT_MATCH


@pytest.mark.parametrize("pid,url,title,attr", [
    ("SB-021", "https://www.thomann.co.uk/cort-g250-se-lh-vivid-burgundy", "Cort G250 SE LH Vivid Burgundy", "handedness"),
    ("SB-021", "https://www.thomann.co.uk/cort-g250-se-lh-black", "Cort G250 SE LH Black", "handedness"),
    ("SB-001", "https://www.andertons.co.uk/boss-rc-5-loop-station-bundle", "Boss RC-5 Loop Station Bundle", "bundle"),
])  # fmt: skip
def test_non_cosmetic_differences_never_give_equivalent_variant(run, pid, url, title, attr):
    m = run(pid, candidate(url, title))
    assert m.match_status is M.SIMILAR_MATCH and variant(m, attr).result is CheckResult.CONFLICT


def test_different_model_of_the_same_family_is_not_equivalent(run):
    m = run("SB-001", candidate("https://www.andertons.co.uk/boss-rc-500-loop-station", "Boss RC-500 Loop Station"))
    assert m.match_status in (M.SIMILAR_MATCH, M.NO_MATCH) and m.match_status is not M.EQUIVALENT_VARIANT


def test_finish_difference_with_unresolved_identity_is_review_not_equivalent(run):
    # Left-handed product; a right/unstated-handed listing in another finish: handedness unconfirmed.
    m = run("SB-028", candidate("https://www.andertons.co.uk/epiphone-les-paul-standard-60s-ebony",
                                "Epiphone Les Paul Standard '60s, Ebony"))  # fmt: skip
    assert m.match_status is M.REVIEW_REQUIRED
    assert any("equivalent variant not established" in r for r in m.review_reasons)


def test_used_item_in_another_finish_is_similar_with_condition_label(run):
    used = candidate("https://www.thomann.co.uk/cort-g250-se-black-used", "Used Cort G250 SE Electric Guitar Black",
                     llm=True, product_title="Used Cort G250 SE Electric Guitar Black", brand="Cort", model="G250 SE",
                     price="199.00", quote="£199.00")  # fmt: skip
    m = run("SB-021", used)
    assert m.match_status is M.SIMILAR_MATCH and m.condition.condition is Condition.USED
    assert m.price_kind is PriceKind.ALTERNATIVE and m.alternative_price.condition_label == "Used"
    assert m.alternative_price.price == Decimal("199.00")


# --------------------------------------------------------------------------- reconciliation (P-4, U-4)


def test_agreeing_proposal_keeps_exact_and_direct_price(run):
    m = run("SB-001", with_proposal(rc5(), ProposedMatch.EXACT_MATCH))
    assert m.match_status is M.EXACT_MATCH and m.proposal_agreement is ProposalAgreement.AGREES
    assert m.proposed_match is ProposedMatch.EXACT_MATCH and m.price_kind is PriceKind.DIRECT
    assert m.comparison.eligible and m.intelligence.price.difference_gbp == Decimal("189.00") - Decimal("189.99")


@pytest.mark.parametrize("proposed", [ProposedMatch.EQUIVALENT_VARIANT, ProposedMatch.SIMILAR_ALTERNATIVE,
                                      ProposedMatch.NOT_A_MATCH])  # fmt: skip
def test_more_cautious_proposal_turns_exact_into_review_only(run, proposed):
    m = run("SB-001", with_proposal(rc5(), proposed, reasons=[("Different version", "£189.00 In stock")]))
    assert m.match_status is M.REVIEW_REQUIRED  # never SIMILAR_MATCH / NO_MATCH directly
    assert m.proposal_agreement is ProposalAgreement.LLM_STRICTER
    assert any(f"LLM 2 proposed {proposed.value}" in r for r in m.review_reasons)
    assert not m.comparison.eligible and m.price_kind is PriceKind.NONE


def test_verified_non_cosmetic_difference_turns_exact_or_equivalent_into_review(run):
    diff = (("pickups", "SSS", "HSS", "Pickups: HSS"),)
    m = run("SB-001", with_proposal(rc5(), ProposedMatch.EXACT_MATCH, diffs=diff))
    assert m.match_status is M.REVIEW_REQUIRED and any("non-cosmetic difference in pickups" in r for r in m.review_reasons)
    e = run("SB-021", with_proposal(cort_black(), ProposedMatch.EQUIVALENT_VARIANT, diffs=diff))
    assert e.match_status is M.REVIEW_REQUIRED


def test_python_equivalent_with_llm_exact_stays_equivalent(run):
    m = run("SB-021", with_proposal(cort_black(), ProposedMatch.EXACT_MATCH))
    assert m.match_status is M.EQUIVALENT_VARIANT and m.proposal_agreement is ProposalAgreement.PYTHON_STRICTER


@pytest.mark.parametrize("cand,expected", [
    (lambda: candidate("https://www.thomann.co.uk/cort-g250-se-lh-black", "Cort G250 SE LH Black"), M.SIMILAR_MATCH),
    (lambda: candidate("https://www.andertons.co.uk/p/123456", "Boss RC-5 Loop Station"), M.REVIEW_REQUIRED),
    (lambda: candidate("https://www.andertons.co.uk/blog/best-loopers", "Best loopers"), M.NO_MATCH),
])  # fmt: skip
def test_llm2_never_upgrades_or_replaces_a_python_decision(run, catalogue, cand, expected):
    pid = "SB-021" if expected is M.SIMILAR_MATCH else "SB-001"
    for proposed in ProposedMatch:
        m = run(pid, with_proposal(cand(), proposed))
        assert m.match_status is expected, proposed


def test_unsupported_or_failed_proposal_is_ignored(run):
    for status in (ProposalStatus.UNSUPPORTED, ProposalStatus.FAILED):
        m = run("SB-001", with_proposal(rc5(), ProposedMatch.NOT_A_MATCH, status=status))
        assert m.match_status is M.EXACT_MATCH and m.proposal_agreement is ProposalAgreement.NOT_AVAILABLE
        assert m.proposed_match is None


def test_verified_identity_issue_adds_review_unverified_does_not(run):
    issue = dict(kind=IssueKind.AMBIGUOUS_VARIANT, fields=["finish"], description="two finishes listed",
                 quotes=["Boss RC-5"])  # fmt: skip
    c = rc5()
    c.extraction_issues = [IssueRecord(**issue, verified=False)]
    assert run("SB-001", c).match_status is M.EXACT_MATCH
    c.extraction_issues = [IssueRecord(**issue, verified=True)]
    m = run("SB-001", c)
    assert m.match_status is M.REVIEW_REQUIRED and any("ambiguous variant" in r for r in m.review_reasons)


def test_no_proposal_keeps_previous_behaviour(run):
    m = run("SB-001", candidate(AND_RC5, "Boss RC-5 Loop Station Pedal | Andertons Music Co."))
    assert m.match_status is M.EXACT_MATCH and m.proposal_agreement is ProposalAgreement.NOT_AVAILABLE


# --------------------------------------------------------------------------- direct vs alternative prices


def test_alternative_price_needs_no_benchmark_flag_but_a_search_approved_domain(run):
    marketplace = target("ebay.co.uk", T.MARKETPLACE, benchmark=False, status=DomainApprovalStatus.APPROVED)
    m = run("SB-021", cort_black(price="269.00", quote="£269.00", competitor=marketplace))
    assert m.price_kind is PriceKind.ALTERNATIVE and m.alternative_price.shown
    pending = target("new.co.uk", T.RETAILER, benchmark=False, status=DomainApprovalStatus.APPROVAL_REQUIRED)
    m = run("SB-021", cort_black(price="269.00", quote="£269.00", competitor=pending))
    assert not m.alternative_price.shown and "not approved for product search" in m.alternative_price.reasons[0]
    assert m.price_kind is PriceKind.NONE


def test_alternative_price_uses_the_same_price_validation(run):
    m = run("SB-021", cort_black(price="269.00", quote="£269.00", retrieved_at=NOW.replace(year=2025)))
    assert not m.alternative_price.shown and "STALE" in m.alternative_price.reasons[0]
    m = run("SB-021", cort_black())  # no price extracted
    assert not m.alternative_price.shown and m.alternative_price.price is None


def test_alternative_prices_never_counted_as_comparable(catalogue):
    from app.agents.intelligence.schemas import ProductIntelligence, ProductSummary

    p = catalogue.products["SB-021"]
    burgundy = candidate(CORT_BURGUNDY, "Cort G250 SE Electric Guitar Vivid Burgundy", llm=True,
                         product_title="Cort G250 SE Electric Guitar Vivid Burgundy", brand="Cort", model="G250 SE",
                         price="279.00", quote="£279.00")  # fmt: skip
    pr = ProductIntelligence(product=ProductSummary(product_id=p.product_id, product_name=p.product_name,
                                                    brand=p.brand, manufacturer_model=p.manufacturer_model),
                             queries=[], domains=[], not_selected=[],
                             candidates=[cort_black(price="269.00", quote="£269.00"), burgundy])  # fmt: skip
    s = evaluate_product(pr, p, 24, NOW)
    assert s.counts[M.EXACT_MATCH] == 1 and s.counts[M.EQUIVALENT_VARIANT] == 1
    assert s.comparable_prices == 1 and s.alternative_prices == 1
    assert [m.match_status for m in s.candidates] == [M.EXACT_MATCH, M.EQUIVALENT_VARIANT]


# --------------------------------------------------------------------------- Task C view


def test_view_answers_the_four_questions_with_explicit_uncertainty(run):
    c = rc5(availability="In stock")
    c.availability_status, c.availability_reason = AvailabilityStatus.IN_STOCK, "Verified text 'In stock'"
    m = run("SB-001", with_proposal(c, ProposedMatch.EXACT_MATCH))
    v = m.intelligence
    assert v.seller.domain == "andertons.co.uk" and v.seller.price_benchmark
    assert v.match.status is M.EXACT_MATCH and v.match.proposed is ProposedMatch.EXACT_MATCH
    assert v.price.kind is PriceKind.DIRECT and v.price.price == Decimal("189.00") and v.price.quote
    assert v.availability.status is AvailabilityStatus.IN_STOCK and v.availability.text == "In stock"
    assert v.uncertainties == []


def test_view_lists_missing_and_unknown_values_and_never_fills_them(run):
    m = run("SB-001", candidate(AND_RC5, "Boss RC-5 Loop Station Pedal | Andertons Music Co."))  # no LLM
    v = m.intelligence
    assert v.price.kind is PriceKind.NONE and v.price.price is None and v.price.difference_gbp is None
    assert v.availability.status is AvailabilityStatus.UNKNOWN and v.availability.text is None
    assert any(u.startswith("availability: unknown") for u in v.uncertainties)
    assert v.price.not_shown_reasons  # why no direct price


def test_view_never_shows_a_difference_for_alternative_prices(run):
    m = run("SB-021", cort_black(price="269.00", quote="£269.00"))
    assert m.intelligence.price.kind is PriceKind.ALTERNATIVE and m.intelligence.price.difference_gbp is None
    assert m.intelligence.price.price == Decimal("269.00") and m.intelligence.price.condition_label


# --------------------------------------------------------------------------- SB-028 approved Figured identity

LP_FIG = ("https://www.gear4music.com/Guitar-and-Bass/Epiphone-Les-Paul-Standard-60s-Figured-Left-Handed-Iced-Tea-Burst/6UYY",
          "Epiphone Les Paul Standard 60s Figured Left Handed Inspired by Gibson, Iced Tea Burst")


def test_sb028_llm2_disagreement_still_downgrades_the_figured_exact_match(run):
    """The approved identity never weakens U-4: a verified stricter proposal still gives REVIEW_REQUIRED."""
    base = dict(llm=True, product_title=LP_FIG[1], brand="Epiphone", model="Les Paul Standard 60s Figured",
                sku="EIGLP6ITNH1L", price="599.00", quote="£599.00")
    assert run("SB-028", candidate(*LP_FIG, **base)).match_status is M.EXACT_MATCH
    c = with_proposal(candidate(*LP_FIG, **base), ProposedMatch.SIMILAR_ALTERNATIVE, reasons=(("figured", LP_FIG[1]),),
                      diffs=(("model", "Les Paul Standard '60s LH", "Les Paul Standard 60s Figured", LP_FIG[1]),))
    m = run("SB-028", c)
    assert m.match_status is M.REVIEW_REQUIRED and m.proposal_agreement is ProposalAgreement.LLM_STRICTER
    assert not m.comparison.eligible
    agree = with_proposal(candidate(*LP_FIG, **base), ProposedMatch.EXACT_MATCH, reasons=(("same", LP_FIG[1]),))
    m = run("SB-028", agree)
    assert m.match_status is M.EXACT_MATCH and m.proposal_agreement is ProposalAgreement.AGREES


def test_task_a_prompt_carries_the_approved_designation_only_for_sb028(catalogue):
    import json

    from app.agents.intelligence.prompts import MATCH_SYSTEM, match_user

    def sent(pid):
        text = match_user(catalogue.products[pid], target(), "https://x.co.uk/p", "t", "page", {})
        return json.loads(text.split("Catalogue product:\n", 1)[1].split("\n\n", 1)[0])

    assert sent("SB-028")["model_designation"] == "Figured"
    assert "model_designation" not in sent("SB-001")
    assert "model_designation" in MATCH_SYSTEM
