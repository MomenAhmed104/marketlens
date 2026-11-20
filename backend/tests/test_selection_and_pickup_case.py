"""Reliability fixes: (1) an option counts as selected only with explicit selection evidence in its
verified quote; (2) pickup layouts are detected in any letter case. No network, no LLM."""

from decimal import Decimal

import pytest

from app.agents.intelligence.rules import check_extraction, selection_evidence
from app.agents.intelligence.schemas import ExtractedField as F
from app.agents.intelligence.schemas import FieldStatus, NormalizedSpec, OptionPrice, ProductOption
from app.schemas.common import MatchStatus as M
from app.services.matching import attributes as A
from app.services.matching import match_candidate
from app.services.matching.schemas import CheckResult
from tests.intelligence_fakes import extraction
from tests.matching_fakes import NOW, candidate

# --------------------------------------------------------------------------- selection evidence


@pytest.mark.parametrize("quote", [
    "Selected: Black", "Selected colour - Black", "Chosen finish = Black", "Selection: Black",
    "Black (selected)", "Black [currently selected]", "Black - selected", "✓ Black", "Black ✔", "[x] Black",
    "You have selected Black", "Currently selected colour: Black",
    "Finish: Black / 3-Colour Sunburst (selected: Black)",
])  # fmt: skip
def test_explicit_selection_markers_are_accepted(quote):
    assert selection_evidence("Black", quote)


@pytest.mark.parametrize("quote", [
    "Colour: Black",  # heading form: real pages use it for spec bullets too ("- Colour: Ocean Blue Grey")
    "- Colour: Black",
    "Finish: Black / 3-Colour Sunburst",
    "Carefully selected black spruce top",  # prose, no separator
    "Black Sunburst (selected)",  # another value is marked
    "Selected: Black Sunburst",  # another value is marked
    "Black",
])  # fmt: skip
def test_appearing_on_the_page_is_not_selection(quote):
    assert selection_evidence("Black", quote) is None


@pytest.fixture
def black(catalogue):
    return catalogue.products["SB-021"].model_copy(update={"product_name": "Cort G250 SE Electric Guitar, Black"})


PAGE = """Cort G250 SE Electric Guitar
Colour: Black
Finish: Black / 3-Colour Sunburst
3-Colour Sunburst £479.00
Our price £499.00
In stock"""


def run(product, page, option_quote, selected="Black", option_prices=None, values=("Black", "3-Colour Sunburst")):
    ext = extraction(
        displayed_price="£499.00", currency="GBP", price_quote="Our price £499.00",
        options=[ProductOption(name="Finish", values=list(values), selected_value=selected, quote=option_quote)],
        option_prices=option_prices or [OptionPrice(option="3-Colour Sunburst", displayed_price="£479.00",
                                                    price_quote="3-Colour Sunburst £479.00")],
    )  # fmt: skip
    c = check_extraction(ext, page, "Cort G250 SE Electric Guitar", product)
    return c, next(e for e in c.evidence if e.field is F.PRICE)


def test_unverified_selection_is_not_used_for_the_main_price(black):
    # LLM 2 claims Black is selected, but the quote ("Colour: Black") only shows a heading.
    c, ev = run(black, PAGE, "Colour: Black")
    (opt,) = c.options
    assert opt.selected_value is None and opt.claimed_selected_value == "Black" and opt.selection_evidence is None
    assert opt.values == ["Black", "3-Colour Sunburst"]  # option information kept
    assert ev.status is FieldStatus.AMBIGUOUS and c.price is None  # not assumed to be the Stompbox variant
    assert c.option_prices[0].matches_catalogue_variant is False  # Sunburst stays alternative information


def test_explicitly_selected_option_labels_the_main_price(black):
    page = PAGE.replace("Colour: Black", "Selected colour: Black")
    c, ev = run(black, page, "Selected colour: Black")
    assert c.options[0].selected_value == "Black" and c.options[0].selection_evidence == "selected colour: black"
    assert ev.status is FieldStatus.EXTRACTED and c.price == Decimal("499.00")


def test_selection_does_not_bypass_strict_variant_matching(black):
    quote = "Finish: Black / Black Left-Handed (selected)"
    page = PAGE.replace("Finish: Black / 3-Colour Sunburst", quote)
    c, ev = run(black, page, quote, selected="Black Left-Handed", values=("Black", "Black Left-Handed"))
    # The selection is verified, but a left-handed option never matches a right-handed product.
    assert c.options[0].selected_value == "Black Left-Handed" and c.options[0].selection_evidence
    assert ev.status is FieldStatus.AMBIGUOUS and c.price is None


def test_partial_value_marked_selected_does_not_select_the_shorter_value(black):
    quote = "Finish: Jet Black (selected) / 3-Colour Sunburst"
    c, ev = run(black, PAGE + f"\n{quote}", quote, selected="Black", values=("Black", "3-Colour Sunburst"))
    assert c.options[0].selected_value is None and c.options[0].claimed_selected_value == "Black"
    assert ev.status is FieldStatus.AMBIGUOUS


def test_option_specific_prices_do_not_need_a_selection(black):
    # A price attached to the Black option itself is option evidence, not a selection claim.
    page = PAGE + "\nBlack £499.00"
    c, ev = run(black, page, "Colour: Black", option_prices=[
        OptionPrice(option="Black", displayed_price="£499.00", price_quote="Black £499.00"),
        OptionPrice(option="3-Colour Sunburst", displayed_price="£479.00", price_quote="3-Colour Sunburst £479.00")])  # fmt: skip
    assert ev.status is FieldStatus.EXTRACTED and c.price == Decimal("499.00") and ev.quote == "Black £499.00"


# --------------------------------------------------------------------------- pickup case


@pytest.mark.parametrize("text", ["HSS", "hss", "Hss", "hSs", "Cort G250 SE hss", "cort-g250-se-hss-black"])
def test_pickup_layouts_are_detected_in_any_case(text):
    assert A.pickup_configs(text) == {"HSS"}


@pytest.mark.parametrize("text", ["class", "hhh", "shh", "hss2", "Thrash", "Ashdown"])
def test_pickup_codes_inside_words_never_match(text):
    assert A.pickup_configs(text) == set()


@pytest.fixture
def hss(catalogue):
    return catalogue.products["SB-021"].model_copy(update={"product_name": "Cort G250 SE hss Electric Guitar, Vivid Burgundy"})


def cort(title, url="https://www.thomann.co.uk/cort-g250-se-vivid-burgundy", specs=()):
    c = candidate(url, title, llm=True, product_title=title, brand="Cort", model="G250 SE")
    c.specifications = [NormalizedSpec(name=n, value=v, key=k) for n, v, k in specs]
    return c


def check(m, attr):
    return next(v for v in m.variants if v.attribute == attr)


def test_lower_case_catalogue_and_competitor_layouts_compare_identically(catalogue, hss):
    assert A.profile(hss).pickups == {"HSS"}
    m = match_candidate(hss, cort("Cort G250 SE Electric Guitar Vivid Burgundy", specs=[("Pickups", "Hss", "pickups")]),
                        NOW, 24)  # fmt: skip
    assert m.match_status is M.EXACT_MATCH and check(m, "pickups").result is CheckResult.MATCH
    m = match_candidate(hss, cort("cort g250 se hh electric guitar vivid burgundy"), NOW, 24)
    assert m.match_status is M.SIMILAR_MATCH and check(m, "pickups").found == ["HH"]


def test_lower_case_layout_in_a_url_is_detected(catalogue):
    # Catalogue silent; the URL slug names an HH variant -> review, as for an upper-case title.
    m = match_candidate(catalogue.products["SB-021"],
                        cort("Cort G250 SE Electric Guitar Vivid Burgundy",
                             url="https://www.thomann.co.uk/cort-g250-se-hh-vivid-burgundy"), NOW, 24)  # fmt: skip
    assert m.match_status is M.REVIEW_REQUIRED and check(m, "pickups").found == ["HH"]
