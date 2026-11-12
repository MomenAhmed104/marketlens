"""Deterministic checks on LLM 2 output: evidence quotes, price and sale-price exclusion."""

from decimal import Decimal

import pytest

from app.agents.intelligence.rules import check_extraction, product_queries
from app.agents.intelligence.schemas import ExtractedField as F
from app.agents.intelligence.schemas import ExtractedSpec, FieldStatus, PageType
from app.services.prices import find_prices, is_conditional_context, is_sale_context, parse_single_price
from tests.intelligence_fakes import extraction

PAGE = """Boss RC-5 Loop Station Pedal | Example Music
Boss RC-5 Loop Station
Was £219.00 Now £189.00
In stock - dispatched today
Product code: BOS-RC5
Brand: BOSS  Model: RC-5
Max recording time: 13 hours
Finance from £15.75 per month
"""


def price_ev(checked):
    return next(e for e in checked.evidence if e.field is F.PRICE)


def field(checked, f):
    return next(e for e in checked.evidence if e.field is f)


def check(**kw):
    return check_extraction(extraction(**kw), PAGE, "Boss RC-5 Loop Station Pedal | Example Music")


# --------------------------------------------------------------------------- price


def test_current_price_from_was_now_text_is_extracted_and_sale_flagged():
    c = check(displayed_price="£189.00", price_quote="Was £219.00 Now £189.00", currency="GBP",
              ignored_prices=["Was £219.00"])  # fmt: skip
    assert price_ev(c).status is FieldStatus.EXTRACTED
    assert c.price == Decimal("189.00") and c.currency == "GBP" and c.price_text == "£189.00"
    assert c.sale_indicated is True  # informational only; no discount computed


@pytest.mark.parametrize("value,quote", [
    ("£219.00", "Was £219.00 Now £189.00"),  # the crossed-out "was" figure
    ("£219.00", "Was £219.00"),
])  # fmt: skip
def test_was_price_is_excluded(value, quote):
    c = check(displayed_price=value, price_quote=quote)
    assert price_ev(c).status is FieldStatus.EXCLUDED_SALE_PRICE and c.price is None


def test_amount_the_llm_itself_listed_as_ignored_is_excluded():
    page = "Our price £189.00 (RRP £219.00)"
    c = check_extraction(extraction(displayed_price="£219.00", price_quote="Our price £189.00 (RRP £219.00)",
                                    ignored_prices=["RRP £219.00"]), page, None)  # fmt: skip
    assert price_ev(c).status is FieldStatus.EXCLUDED_SALE_PRICE and c.price is None


@pytest.mark.parametrize("quote", ["Save £30.00 today", "RRP £219.00", "£30.00 off", "Originally £219.00"])
def test_saving_and_original_prices_are_excluded(quote):
    amount = find_prices(quote)[0].text
    c = check_extraction(extraction(displayed_price=amount, price_quote=quote), quote, None)
    assert price_ev(c).status is FieldStatus.EXCLUDED_SALE_PRICE and c.price is None


@pytest.mark.parametrize("page,original,current", [
    ("Regular price £219.00 Sale price £189.00", "£219.00", "£189.00"),  # Shopify
    ("BOSS RC-5 Regular Price £219.00 Special Price £197.00 In Stock", "£219.00", "£197.00"),  # Magento (live BopDJ text)
    ("Normal price £99.00 Offer price £89.00", "£99.00", "£89.00"),
    ("Regular price £219.00 Now £189.00", "£219.00", "£189.00"),
])  # fmt: skip
def test_regular_price_is_original_only_when_a_current_price_label_is_shown(page, original, current):
    c = check_extraction(extraction(displayed_price=original, price_quote=page), page, None)
    assert price_ev(c).status is FieldStatus.EXCLUDED_SALE_PRICE and c.price is None
    c = check_extraction(extraction(displayed_price=current, price_quote=page), page, None)
    assert price_ev(c).status is FieldStatus.EXTRACTED and c.price_text == current


def test_regular_price_alone_is_the_standard_price():
    plain = "Regular price £189.00"
    c = check_extraction(extraction(displayed_price="£189.00", price_quote=plain), plain, None)
    assert price_ev(c).status is FieldStatus.EXTRACTED


def test_missing_price_is_not_found_never_zero():
    c = check(displayed_price=None, price_quote=None)
    assert price_ev(c).status is FieldStatus.NOT_FOUND and c.price is None and c.currency is None


@pytest.mark.parametrize("value", ["£0.00", "Call for price", "189.00", "£189 or £199"])
def test_invalid_prices(value):
    page = f"Price: {value}"
    c = check_extraction(extraction(displayed_price=value, price_quote=page), page, None)
    assert price_ev(c).status is FieldStatus.INVALID and c.price is None


def test_price_not_in_page_or_not_in_quote_is_unsupported():
    c = check(displayed_price="£179.00", price_quote="Now £179.00")  # invented quote
    assert price_ev(c).status is FieldStatus.UNSUPPORTED and c.price is None
    c = check(displayed_price="£179.00", price_quote="Was £219.00 Now £189.00")  # real quote, wrong amount
    assert price_ev(c).status is FieldStatus.UNSUPPORTED and c.price is None


def test_snippet_text_is_not_evidence():
    # A price that appears only in a search snippet, not in the retrieved page, is never accepted.
    c = check_extraction(extraction(displayed_price="£150.00", price_quote="£150.00 used"), "Boss RC-5 page", None)
    assert price_ev(c).status is FieldStatus.UNSUPPORTED


def test_conditional_prices_are_ambiguous():
    c = check(displayed_price="£15.75", price_quote="Finance from £15.75 per month")
    assert price_ev(c).status is FieldStatus.AMBIGUOUS and c.price is None
    c = check(displayed_price="£189.00", price_quote="Was £219.00 Now £189.00", price_is_conditional=True)
    assert price_ev(c).status is FieldStatus.AMBIGUOUS


def test_currency_must_be_explicit_and_consistent():
    page = "Price $189.00"
    c = check_extraction(extraction(displayed_price="$189.00", price_quote=page), page, None)
    assert price_ev(c).status is FieldStatus.AMBIGUOUS and c.currency is None
    c = check(displayed_price="£189.00", price_quote="Was £219.00 Now £189.00", currency="EUR")
    assert price_ev(c).status is FieldStatus.CONFLICT and c.price is None


def test_price_on_listing_page_is_not_attributed():
    c = check(page_type=PageType.LISTING_PAGE, displayed_price="£189.00", price_quote="Was £219.00 Now £189.00")
    assert price_ev(c).status is FieldStatus.AMBIGUOUS and c.price is None


# --------------------------------------------------------------------------- text fields


def test_text_fields_need_quote_in_page_and_value_in_quote():
    c = check(product_title="Boss RC-5 Loop Station", title_quote="Boss RC-5 Loop Station",
              brand="BOSS", brand_quote="Brand: BOSS", model="RC5", model_quote="Model: RC-5",
              sku="BOS-RC5", sku_quote="Product code: BOS-RC5",
              availability="In stock", availability_quote="In stock - dispatched today")  # fmt: skip
    assert all(field(c, f).status is FieldStatus.EXTRACTED for f in (F.PRODUCT_TITLE, F.BRAND, F.MODEL, F.SKU,
                                                                     F.AVAILABILITY))  # fmt: skip
    assert c.values[F.MODEL] == "RC5" and c.values[F.SKU] == "BOS-RC5"


def test_invented_or_unquoted_values_are_rejected():
    c = check(brand="Roland", brand_quote="Brand: BOSS",  # value not in its quote
              model="RC-500", model_quote="Model: RC-500",  # quote not in page
              availability="In stock", availability_quote=None)  # no quote at all
    for f in (F.BRAND, F.MODEL, F.AVAILABILITY):
        assert field(c, f).status is FieldStatus.UNSUPPORTED and c.values[f] is None
    assert field(c, F.SKU).status is FieldStatus.NOT_FOUND


def test_whitespace_and_quote_style_are_normalised():
    page = "Epiphone Les Paul Standard ’60s\n\nLeft  Handed"
    c = check_extraction(extraction(product_title="Epiphone Les Paul Standard '60s Left Handed",
                                    title_quote="Epiphone Les Paul Standard '60s Left Handed"), page, None)  # fmt: skip
    assert field(c, F.PRODUCT_TITLE).status is FieldStatus.EXTRACTED


def test_specifications_only_kept_when_value_is_on_page():
    c = check(specifications=[ExtractedSpec(name="Recording time", value="13 hours"),
                              ExtractedSpec(name="Weight", value="0.5 kg")])  # fmt: skip
    assert [s.value for s in c.specifications] == ["13 hours"]


# --------------------------------------------------------------------------- helpers


def test_price_parsing():
    assert parse_single_price("£1,299.00").amount == Decimal("1299.00")
    assert parse_single_price("189.99 GBP").currency == "GBP"
    assert parse_single_price("€99").currency == "EUR"
    assert parse_single_price("$99").currency is None
    assert parse_single_price("£2k") is None and parse_single_price("£10 or £12") is None
    t = "Was £599 Now £579"
    was, now = find_prices(t)
    assert is_sale_context(t, was) and not is_sale_context(t, now)
    t = "From £12.99"
    assert is_conditional_context(t, find_prices(t)[0])


def test_product_queries():
    assert product_queries("Boss", "RC-5", "Boss RC-5 Loop Station Guitar Effect Pedal", None, 1) == [
        "Boss RC-5 Loop Station Guitar Effect Pedal"]
    assert product_queries("Bromo", "BAT4CEL", "Bromo Tahoma Series Electro-Acoustic Guitar", None, 2) == [
        "Bromo BAT4CEL Bromo Tahoma Series Electro-Acoustic Guitar", "Bromo BAT4CEL"]
    q = product_queries("Boss", "RC-5", "Boss RC-5 Loop Station", "13 hours recording; 99 phrase memories", 1)
    assert q == ["Boss RC-5 Loop Station 13 hours recording"]
