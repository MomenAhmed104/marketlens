"""Variant-specific price association (audit fix, P-6): a price is kept only when exactly one
verified option matches every known catalogue variant attribute; otherwise AMBIGUOUS. Never the
lowest, first or default-option price. Deterministic; no network, no LLM."""

from decimal import Decimal

import pytest

from app.agents.intelligence.rules import check_extraction
from app.agents.intelligence.schemas import ExtractedField as F
from app.agents.intelligence.schemas import ExtractionIssue, FieldStatus, IssueKind, OptionPrice, PageType, ProductOption
from tests.intelligence_fakes import extraction


@pytest.fixture
def black(catalogue):
    """A right-handed electric guitar in Black (SB-021's row with a Black finish)."""
    return catalogue.products["SB-021"].model_copy(update={"product_name": "Cort G250 SE Electric Guitar, Black"})


def op(option, price):
    return OptionPrice(option=option, displayed_price=price, price_quote=f"{option} {price}")


def page_for(*options_prices, extra=""):
    return "Cort G250 SE Electric Guitar\n" + "\n".join(f"{o} {p}" for o, p in options_prices) + f"\n{extra}In stock"


def check(product, options_prices, main=None, selected=None, title=None, extra="", **kw):
    page = page_for(*options_prices, extra=extra)
    if selected:
        page += f"\nFinish: {' / '.join(o for o, _ in options_prices)} (selected: {selected})"
    ext = extraction(
        product_title=title, title_quote=title, displayed_price=main, currency="GBP" if main else None,
        price_quote=f"{selected} {main}" if main and selected else (main if main else None),
        option_prices=[op(o, p) for o, p in options_prices if not (selected and o == selected)],
        options=[ProductOption(name="Finish", values=[o for o, _ in options_prices], selected_value=selected,
                               quote=f"Finish: {' / '.join(o for o, _ in options_prices)} (selected: {selected})")]
        if selected else [],
        **kw,
    )  # fmt: skip
    c = check_extraction(ext, page + (f"\n{title}" if title else ""), "Cort G250 SE Electric Guitar", product)
    return c, next(e for e in c.evidence if e.field is F.PRICE)


def test_black_matches_black_when_other_finishes_have_different_prices(black):
    # The cheaper Sunburst is never chosen; the Black price is the only one matching the catalogue.
    c, ev = check(black, [("Black", "£499.00"), ("3-Colour Sunburst", "£479.00")], main="£499.00", selected="Black")
    assert ev.status is FieldStatus.EXTRACTED and c.price == Decimal("499.00") and c.currency == "GBP"
    assert "only option matching the catalogue variant" in ev.note
    # The other option price is kept as alternative information, marked as not matching.
    (sunburst,) = c.option_prices
    assert sunburst.amount == Decimal("479.00") and sunburst.matches_catalogue_variant is False
    assert sunburst.variant_checks["finish"] == "CONFLICT"


def test_black_option_price_chosen_from_option_list_without_main_price(black):
    c, ev = check(black, [("Black", "£499.00"), ("3-Colour Sunburst", "£479.00")])
    assert ev.status is FieldStatus.EXTRACTED and c.price == Decimal("499.00") and ev.quote == "Black £499.00"
    assert [o.matches_catalogue_variant for o in c.option_prices] == [True, False]


def test_black_left_handed_option_is_rejected_for_a_right_handed_product(black):
    c, ev = check(black, [("Black", "£499.00"), ("Black Left-Handed", "£529.00")])
    assert c.price == Decimal("499.00")
    lh = next(o for o in c.option_prices if o.option == "Black Left-Handed")
    assert lh.matches_catalogue_variant is False and lh.variant_checks["handedness"] == "CONFLICT"
    # Only the left-handed Black and another finish: nothing matches -> AMBIGUOUS, never the cheaper one.
    c, ev = check(black, [("Black Left-Handed", "£529.00"), ("3-Colour Sunburst", "£479.00")])
    assert ev.status is FieldStatus.AMBIGUOUS and c.price is None and "no option matches" in ev.note


def test_multiple_matching_options_are_ambiguous(black):
    c, ev = check(black, [("Black", "£499.00"), ("Black Gloss", "£519.00"), ("3-Colour Sunburst", "£479.00")])
    assert ev.status is FieldStatus.AMBIGUOUS and c.price is None and "several options match" in ev.note


def test_no_matching_option_is_ambiguous(catalogue):
    burgundy = catalogue.products["SB-021"]  # Vivid Burgundy, not offered on the page
    c, ev = check(burgundy, [("Black", "£279.00"), ("Olympic White", "£289.00")])
    assert ev.status is FieldStatus.AMBIGUOUS and c.price is None and "no option matches" in ev.note
    assert all(o.matches_catalogue_variant is False for o in c.option_prices)


def test_unverifiable_handedness_is_ambiguous_until_the_page_states_it(catalogue):
    lh = catalogue.products["SB-028"]  # left-handed, Iced Tea Burst
    opts = [("Iced Tea Burst", "£579.00"), ("Ebony", "£599.00")]
    c, ev = check(lh, opts)
    assert ev.status is FieldStatus.AMBIGUOUS and "handedness" in ev.note  # a finish match alone is not enough
    # A verified page title stating left-handed applies to every option (no option states handedness).
    c, ev = check(lh, opts, title="Epiphone Les Paul Standard '60s Left-Handed")
    assert ev.status is FieldStatus.EXTRACTED and c.price == Decimal("579.00")


def test_catalogue_without_a_finish_cannot_be_associated_by_colour(catalogue):
    rc5 = catalogue.products["SB-001"]  # Boss RC-5: the catalogue names no finish
    c, ev = check(rc5, [("Black", "£189.00"), ("White", "£199.00")])
    assert ev.status is FieldStatus.AMBIGUOUS and "finish" in ev.note


def test_configuration_must_match_too(catalogue):
    st_james = catalogue.products["SB-046"]  # Valve Head & 2x12 Cabinet, Black
    c, ev = check(st_james, [("Black Head only", "£899.00"), ("Black Head & 2x12 Cabinet", "£1,299.00")])
    assert ev.status is FieldStatus.EXTRACTED and c.price == Decimal("1299.00")
    head = next(o for o in c.option_prices if o.option == "Black Head only")
    assert head.variant_checks["configuration"] == "UNKNOWN"  # cabinet not stated


def test_unverified_option_prices_are_not_candidates(black):
    ext = extraction(option_prices=[OptionPrice(option="Black", displayed_price="£499.00", price_quote="Black £499.00"),
                                    OptionPrice(option="Sunburst", displayed_price="£479.00", price_quote="nowhere")])  # fmt: skip
    c = check_extraction(ext, "Cort G250 SE\nBlack £499.00\nIn stock", None, black)
    ev = next(e for e in c.evidence if e.field is F.PRICE)
    assert c.option_prices[1].status is FieldStatus.UNSUPPORTED
    assert ev.status is FieldStatus.EXTRACTED and c.price == Decimal("499.00")


def test_issue_naming_an_unaccounted_price_stays_ambiguous(black):
    issue = ExtractionIssue(kind=IssueKind.MULTIPLE_PRICES, fields=["price"], description="bundle price too",
                            quotes=["Black £499.00", "Bundle £559.00"])  # fmt: skip
    c, ev = check(black, [("Black", "£499.00"), ("3-Colour Sunburst", "£479.00")], extra="Bundle £559.00\n",
                  issues=[issue])  # fmt: skip
    assert ev.status is FieldStatus.AMBIGUOUS and "no option accounts for" in ev.note


def test_listing_pages_and_missing_product_context_never_associate(black):
    c, ev = check(black, [("Black", "£499.00"), ("3-Colour Sunburst", "£479.00")], page_type=PageType.LISTING_PAGE)
    assert ev.status is FieldStatus.AMBIGUOUS and c.price is None
    # Without the catalogue product (older callers), several prices stay AMBIGUOUS as before.
    c, ev = check(None, [("Black", "£499.00"), ("3-Colour Sunburst", "£479.00")], main="£499.00", selected="Black")
    assert ev.status is FieldStatus.AMBIGUOUS and c.price is None


def test_single_price_pages_are_unchanged(black):
    c, ev = check(black, [("Black", "£499.00"), ("3-Colour Sunburst", "£499.00")], main="£499.00", selected="Black")
    assert ev.status is FieldStatus.EXTRACTED and c.price == Decimal("499.00") and ev.note is None
