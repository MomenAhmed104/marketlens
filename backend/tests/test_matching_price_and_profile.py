"""Phase 6 price re-verification and Stompbox catalogue profiling."""

from decimal import Decimal

import pytest

from app.agents.intelligence.schemas import PageType
from app.schemas.common import EvidenceStatus
from app.services.matching import attributes as A
from app.services.matching import validate_price
from app.services.matching.schemas import PriceStatus
from tests.matching_fakes import NOW, candidate

URL = "https://www.andertons.co.uk/boss-rc-5-loop-station-pedal"


def priced(**kw):
    base = dict(llm=True, model="RC-5", brand="Boss", price="189.00", quote="£189.00 In stock")
    base.update(kw)
    return candidate(URL, "Boss RC-5", **base)


def v(c, max_age=24):
    return validate_price(c, NOW, max_age)


# --------------------------------------------------------------------------- price


def test_valid_gbp_price():
    r = v(priced())
    assert r.status is PriceStatus.VALID and r.evidence_status is EvidenceStatus.VERIFIED
    assert (r.price, r.currency, r.price_text, r.quote) == (Decimal("189.00"), "GBP", "£189.00", "£189.00 In stock")
    assert r.source_url == URL and r.retrieved_at == NOW


def test_current_price_on_a_sale_page_is_valid_and_flagged():
    r = v(priced(quote="Was £219.00 Now £189.00", sale=True))
    assert r.status is PriceStatus.VALID and r.sale_indicated
    assert any("no discount computed" in x for x in r.reasons)


@pytest.mark.parametrize("quote", ["Was £189.00", "RRP £189.00", "Save £189.00", "Regular price £189.00 Sale price £150"])
def test_rrp_or_crossed_out_amount_is_rejected_even_if_marked_extracted(quote):
    # Defence in depth: Phase 6 re-checks the quote and never trusts the Phase 5 status alone.
    r = v(priced(quote=quote))
    assert r.status is PriceStatus.EXCLUDED_SALE_PRICE and r.price is None


def test_conditional_amount_is_ambiguous():
    r = v(priced(price="15.75", quote="Finance from £15.75 per month"))
    assert r.status is PriceStatus.AMBIGUOUS and r.price is None


@pytest.mark.parametrize("kw,problem", [
    ({"price": "0.00", "quote": "£0.00"}, "not positive"),
    ({"price_text": "£199.00"}, "does not parse to the stored amount"),
    ({"quote": "In stock now"}, "not found in its quote"),
    ({"page_type": PageType.LISTING_PAGE}, "single-product page"),
])  # fmt: skip
def test_inconsistent_records_are_invalid(kw, problem):
    r = v(priced(**kw))
    assert r.status is PriceStatus.INVALID and r.price is None and any(problem in x for x in r.reasons)


def test_wrong_currency():
    r = v(priced(currency="EUR", quote="€189.00"))
    assert r.status is PriceStatus.WRONG_CURRENCY and r.currency == "EUR" and r.price == Decimal("189.00")


def test_currency_mismatch_between_text_and_record_is_invalid():
    c = priced()
    c.currency = "EUR"  # record says EUR, displayed text says £
    r = v(c)
    assert r.status is PriceStatus.INVALID and any("differs" in x for x in r.reasons)


def test_missing_is_not_zero_and_no_llm_is_not_extracted():
    r = v(priced(price=None))
    assert r.status is PriceStatus.MISSING and r.price is None and r.evidence_status is EvidenceStatus.NOT_FOUND
    r = v(candidate(URL, "Boss RC-5"))
    assert r.status is PriceStatus.NOT_EXTRACTED and "no-LLM" in r.reasons[0]


def test_stale_evidence():
    from datetime import timedelta

    r = v(priced(retrieved_at=NOW - timedelta(hours=25)))
    assert r.status is PriceStatus.VALID and r.evidence_status is EvidenceStatus.STALE
    assert v(priced(retrieved_at=NOW - timedelta(hours=25)), max_age=48).evidence_status is EvidenceStatus.VERIFIED


# --------------------------------------------------------------------------- catalogue profiles


def test_every_catalogue_product_profiles(catalogue):
    profiles = {pid: A.profile(p) for pid, p in catalogue.products.items()}
    assert len(profiles) == 100
    assert profiles["SB-028"].handedness == "left" and profiles["SB-028"].finish == "Iced Tea Burst"
    assert profiles["SB-044"].handedness == "left" and profiles["SB-044"].finish is None  # "Left Handed" is not a finish
    assert profiles["SB-033"].finish == "Sonic Blue" and profiles["SB-033"].bundle  # "with Deluxe Gig Bag"
    assert profiles["SB-032"].strings == {"5"} and profiles["SB-029"].strings_default == "4"
    assert profiles["SB-021"].strings_default == "6" and profiles["SB-001"].strings_default is None
    assert profiles["SB-038"].sizes == {"3/4"}
    assert profiles["SB-046"].cab_configs == {"2x12"} and profiles["SB-047"].cab_configs == set()
    assert profiles["SB-003"].generations == {2} and profiles["SB-014"].generations == {2}  # II, Mk.II
    assert profiles["SB-070"].generations == {2} and profiles["SB-065"].generations == {3}  # V2, V3
    assert profiles["SB-053"].editions == {"10th edition"}
    assert profiles["SB-016"].wattages == {"15"} and profiles["SB-016"].finish is None
    assert profiles["SB-054"].finish is None  # "White Light" is the model, not a finish
    left = sorted(pid for pid, pr in profiles.items() if pr.handedness == "left")
    assert left == ["SB-028", "SB-044"]


@pytest.mark.parametrize("model,text,expected", [
    ("RC-5", "Boss RC5 Loop Station", True),
    ("RC-5", "Boss RC-500 Loop Station", False),
    ("Mod Factory Mk.II", "Mooer Mod Factory MKII", True),
    ("Mod Factory Mk.II", "Mooer Mod Factory", False),
    ("Les Paul Standard '60s LH", "Les Paul Standard 60s Left Handed", True),
    ("St. James 50 6L6 Head & 2x12", "St James 50 6L6 Head + 2x12 Cab", True),
    ("BAA2CE", "Bromo BAA2 Appalachia", False),
])  # fmt: skip
def test_model_identity(model, text, expected):
    assert A.model_in(model, text) is expected


def test_sibling_models():
    assert A.sibling_models("RC-5", "Boss RC-500 and RC-1") == {"rc500", "rc1"}
    assert A.sibling_models("RC-5", "Boss RC-5 Loop Station") == set()
    assert A.sibling_models("G250 SE", "Cort G250 SE") == set()
    assert A.sibling_models("BAA2CE", "Bromo BAA2 Appalachia") == {"baa2"}
    assert A.sibling_models("Hook", "NUX Hook") == set()  # descriptive models have no family
