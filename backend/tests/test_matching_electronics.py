"""Deterministic electronics / functional checks in the matcher (audit fix): pickup layout,
active/passive electronics and electro-acoustic. A verified difference prevents EXACT_MATCH
(SIMILAR_MATCH); missing evidence the catalogue needs gives REVIEW_REQUIRED; no difference is
ever inferred from silence. No network, no LLM."""

import pytest

from app.agents.intelligence.schemas import (
    PROPOSAL_STATUS,
    MatchProposalRecord,
    NormalizedSpec,
    ProposalStatus,
    ProposedMatch,
    VerifiedReason,
)
from app.schemas.common import MatchStatus as M
from app.services.matching import match_candidate
from app.services.matching.schemas import CheckResult
from tests.matching_fakes import NOW, candidate


@pytest.fixture
def run(catalogue):
    def _run(product, cand):
        p = catalogue.products[product] if isinstance(product, str) else product
        return match_candidate(p, cand, NOW, 24)

    return _run


@pytest.fixture
def hss(catalogue):
    """SB-021's row stating an HSS pickup layout."""
    return catalogue.products["SB-021"].model_copy(update={"product_name": "Cort G250 SE HSS Electric Guitar, Vivid Burgundy"})


@pytest.fixture
def active_bass(catalogue):
    return catalogue.products["SB-029"].model_copy(update={"product_name": "Ashdown Roasted Capri Active Bass Guitar, Ice Blue"})


def check(m, attr):
    return next(v for v in m.variants if v.attribute == attr)


def cort(title, specs=(), url="https://www.thomann.co.uk/cort-g250-se-vivid-burgundy"):
    c = candidate(url, title, llm=True, product_title=title, brand="Cort", model="G250 SE")
    c.specifications = [NormalizedSpec(name=n, value=v, key=k) for n, v, k in specs]
    return c


# --------------------------------------------------------------------------- pickups


def test_pickup_configuration_conflict_is_similar(run, hss):
    m = run(hss, cort("Cort G250 SE HH Electric Guitar Vivid Burgundy"))
    assert m.match_status is M.SIMILAR_MATCH and check(m, "pickups").result is CheckResult.CONFLICT
    m = run(hss, cort("Cort G250 SE Electric Guitar Vivid Burgundy", [("Pickups", "SSS", "pickups")]))
    assert m.match_status is M.SIMILAR_MATCH and check(m, "pickups").found == ["SSS"]


def test_pickup_configuration_confirmed_by_spec_is_exact(run, hss):
    m = run(hss, cort("Cort G250 SE Electric Guitar Vivid Burgundy", [("Pickup configuration", "HSS", "pickups")]))
    assert m.match_status is M.EXACT_MATCH and check(m, "pickups").result is CheckResult.MATCH


def test_missing_pickup_evidence_for_a_stated_layout_needs_review(run, hss):
    m = run(hss, cort("Cort G250 SE Electric Guitar Vivid Burgundy"))
    assert m.match_status is M.REVIEW_REQUIRED and any("pickups" in r for r in m.review_reasons)


def test_pickup_variant_in_title_the_catalogue_does_not_state_needs_review(run):
    # The audit case: catalogue silent, the competitor title names an HH variant -> not exact.
    m = run("SB-021", cort("Cort G250 SE HH Electric Guitar Vivid Burgundy"))
    assert m.match_status is M.REVIEW_REQUIRED and check(m, "pickups").result is CheckResult.UNVERIFIED


def test_no_difference_inferred_without_evidence(run):
    # Catalogue silent; only a spec lists the layout (every guitar page does): nothing to compare.
    m = run("SB-021", cort("Cort G250 SE Electric Guitar Vivid Burgundy", [("Pickups", "HSS", "pickups")]))
    assert m.match_status is M.EXACT_MATCH and check(m, "pickups").result is CheckResult.NOT_APPLICABLE
    m = run("SB-001", candidate("https://www.andertons.co.uk/boss-rc-5-loop-station", "Boss RC-5 Loop Station"))
    assert all(check(m, a).result is CheckResult.NOT_APPLICABLE for a in ("pickups", "electronics", "electro_acoustic"))


# --------------------------------------------------------------------------- active / passive


def test_active_passive_conflict_is_similar(run, active_bass):
    c = candidate("https://www.thomann.co.uk/ashdown-roasted-capri-ice-blue", "Ashdown Roasted Capri Bass Ice Blue",
                  llm=True, product_title="Ashdown Roasted Capri Bass Ice Blue", brand="Ashdown", model="Roasted Capri")  # fmt: skip
    c.specifications = [NormalizedSpec(name="Electronics", value="Passive", key="electronics")]
    m = run(active_bass, c)
    assert m.match_status is M.SIMILAR_MATCH and check(m, "electronics").result is CheckResult.CONFLICT


def test_active_confirmed_in_title_is_exact(run, active_bass):
    m = run(active_bass, candidate("https://www.thomann.co.uk/ashdown-roasted-capri-active-ice-blue",
                                   "Ashdown Roasted Capri Active Bass Guitar, Ice Blue"))  # fmt: skip
    assert m.match_status is M.EXACT_MATCH and check(m, "electronics").result is CheckResult.MATCH


def test_missing_active_evidence_needs_review(run, active_bass):
    m = run(active_bass, candidate("https://www.thomann.co.uk/ashdown-roasted-capri-ice-blue",
                                   "Ashdown Roasted Capri Bass Guitar, Ice Blue"))  # fmt: skip
    assert m.match_status is M.REVIEW_REQUIRED and check(m, "electronics").result is CheckResult.UNVERIFIED


# --------------------------------------------------------------------------- electro-acoustic


EXOTIC = "https://www.thomann.co.uk/cort-exotic-ga-walnut-sunburst"


def test_electro_acoustic_product_against_explicit_acoustic_only_is_similar(run):
    m = run("SB-045", candidate(EXOTIC, "Cort Exotic-GA Walnut Solid Top Acoustic Guitar, Sunburst, no pickup"))
    assert m.match_status is M.SIMILAR_MATCH and check(m, "electro_acoustic").result is CheckResult.CONFLICT
    c = candidate(EXOTIC, "Cort Exotic-GA Walnut Solid Top Guitar, Sunburst", llm=True, brand="Cort", model="Exotic-GA",
                  product_title="Cort Exotic-GA Walnut Solid Top Guitar, Sunburst")  # fmt: skip
    c.specifications = [NormalizedSpec(name="Electronics", value="None", key="electronics")]
    assert run("SB-045", c).match_status is M.SIMILAR_MATCH


def test_missing_electronics_evidence_needs_review(run):
    # The audit case: an electro-acoustic product, a page that never says electro -> not exact.
    m = run("SB-045", candidate(EXOTIC, "Cort Exotic-GA Walnut Solid Top Acoustic Guitar, Sunburst"))
    assert m.match_status is M.REVIEW_REQUIRED and check(m, "electro_acoustic").result is CheckResult.UNVERIFIED


def test_electro_acoustic_confirmed_is_exact(run):
    m = run("SB-045", candidate(EXOTIC, "Cort Exotic-GA Walnut Solid Top Electro-Acoustic Guitar, Sunburst"))
    assert m.match_status is M.EXACT_MATCH and check(m, "electro_acoustic").result is CheckResult.MATCH


def test_exact_code_like_model_implies_the_electronics(run):
    # "BAA2CE" is the electro version's own code (the acoustic one is "BAA2").
    m = run("SB-040", candidate("https://www.thomann.co.uk/bromo-baa2ce-natural", "Bromo BAA2CE Appalachia Guitar, Natural"))
    assert m.match_status is M.EXACT_MATCH and check(m, "electro_acoustic").note == "Implied by the exact model code"


def test_acoustic_product_against_electro_version_is_similar(run):
    m = run("SB-037", candidate("https://www.thomann.co.uk/aria-209n-electro-acoustic-natural",
                                "Aria 209N Solid Spruce Top Electro-Acoustic Guitar, Natural"))  # fmt: skip
    assert m.match_status is M.SIMILAR_MATCH and check(m, "electro_acoustic").result is CheckResult.CONFLICT


# --------------------------------------------------------------------------- LLM 2 rules preserved


def test_llm2_exact_proposal_never_overrides_an_electronics_conflict(run, hss):
    c = cort("Cort G250 SE HH Electric Guitar Vivid Burgundy")
    c.match_proposal = MatchProposalRecord(classification=ProposedMatch.EXACT_MATCH,
                                           corresponding_status=PROPOSAL_STATUS[ProposedMatch.EXACT_MATCH],
                                           reasons=[VerifiedReason(text="same", quote="Cort G250 SE", verified=True)])  # fmt: skip
    c.match_proposal_status = ProposalStatus.COMPLETED
    m = run(hss, c)
    assert m.match_status is M.SIMILAR_MATCH and m.proposal_agreement.value == "PYTHON_STRICTER"
