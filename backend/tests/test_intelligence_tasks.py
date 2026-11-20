"""LLM 2 Task B additions (options, option prices, issues, spec keys, availability) and
Task A proposal verification. Deterministic; no network, no LLM."""

from decimal import Decimal

from app.agents.intelligence.rules import check_extraction, check_match_proposal, is_cosmetic, spec_key
from app.agents.intelligence.schemas import ExtractedField as F
from app.agents.intelligence.schemas import (
    ExtractedSpec,
    ExtractionIssue,
    FieldStatus,
    IssueKind,
    OptionPrice,
    PageType,
    ProductOption,
    ProposalStatus,
    ProposedMatch,
)
from app.schemas.common import AvailabilityStatus as A
from app.schemas.common import MatchStatus
from tests.intelligence_fakes import extraction, proposal

PAGE = """Cort G250 SE Electric Guitar | Example Music
Cort G250 SE Electric Guitar
Colour: Vivid Burgundy / Black / Olympic White (selected: Vivid Burgundy)
Vivid Burgundy £299.00
Black £279.00
Was £329.00 Now £299.00
Only 2 left - Add to basket
Number of strings: 6
Pickups: HSS
"""
TITLE = "Cort G250 SE Electric Guitar | Example Music"
BASE = dict(product_title="Cort G250 SE Electric Guitar", title_quote="Cort G250 SE Electric Guitar",
            displayed_price="£299.00", currency="GBP", price_quote="Was £329.00 Now £299.00",
            ignored_prices=["Was £329.00"], availability="Only 2 left", availability_quote="Only 2 left - Add to basket")  # fmt: skip


def check(**kw):
    return check_extraction(extraction(**{**BASE, **kw}), PAGE, TITLE)


def price_ev(c):
    return next(e for e in c.evidence if e.field is F.PRICE)


# --------------------------------------------------------------------------- Task B: prices


def test_single_current_price_still_extracted_with_matching_option_price():
    c = check(option_prices=[OptionPrice(option="Vivid Burgundy", displayed_price="£299.00",
                                         price_quote="Vivid Burgundy £299.00")])  # fmt: skip
    assert price_ev(c).status is FieldStatus.EXTRACTED and c.price == Decimal("299.00")
    assert c.option_prices[0].status is FieldStatus.EXTRACTED and c.option_prices[0].amount == Decimal("299.00")


def test_multiple_current_prices_make_the_price_ambiguous_and_none_is_selected():
    c = check(option_prices=[OptionPrice(option="Vivid Burgundy", displayed_price="£299.00",
                                         price_quote="Vivid Burgundy £299.00"),
                             OptionPrice(option="Black", displayed_price="£279.00", price_quote="Black £279.00")])  # fmt: skip
    ev = price_ev(c)
    assert ev.status is FieldStatus.AMBIGUOUS and "279.00" in ev.note and "299.00" in ev.note
    assert c.price is None and c.currency is None and c.price_text is None  # the lower price is never chosen
    assert [o.status for o in c.option_prices] == [FieldStatus.EXTRACTED, FieldStatus.EXTRACTED]


def test_sale_labelled_option_price_is_excluded_and_does_not_count_as_a_second_price():
    c = check(option_prices=[OptionPrice(option="Vivid Burgundy", displayed_price="£329.00",
                                         price_quote="Was £329.00 Now £299.00")])  # fmt: skip
    assert c.option_prices[0].status is FieldStatus.EXCLUDED_SALE_PRICE
    assert price_ev(c).status is FieldStatus.EXTRACTED and c.price == Decimal("299.00")


def test_unsupported_option_price_is_ignored():
    c = check(option_prices=[OptionPrice(option="Left-handed", displayed_price="£319.00", price_quote="LH £319.00")])
    assert c.option_prices[0].status is FieldStatus.UNSUPPORTED and price_ev(c).status is FieldStatus.EXTRACTED


def test_verified_multiple_prices_issue_with_two_amounts_makes_price_ambiguous():
    issue = ExtractionIssue(kind=IssueKind.MULTIPLE_PRICES, fields=["price"], description="two colour prices",
                            quotes=["Vivid Burgundy £299.00", "Black £279.00"])  # fmt: skip
    c = check(issues=[issue])
    assert c.issues[0].verified and price_ev(c).status is FieldStatus.AMBIGUOUS and c.price is None


def test_unverified_issue_has_no_effect():
    issue = ExtractionIssue(kind=IssueKind.MULTIPLE_PRICES, fields=["price"], description="invented",
                            quotes=["Blue £199.00", "Black £279.00"])  # fmt: skip
    c = check(issues=[issue])
    assert not c.issues[0].verified and price_ev(c).status is FieldStatus.EXTRACTED


# --------------------------------------------------------------------------- Task B: options, specs, availability


def test_options_kept_only_when_quote_and_values_are_on_the_page():
    # The quote includes the page's explicit selection marker, so the selected value is kept.
    ok = ProductOption(name="Colour", values=["Vivid Burgundy", "Black"], selected_value="Vivid Burgundy",
                       quote="Colour: Vivid Burgundy / Black / Olympic White (selected: Vivid Burgundy)")  # fmt: skip
    bad = ProductOption(name="Hand", values=["Left", "Right"], selected_value="Right", quote="Hand: Left / Right")
    invented_value = ProductOption(name="Colour", values=["Sunburst"], selected_value=None,
                                   quote="Colour: Vivid Burgundy")  # fmt: skip
    c = check(options=[ok, bad, invented_value])
    assert [(o.name, o.selected_value) for o in c.options] == [("Colour", "Vivid Burgundy")]


def test_specifications_get_deterministic_keys_and_keep_the_verbatim_check():
    c = check(specifications=[ExtractedSpec(name="Number of strings", value="6"),
                              ExtractedSpec(name="Pickup layout", value="HSS", normalized_name="pickups"),
                              ExtractedSpec(name="Fret wire", value="Medium jumbo")])  # fmt: skip
    assert [(s.name, s.key) for s in c.specifications] == [("Number of strings", "strings"), ("Pickup layout", "pickups")]
    assert spec_key("Body Colour") == "finish" and spec_key("Neck profile") == "neck_profile"
    # LLM 2's proposed name may supply ordinary keys, but never a category-dependent one (Stage 6.1).
    assert spec_key("Something", "scale length") == "scale_length"
    assert spec_key("Something", "wattage") == "something" and spec_key("Something", "electronics") == "something"


def test_low_stock_on_a_buyable_page_is_in_stock():
    c = check()
    assert c.availability_status is A.IN_STOCK and "Only 2 left" in c.availability_reason


def test_availability_disagreement_and_conflict_issue_give_unknown():
    assert check(availability_normalized=A.OUT_OF_STOCK).availability_status is A.UNKNOWN
    issue = ExtractionIssue(kind=IssueKind.CONFLICTING_INFORMATION, fields=["availability"],
                            description="in stock and sold out", quotes=["Only 2 left"])  # fmt: skip
    assert check(issues=[issue]).availability_status is A.UNKNOWN
    unsupported = check(availability="In stock", availability_quote="In stock now")  # quote not on the page
    assert unsupported.availability_status is A.UNKNOWN and unsupported.values[F.AVAILABILITY] is None


def test_existing_price_rules_unchanged_by_the_additions():
    c = check(displayed_price="£329.00")  # the "was" figure
    assert price_ev(c).status is FieldStatus.EXCLUDED_SALE_PRICE and c.price is None
    listing = check(page_type=PageType.LISTING_PAGE)
    assert price_ev(listing).status is FieldStatus.AMBIGUOUS


# --------------------------------------------------------------------------- Task A


def test_proposal_with_verified_quotes_is_completed_and_mapped():
    p = proposal(ProposedMatch.SIMILAR_ALTERNATIVE, reasons=[("Different pickups", "Pickups: HSS")],
                 diffs=[("pickups", "SSS", "HSS", "Pickups: HSS")])  # fmt: skip
    status, rec = check_match_proposal(p, PAGE, TITLE, ["gen-9"])
    assert status is ProposalStatus.COMPLETED and rec.corresponding_status is MatchStatus.SIMILAR_MATCH
    assert rec.reasons[0].verified and rec.differing_attributes[0].verified
    assert rec.differing_attributes[0].cosmetic is False and rec.request_ids == ["gen-9"]


def test_proposal_without_any_verified_quote_is_unsupported():
    status, rec = check_match_proposal(proposal(reasons=[("Same model", "Cort G250 Deluxe")]), PAGE, TITLE)
    assert status is ProposalStatus.UNSUPPORTED and not rec.reasons[0].verified
    status, _ = check_match_proposal(proposal(), PAGE, TITLE)  # no quotes at all
    assert status is ProposalStatus.UNSUPPORTED


def test_quote_from_the_catalogue_product_only_is_rejected():
    # "Sonic Blue" appears in the catalogue product, never in the page text.
    p = proposal(ProposedMatch.EQUIVALENT_VARIANT, diffs=[("finish", "Sonic Blue", "Sonic Blue", "Sonic Blue")])
    status, rec = check_match_proposal(p, PAGE, TITLE)
    assert status is ProposalStatus.UNSUPPORTED and not rec.differing_attributes[0].verified


def test_page_value_must_be_in_its_quote():
    p = proposal(diffs=[("finish", "Black", "Sunburst", "Vivid Burgundy £299.00")])
    _, rec = check_match_proposal(p, PAGE, TITLE)
    assert not rec.differing_attributes[0].verified


def test_cosmetic_is_decided_in_python_from_the_attribute_name():
    assert all(is_cosmetic(a) for a in ("finish", "Colour", "color", "body finish", "Colour/Finish"))
    assert not any(is_cosmetic(a) for a in ("handedness", "pickups", "size", "model", "hardware", "electronics"))


# --------------------------------------------------------------------------- Stage 6.3.1: geography (Task B path)

GEO_PAGE = """Boss RC-5 Loop Station Guitar Loop Pedal - Secondhand | Example Music
Boss RC-5 Loop Station Guitar Loop Pedal - Secondhand
This product is currently unavailable for delivery to your country
"""
GEO_TEXT = "This product is currently unavailable for delivery to your country"


def test_verified_geographic_restriction_is_unknown_whatever_llm2_proposes():
    for proposed in (A.OUT_OF_STOCK, A.UNKNOWN, None):
        c = check_extraction(extraction(product_title="Boss RC-5 Loop Station Guitar Loop Pedal - Secondhand",
                                        title_quote="Boss RC-5 Loop Station Guitar Loop Pedal - Secondhand",
                                        availability=GEO_TEXT, availability_quote=GEO_TEXT,
                                        availability_normalized=proposed),
                             GEO_PAGE, "Boss RC-5 Loop Station Guitar Loop Pedal - Secondhand | Example Music")  # fmt: skip
        assert c.availability_status is A.UNKNOWN, proposed
        assert "geographic or delivery restriction" in c.availability_reason


def test_generic_unavailability_on_a_restricted_page_is_unknown_in_task_b():
    page = GEO_PAGE + "This product is currently not available for purchase\n"
    c = check_extraction(extraction(product_title="Boss RC-5 Loop Station Guitar Loop Pedal - Secondhand",
                                    title_quote="Boss RC-5 Loop Station Guitar Loop Pedal - Secondhand",
                                    availability="This product is currently not available for purchase",
                                    availability_quote="This product is currently not available for purchase",
                                    availability_normalized=A.OUT_OF_STOCK), page, "t")  # fmt: skip
    assert c.availability_status is A.UNKNOWN
