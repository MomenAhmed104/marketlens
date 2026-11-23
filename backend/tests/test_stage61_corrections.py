"""Stage 6.1 deterministic corrections from the live evaluation (run intelligence-20261119T193707Z-8189db):
1. delivery charges never set the sale flag or become a product price;
2. specification categories (power supply vs output power, controls vs electronics);
3. controlled finish aliases ("Ice Tea" = "Iced Tea Burst"). No network, no LLM."""

from decimal import Decimal

import pytest

from app.agents.intelligence.rules import check_extraction, spec_key
from app.agents.intelligence.schemas import ExtractedField as F
from app.agents.intelligence.schemas import ExtractedSpec, FieldStatus, IgnoredPriceCategory, NormalizedSpec, OptionPrice
from app.agents.intelligence.schemas import SpecCategory as C
from app.schemas.common import MatchStatus as M
from app.services.matching import attributes as A
from app.services.matching import match_candidate
from app.services.matching.schemas import CheckResult
from app.services.prices import find_prices, ignored_price_category, is_delivery_context
from app.services.specs import spec_category
from tests.intelligence_fakes import extraction
from tests.matching_fakes import NOW, candidate

# --------------------------------------------------------------------------- 1. delivery charges

# The exact delivery texts LLM 2 ignored on the Gear4music RC-5 page in the live run.
G4M_DELIVERY = ["EXPRESS CLICK & COLLECT locally £3.49", "NEXT available Working Day delivery £4.99",
                "SPECIFIC day delivery, MON-FRI £6.49", "SATURDAY delivery, 1hr window notification £7.49",
                "SUNDAY delivery, 1hr window notification £7.99", "SPECIFIC MORNING delivery, MON-FRI £9.99",
                "SPECIFIC MORNING delivery by 10.30AM, MON-FRI £13.49"]  # fmt: skip
PAGE = "Boss RC-5 Loop Station Guitar Loop Pedal\n£ 186.00 Price Includes VAT @ 20%\nWas £219.00 Now £189.00\n" \
       "RRP £249.00\nNext day delivery £4.99\n" + "\n".join(G4M_DELIVERY) + "\nFree delivery £186.00 today"  # fmt: skip


def price_check(**kw):
    c = check_extraction(extraction(**kw), PAGE, "Boss RC-5")
    return c, next(e for e in c.evidence if e.field is F.PRICE)


def test_multiple_delivery_options_never_set_the_sale_flag():
    c, ev = price_check(displayed_price="£ 186.00", currency="GBP", price_quote="£ 186.00 Price Includes VAT @ 20%",
                        ignored_prices=G4M_DELIVERY)  # fmt: skip
    assert ev.status is FieldStatus.EXTRACTED and c.price == Decimal("186.00")
    assert c.sale_indicated is False
    assert [r.category for r in c.ignored_price_details] == [IgnoredPriceCategory.DELIVERY] * len(G4M_DELIVERY)


def test_delivery_alongside_a_genuine_sale_keeps_the_sale_flag():
    c, ev = price_check(displayed_price="£189.00", currency="GBP", price_quote="Was £219.00 Now £189.00",
                        ignored_prices=["Was £219.00", "Next day delivery £4.99"])  # fmt: skip
    assert ev.status is FieldStatus.EXTRACTED and c.price == Decimal("189.00") and c.sale_indicated is True
    assert {r.text: r.category for r in c.ignored_price_details} == {
        "Was £219.00": IgnoredPriceCategory.SALE, "Next day delivery £4.99": IgnoredPriceCategory.DELIVERY}


def test_delivery_amount_never_becomes_the_product_price():
    _, ev = price_check(displayed_price="£4.99", currency="GBP", price_quote="Next day delivery £4.99")
    assert ev.status is FieldStatus.INVALID and "delivery" in ev.note
    # Same amount listed as a delivery charge (even with a neutral quote): never used.
    _, ev = price_check(displayed_price="£4.99", currency="GBP", price_quote="£4.99",
                        ignored_prices=["NEXT available Working Day delivery £4.99"])  # fmt: skip
    assert ev.status is FieldStatus.AMBIGUOUS and "delivery charge" in ev.note


def test_free_delivery_wording_does_not_disqualify_the_product_price():
    c, ev = price_check(displayed_price="£186.00", currency="GBP", price_quote="Free delivery £186.00 today")
    assert ev.status is FieldStatus.EXTRACTED and c.sale_indicated is False


@pytest.mark.parametrize("ignored,value,quote", [
    (["RRP £249.00"], "£249.00", "RRP £249.00"),  # the RRP itself returned as price
    (["Was £219.00"], "£219.00", "Was £219.00 Now £189.00"),  # the crossed-out figure
])  # fmt: skip
def test_rrp_and_crossed_out_prices_stay_excluded_and_flag_a_sale(ignored, value, quote):
    c, ev = price_check(displayed_price=value, currency="GBP", price_quote=quote, ignored_prices=ignored)
    assert ev.status is FieldStatus.EXCLUDED_SALE_PRICE and c.price is None and c.sale_indicated is True


def test_normal_product_price_has_no_sale_flag():
    c, ev = price_check(displayed_price="£ 186.00", currency="GBP", price_quote="£ 186.00 Price Includes VAT @ 20%")
    assert ev.status is FieldStatus.EXTRACTED and c.sale_indicated is False and c.ignored_price_details == []


def test_delivery_option_prices_are_not_product_options():
    c, _ = price_check(displayed_price="£ 186.00", currency="GBP", price_quote="£ 186.00 Price Includes VAT @ 20%",
                       option_prices=[OptionPrice(option="Next day", displayed_price="£4.99",
                                                  price_quote="Next day delivery £4.99")])  # fmt: skip
    assert c.option_prices[0].status is FieldStatus.INVALID and c.price == Decimal("186.00")


@pytest.mark.parametrize("text,category", [
    ("NEXT available Working Day delivery £4.99", "delivery"), ("£3.95 postage", "delivery"),
    ("EXPRESS CLICK & COLLECT locally £3.49", "delivery"), ("Save £5 on delivery", "delivery"),
    ("Was £219.00", "sale"), ("RRP £249.00", "sale"), ("Save £30", "sale"), ("15% off", "sale"),
    ("£15.75 per month", "finance"), ("Deposit £50", "finance"), ("Gig bag £29.99", "other"),
])  # fmt: skip
def test_ignored_price_categories(text, category):
    assert ignored_price_category(text) == category


def test_delivery_context_is_about_the_amount_itself():
    t = "Free UK delivery £219.00 | Next day delivery: £4.99 | £3.49 postage | delivery available. Our price £199.00"
    assert [is_delivery_context(t, m) for m in find_prices(t)] == [False, True, True, False]


# --------------------------------------------------------------------------- 2. specification categories


@pytest.mark.parametrize("name,value,category", [
    # guitar pedals (the live-run cases)
    ("Power", "9V AC Adapter/Battery", C.POWER_SUPPLY),
    ("Controls", "Memory/Loop level, Tempo, On/Off, Setup, Memory", C.CONTROLS),
    ("Power supply", "9V DC centre negative", C.POWER_SUPPLY),
    ("Current draw", "170 mA", C.POWER_SUPPLY),
    # amplifiers
    ("Power", "50W", C.OUTPUT_POWER), ("Output power", "15 Watts", C.OUTPUT_POWER),
    ("Power", "50W RMS, 230V mains", C.OTHER),  # mixed: output never inferred from the supply
    ("Output", "2 x 1/4 inch jack", C.OTHER), ("Power", "On/Off", C.OTHER),
    # electric guitars
    ("Pickups", "HSS", C.PICKUP_CONFIGURATION), ("Pickup configuration", "H-S-S", C.PICKUP_CONFIGURATION),
    ("Neck Pickup", "Gibson Custombucker", C.ELECTRONICS), ("Controls", "1 Volume, 2 Tone, 5-way switch", C.CONTROLS),
    # electro-acoustic guitars
    ("Electronics", "Fishman Presys II", C.ELECTRONICS), ("Pickup system", "LR Baggs Element", C.ELECTRONICS),
    ("Preamp", "Active 3-band EQ with tuner", C.ELECTRONICS), ("Body Material", "Mahogany", C.OTHER),
])  # fmt: skip
def test_spec_categories(name, value, category):
    assert spec_category(name, value) is category


def test_extracted_specs_get_categories_and_correct_keys():
    page = "Power: 9V AC Adapter/Battery\nControls: Memory/Loop level, Tempo, On/Off, Setup, Memory\nPower: 50W"
    ext = extraction(specifications=[
        ExtractedSpec(name="Power", value="9V AC Adapter/Battery", normalized_name="wattage"),  # LLM 2 hint ignored
        ExtractedSpec(name="Controls", value="Memory/Loop level, Tempo, On/Off, Setup, Memory", normalized_name="electronics"),
        ExtractedSpec(name="Power", value="50W")])  # fmt: skip
    specs = check_extraction(ext, page, None).specifications
    assert [(s.key, s.category) for s in specs] == [("power_supply", C.POWER_SUPPLY), ("controls", C.CONTROLS),
                                                   ("output_power", C.OUTPUT_POWER)]  # fmt: skip
    assert spec_key("Power") == "power"  # no value: undecided, never "wattage"


def with_specs(c, *specs, categorised=True):
    c.specifications = [NormalizedSpec(name=n, value=v, category=spec_category(n, v) if categorised else None)
                        for n, v in specs]  # fmt: skip
    return c


EXOTIC = "https://www.thomann.co.uk/cort-exotic-ga-walnut-sunburst"
EXOTIC_TITLE = "Cort Exotic-GA Walnut Solid Top Guitar, Sunburst"


def exotic(*specs, categorised=True):
    c = candidate(EXOTIC, EXOTIC_TITLE, llm=True, product_title=EXOTIC_TITLE, brand="Cort", model="Exotic-GA")
    return with_specs(c, *specs, categorised=categorised)


def electro(m):
    return next(v for v in m.variants if v.attribute == "electro_acoustic")


@pytest.mark.parametrize("categorised", [True, False])  # False: older records without a stored category
def test_controls_spec_never_confirms_an_electro_acoustic_preamp(catalogue, categorised):
    m = match_candidate(catalogue.products["SB-045"], exotic(("Controls", "Volume, Tone"), categorised=categorised), NOW, 24)
    assert electro(m).result is CheckResult.UNVERIFIED and m.match_status is M.REVIEW_REQUIRED
    m = match_candidate(catalogue.products["SB-045"], exotic(("Power", "9V battery"), categorised=categorised), NOW, 24)
    assert electro(m).result is CheckResult.UNVERIFIED


def test_explicit_electronics_spec_confirms_or_contradicts_electro_acoustic(catalogue):
    m = match_candidate(catalogue.products["SB-045"], exotic(("Electronics", "Fishman Presys II")), NOW, 24)
    assert electro(m).result is CheckResult.MATCH and m.match_status is M.EXACT_MATCH
    m = match_candidate(catalogue.products["SB-045"], exotic(("Pickup", "None")), NOW, 24)
    assert electro(m).result is CheckResult.CONFLICT and m.match_status is M.SIMILAR_MATCH


def test_pedal_power_and_controls_specs_do_not_touch_matching(catalogue):
    c = candidate("https://www.andertons.co.uk/boss-rc-5-loop-station-pedal", "Boss RC-5 Loop Station Pedal", llm=True,
                  product_title="Boss RC-5 Loop Station Pedal", brand="Boss", model="RC-5")  # fmt: skip
    with_specs(c, ("Power", "9V AC Adapter/Battery"), ("Controls", "Memory/Loop level, Tempo, On/Off, Setup, Memory"))
    m = match_candidate(catalogue.products["SB-001"], c, NOW, 24)
    assert m.match_status is M.EXACT_MATCH
    assert all(v.result is CheckResult.NOT_APPLICABLE for v in m.variants
               if v.attribute in ("pickups", "electronics", "electro_acoustic", "wattage"))  # fmt: skip


def test_electric_guitar_pickup_layout_only_from_pickup_specs(catalogue):
    hss = catalogue.products["SB-021"].model_copy(update={"product_name": "Cort G250 SE HSS Electric Guitar, Vivid Burgundy"})
    title = "Cort G250 SE Electric Guitar Vivid Burgundy"

    def run(*specs):
        c = candidate("https://www.thomann.co.uk/cort-g250-se-vivid-burgundy", title, llm=True, product_title=title,
                      brand="Cort", model="G250 SE")  # fmt: skip
        return next(v for v in match_candidate(hss, with_specs(c, *specs), NOW, 24).variants if v.attribute == "pickups")

    assert run(("Pickups", "HSS")).result is CheckResult.MATCH
    assert run(("Pickups", "HH")).result is CheckResult.CONFLICT
    assert run(("Controls", "HSS 5-way switch")).result is CheckResult.UNVERIFIED  # controls are not layout evidence


def test_active_passive_needs_an_electronics_spec_not_a_controls_spec(catalogue):
    bass = catalogue.products["SB-029"].model_copy(update={"product_name": "Ashdown Roasted Capri Active Bass Guitar, Ice Blue"})
    title = "Ashdown Roasted Capri Bass Ice Blue"

    def run(*specs):
        c = candidate("https://www.thomann.co.uk/ashdown-roasted-capri-ice-blue", title, llm=True, product_title=title,
                      brand="Ashdown", model="Roasted Capri")  # fmt: skip
        return next(v for v in match_candidate(bass, with_specs(c, *specs), NOW, 24).variants if v.attribute == "electronics")

    assert run(("Controls", "Active/passive switch, Volume, Blend")).result is CheckResult.UNVERIFIED
    assert run(("Electronics", "Active 3-band EQ")).result is CheckResult.MATCH
    assert run(("Electronics", "Passive")).result is CheckResult.CONFLICT


# --------------------------------------------------------------------------- 3. finish aliases


@pytest.mark.parametrize("text", ["Ice Tea", "ICE TEA", "ice-tea", "Epiphone Les Paul Standard 60s, Ice Tea - Secondhand",
                                  "Iced Tea Burst", "iced  tea  burst",
                                  "Iced Tea", "in Iced Tea with Gig Bag"])  # fmt: skip  # "iced tea": approved 20 Nov 2026
def test_iced_tea_burst_and_its_controlled_alias(text):
    assert A.finish_in("Iced Tea Burst", text)


@pytest.mark.parametrize("text", ["Iced", "Tea", "Spice Tea", "Ice", "Bourbon Burst", "Tea Burst", "Icedtea", ""])
def test_unregistered_or_ambiguous_names_are_not_the_finish(text):
    assert not A.finish_in("Iced Tea Burst", text)


def finish_check(catalogue, product_name, title):
    p = catalogue.products["SB-021"].model_copy(update={"product_name": product_name})
    c = candidate("https://www.thomann.co.uk/cort-g250-se", title)
    m = match_candidate(p, c, NOW, 24)
    return m, next(v for v in m.variants if v.attribute == "finish")


def test_live_run_ice_tea_page_no_longer_conflicts(catalogue):
    c = candidate("https://www.gear4music.com/Guitar-and-Bass/Epiphone-Les-Paul-Standard-60s-Ice-Tea-Secondhand/5OD5",
                  "Epiphone Les Paul Standard 60s, Ice Tea - Secondhand")  # fmt: skip
    m = match_candidate(catalogue.products["SB-028"], c, NOW, 24)
    finish = next(v for v in m.variants if v.attribute == "finish")
    assert finish.result is CheckResult.MATCH
    assert m.match_status is M.SIMILAR_MATCH  # still secondhand; handedness still unconfirmed
    assert next(v for v in m.variants if v.attribute == "handedness").result is CheckResult.UNVERIFIED


def test_same_finish_matches(catalogue):
    p = catalogue.products["SB-028"]
    c = candidate("https://www.andertons.co.uk/epiphone-les-paul-standard-60s-figured-left-handed-iced-tea-burst",
                  "Epiphone Les Paul Standard '60s Figured Left-Handed, Iced Tea Burst")  # fmt: skip
    m = match_candidate(p, c, NOW, 24)
    assert next(v for v in m.variants if v.attribute == "finish").result is CheckResult.MATCH
    assert m.match_status is M.EXACT_MATCH  # SB-028 is the approved Figured model (20 Nov 2026)


def test_black_vs_white_is_still_a_conflict(catalogue):
    m, finish = finish_check(catalogue, "Cort G250 SE Electric Guitar, Black", "Cort G250 SE Electric Guitar White")
    assert finish.result is CheckResult.CONFLICT and m.match_status is M.EQUIVALENT_VARIANT  # cosmetic only (U-1)


def test_cherry_sunburst_is_never_vintage_sunburst(catalogue):
    m, finish = finish_check(catalogue, "Cort G250 SE Electric Guitar, Vintage Sunburst",
                             "Cort G250 SE Electric Guitar Cherry Sunburst")  # fmt: skip
    # Shared word "sunburst": not confirmed, never a match or equivalence -> review, as before.
    assert finish.result is CheckResult.UNVERIFIED and m.match_status is M.REVIEW_REQUIRED


def test_alias_is_used_for_variant_price_association(catalogue):
    page = "Epiphone Les Paul Standard '60s Left-Handed\nIce Tea Left-Handed £579.00\nEbony Left-Handed £599.00\n"
    ext = extraction(option_prices=[
        OptionPrice(option="Ice Tea Left-Handed", displayed_price="£579.00", price_quote="Ice Tea Left-Handed £579.00"),
        OptionPrice(option="Ebony Left-Handed", displayed_price="£599.00", price_quote="Ebony Left-Handed £599.00")])  # fmt: skip
    c = check_extraction(ext, page, None, catalogue.products["SB-028"])
    assert c.price == Decimal("579.00") and [o.matches_catalogue_variant for o in c.option_prices] == [True, False]


def test_alias_registry_is_controlled():
    # "iced tea" approved 20 Nov 2026; "itb" (Thomann abbreviation) approved 23 Nov 2026.
    assert A.FINISH_ALIASES == {"iced tea burst": ("ice tea", "iced tea", "itb")}
    assert A.finish_names("Iced Tea Burst") == ["iced tea burst", "ice tea", "iced tea", "itb"]
    assert A.finish_names("Vintage Sunburst") == ["vintage sunburst"]  # no implicit aliases
