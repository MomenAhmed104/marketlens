"""Post-Stage 7: an amount given as the current price AND listed as an ignored price.

Stage 7 Run 1 (Andertons, SB-028 Figured): LLM 2 returned "£599.00" as the current price with the
quote "£599.00", and also listed "£599.00" (alongside "£0.00" and "£17.97") as ignored prices with
no wording. Python excluded it as EXCLUDED_SALE_PRICE, "crossed-out/RRP", which nothing on the page
supported. The amount is still never used, but the status now matches the evidence:
SALE wording -> EXCLUDED_SALE_PRICE, finance wording -> AMBIGUOUS, a bare amount -> CONFLICT.
Offline only; saved run files are never rewritten.
"""

from decimal import Decimal

import pytest

from app.agents.intelligence.rules import check_extraction
from app.agents.intelligence.schemas import ExtractedField as F
from app.agents.intelligence.schemas import FieldEvidence, FieldStatus, PageType
from app.schemas.common import EvidenceStatus
from app.services.matching.price import validate_price
from app.services.matching.schemas import PriceStatus
from tests.intelligence_fakes import extraction
from tests.matching_fakes import NOW, candidate

PAGE = "Epiphone Les Paul Standard '60s Figured Left Handed\n£599.00\nOut of Stock\nFinance from £17.97 per month\n"


def price_of(c):
    return next(e for e in c.evidence if e.field is F.PRICE)


def run(ignored, value="£599.00", quote="£599.00", page=PAGE):
    return check_extraction(extraction(displayed_price=value, price_quote=quote, currency="GBP",
                                       ignored_prices=ignored), page, None)  # fmt: skip


def test_stage7_andertons_case_is_a_conflict_not_a_sale_price():
    c = run(["£0.00", "£17.97", "£599.00"])
    ev = price_of(c)
    assert ev.status is FieldStatus.CONFLICT and c.price is None  # still never used
    assert ev.quote == "£599.00" and ev.value == "£599.00"  # the page quote is preserved
    assert "was/RRP" in ev.note and "not used" in ev.note and "crossed-out" not in ev.note
    assert c.sale_indicated is False  # nothing on the page shows a sale


@pytest.mark.parametrize("ignored", [["Was £599.00"], ["RRP £599.00"], ["Original price £599.00"],
                                     ["£599.00 crossed out, now £549.00"]])  # fmt: skip
def test_genuine_sale_wording_still_gives_excluded_sale_price(ignored):
    page = PAGE + "\n" + ignored[0]
    ev = price_of(run(ignored, page=page))
    assert ev.status is FieldStatus.EXCLUDED_SALE_PRICE and "was/RRP" in ev.note


def test_sale_wording_in_the_price_quote_still_excludes():
    page = "Was £599.00 Now £549.00"
    ev = price_of(run([], value="£599.00", quote="Was £599.00 Now £549.00", page=page))
    assert ev.status is FieldStatus.EXCLUDED_SALE_PRICE


def test_finance_listing_of_the_same_amount_is_ambiguous():
    page = PAGE + "\nDeposit £599.00"
    ev = price_of(run(["Deposit £599.00"], page=page))
    assert ev.status is FieldStatus.AMBIGUOUS and "finance" in ev.note


def test_sale_wins_over_a_bare_listing_of_the_same_amount():
    page = PAGE + "\nWas £599.00"
    ev = price_of(run(["£599.00", "Was £599.00"], page=page))
    assert ev.status is FieldStatus.EXCLUDED_SALE_PRICE


def test_unrelated_ignored_amounts_leave_the_current_price_alone():
    c = run(["£0.00", "£17.97"])
    assert price_of(c).status is FieldStatus.EXTRACTED and c.price == Decimal("599.00")


# --------------------------------------------------------------------------- older records (matching)


def stored(status, note, ignored, quote="£599.00"):
    c = candidate("https://www.andertons.co.uk/epiphone-les-paul-standard-60s-figured-left-handed", "Epiphone",
                  llm=True)  # fmt: skip
    c.page_type = PageType.PRODUCT_PAGE
    c.field_evidence = [e for e in c.field_evidence if e.field is not F.PRICE] + [
        FieldEvidence(field=F.PRICE, status=status, value="£599.00", quote=quote, note=note)]
    c.ignored_prices = ignored
    return validate_price(c, NOW, 24)


def test_older_unsupported_sale_exclusion_is_shown_as_a_conflict():
    v = stored(FieldStatus.EXCLUDED_SALE_PRICE, "LLM 2 also listed this amount as an ignored (crossed-out/RRP) price",
               ["£0.00", "£17.97", "£599.00"])  # fmt: skip
    assert v.status is PriceStatus.CONFLICT and v.evidence_status is EvidenceStatus.CONFLICT and v.price is None
    assert v.quote == "£599.00" and "does not settle" in v.reasons[0]


@pytest.mark.parametrize("ignored,quote", [(["Was £599.00"], "£599.00"), ([], "Was £599.00 Now £549.00")])
def test_older_supported_sale_exclusion_is_unchanged(ignored, quote):
    v = stored(FieldStatus.EXCLUDED_SALE_PRICE, "x", ignored, quote=quote)
    assert v.status is PriceStatus.EXCLUDED_SALE_PRICE


def test_stage7_saved_record_shape_is_relabelled_without_rewriting():
    """The saved Stage 7 run is never edited; this rebuilds its Andertons price record in memory."""
    v = stored(FieldStatus.EXCLUDED_SALE_PRICE, None, ["£0.00", "£17.97", "£599.00"])
    assert v.status is PriceStatus.CONFLICT and v.price is None
