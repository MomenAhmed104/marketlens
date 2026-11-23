"""Stage 9.1 fix: listings that include extras are bundles, never the standalone product.

Found live in the Stage 9.1 smoke run: Gear4music "Boss RC-5 Loop Station Guitar Loop Pedal with
Power Supply" was EXACT_MATCH and price-compared. Offline only (fixtures, synthetic candidates).
"""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.agents.intelligence.schemas import ProductIntelligence
from app.schemas.common import MatchStatus as M
from app.services.matching import evaluate_product, match_candidate
from app.services.matching import attributes as A
from app.services.matching.schemas import CheckResult
from tests.matching_fakes import NOW, candidate

SMOKE = Path(__file__).parent / "fixtures" / "stage91_smoke_sb001.json"


# --------------------------------------------------------------------------- detector


@pytest.mark.parametrize("text, kind, extras", [
    ("Boss RC-5 Loop Station Guitar Loop Pedal with Power Supply", "BUNDLE", {"power supply"}),
    ("boss rc 5 loop station guitar loop pedal with power supply", "BUNDLE", {"power supply"}),  # URL words
    ("Boss RC-5 Loop Station includes USB cable", "BUNDLE", {"cable"}),
    ("Boss RC-5 Loop Station Pedal with Case", "BUNDLE", {"case"}),
    ("Boss RC-5 Loop Station & PSA Adaptor", "BUNDLE", {"power supply"}),
    ("Boss RC-5 Loop Station + Patch Cable", "BUNDLE", {"cable"}),
    ("Boss RC-5 Loop Station, hard case included", "BUNDLE", {"case"}),
    ("Boss RC-5 Loop Station Bundle", "BUNDLE", set()),
    ("Cherub WST-905Li Clip-on Tuner 2 Pack", "BUNDLE", set()),
    ("Boss RC-5 Loop Station (power supply not included)", "OPTIONAL", set()),
    ("Boss RC-5 Loop Station, power supply sold separately", "OPTIONAL", set()),
    ("Boss RC-5 Loop Station - requires 9V power supply", "OPTIONAL", set()),
    ("Boss RC-5 Loop Station, optional footswitch available separately", "OPTIONAL", set()),
    ("Boss RC-5 Loop Station without power supply", "OPTIONAL", set()),
    ("Boss RC-5 Loop Station Power Supply", "AMBIGUOUS", {"power supply"}),
    ("Boss RC-5 Loop Station - Footswitch", "AMBIGUOUS", {"footswitch"}),
    ("Boss RC-5 Loop Station Guitar Loop Pedal", "STANDALONE", set()),
    ("Boss RC-500 Loop Station Pedal", "STANDALONE", set()),
    # Features and products, not extras.
    ("Epiphone IGC 1964 SG Standard Reissue with Maestro Vibrola", "STANDALONE", set()),
    ("NUX Mighty 20 MKII Electric Guitar Amplifier with Bluetooth, 20 Watts", "STANDALONE", set()),
    ("Cherub WST-905Li Flow Tune Rechargeable Clip-on Headstock Guitar Tuner", "STANDALONE", set()),
    ("Fender Stratocaster Standard", "STANDALONE", set()),  # 'stand' inside 'Standard' is not a stand
])  # fmt: skip
def test_bundle_evidence(text, kind, extras):
    be = A.bundle_evidence(text)
    assert be.kind == kind, (text, be)
    assert set(be.included or be.mentioned) == extras


def test_an_accessory_product_is_not_a_bundle_of_itself():
    own = A.bundle_evidence("Boss PSA-230S Power Supply").mentioned  # the catalogue product IS a power supply
    assert A.bundle_evidence("Boss PSA-230S Power Supply Adaptor", own).kind == "STANDALONE"
    assert A.bundle_evidence("Boss PSA-230S Power Supply with Cable", own).included == {"cable"}


# --------------------------------------------------------------------------- matcher (SB-001)

RC5 = "https://www.gear4music.com/Guitar-and-Bass/Boss-RC-5-Loop-Station-Guitar-Loop-Pedal"


def match(title, url=RC5, pid="SB-001", catalogue=None):
    c = candidate(url, title, llm=True, product_title=title, model="RC-5", price="219.00", quote="£219.00")
    return match_candidate(catalogue.products[pid], c, NOW, 24)


def bundle_of(m):
    return next(v for v in m.variants if v.attribute == "bundle")


def test_a_genuine_standalone_rc5_stays_exact(catalogue):
    m = match("Boss RC-5 Loop Station Guitar Loop Pedal", catalogue=catalogue)
    assert m.match_status is M.EXACT_MATCH and m.comparison.eligible
    assert bundle_of(m).result is CheckResult.NOT_APPLICABLE


@pytest.mark.parametrize("title, url_suffix", [
    ("Boss RC-5 Loop Station Guitar Loop Pedal with Power Supply", "-with-Power-Supply"),
    ("Boss RC-5 Loop Station includes USB cable", ""),
    ("Boss RC-5 Loop Station Pedal with Case", ""),
    ("Boss RC-5 Loop Station Bundle", "-Bundle"),
    ("Boss RC-5 Loop Station Pedal Pack", ""),
])  # fmt: skip
def test_bundles_are_never_exact_and_never_price_compared(catalogue, title, url_suffix):
    m = match(title, RC5 + url_suffix, catalogue=catalogue)
    assert m.match_status is M.SIMILAR_MATCH, (title, m.match_reasons)
    assert bundle_of(m).result is CheckResult.CONFLICT and not m.comparison.eligible
    assert m.comparison.difference_gbp is None


@pytest.mark.parametrize("title", [
    "Boss RC-5 Loop Station (power supply not included)",
    "Boss RC-5 Loop Station, power supply sold separately",
    "Boss RC-5 Loop Station - requires 9V power supply",
])  # fmt: skip
def test_optional_accessories_leave_the_standalone_product(catalogue, title):
    m = match(title, catalogue=catalogue)
    assert m.match_status is M.EXACT_MATCH and m.comparison.eligible
    assert bundle_of(m).result is CheckResult.NOT_APPLICABLE


def test_ambiguous_accessory_wording_needs_review(catalogue):
    m = match("Boss RC-5 Loop Station Power Supply", catalogue=catalogue)
    assert m.match_status is M.REVIEW_REQUIRED and not m.comparison.eligible
    assert bundle_of(m).result is CheckResult.UNVERIFIED
    assert any("bundle not confirmed" in r for r in m.review_reasons)


def test_a_different_model_is_not_exact(catalogue):
    m = match("Boss RC-500 Loop Station Pedal", "https://www.andertons.co.uk/boss-rc-500-loop-station-pedal",
              catalogue=catalogue)  # fmt: skip
    assert m.match_status is not M.EXACT_MATCH and not m.comparison.eligible


# --------------------------------------------------------------------------- Stompbox bundles


def test_a_stompbox_bundle_matches_only_the_same_extras(catalogue):
    p = catalogue.products["SB-033"]  # "... Sonic Blue with Deluxe Gig Bag"
    prof = A.profile(p)
    assert prof.bundle and prof.bundle_items == {"bag"}
    base = "Fender Gold Foil Jazz Bass Sonic Blue"
    url = "https://www.example-shop.co.uk/fender-gold-foil-jazz-bass-sonic-blue"

    def m(title):
        c = candidate(url, title, llm=True, product_title=title, model="Gold Foil Jazz Bass", price="1199.00",
                      quote="£1,199.00")  # fmt: skip
        return match_candidate(p, c, NOW, 24)

    assert bundle_of(m(f"{base} with Gig Bag")).result is CheckResult.MATCH
    assert bundle_of(m(f"{base} with Hard Case")).result is CheckResult.CONFLICT
    assert bundle_of(m(f"{base} Bundle")).result is CheckResult.UNVERIFIED  # contents unknown: review
    assert bundle_of(m(base)).result is CheckResult.NOT_APPLICABLE  # unchanged behaviour without bundle evidence


def test_a_guitar_listed_with_a_gig_bag_needs_review_not_a_conflict(catalogue):
    title = "Epiphone Les Paul Standard 60s Figured Left-Handed in Iced Tea with Gig Bag"
    c = candidate("https://wildwireguitars.com/epiphone-les-paul-standard-60s-figured-left-handed-in-iced-tea-with-gig-bag",
                  title, llm=True, product_title=title, model="Les Paul Standard 60s Figured", price="599.00",
                  quote="£599.00")  # fmt: skip
    m = match_candidate(catalogue.products["SB-028"], c, NOW, 24)
    assert bundle_of(m).result is CheckResult.UNVERIFIED and m.match_status is M.REVIEW_REQUIRED


# --------------------------------------------------------------------------- the smoke-run regression


def test_stage91_smoke_run_regression(catalogue):
    data = json.loads(SMOKE.read_text(encoding="utf-8"))
    intel = ProductIntelligence.model_validate(data["intelligence"])
    now = datetime.fromisoformat(data["retrieved_at"])  # evidence as fresh as it was during the run
    summary = evaluate_product(intel, catalogue.products["SB-001"], 24, now)
    by_url = {c.product_url.split("/")[-1]: c for c in summary.candidates}
    bundle = by_url["40BW"]  # "... with Power Supply"
    assert bundle.match_status is M.SIMILAR_MATCH and not bundle.comparison.eligible  # was EXACT_MATCH, +£29.01
    assert bundle_of(bundle).result is CheckResult.CONFLICT and "power supply" in bundle_of(bundle).found
    # Everything else is unchanged from the live run.
    assert by_url["boss-rc-5-loop-station-pedal"].match_status is M.EXACT_MATCH
    assert by_url["boss-rc-5-loop-station-pedal"].comparison.difference_gbp == Decimal("29.01")
    assert by_url["3LJT"].match_status is M.EXACT_MATCH and by_url["3LJT"].comparison.difference_gbp == Decimal("4.51")
    assert by_url["boss-rc-500-loop-station-pedal"].match_status is M.SIMILAR_MATCH
    assert summary.comparable_prices == 2  # was 3
