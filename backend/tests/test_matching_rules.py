"""Phase 6 matching rules on controlled synthetic candidates (real catalogue products, no LLM/key)."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.agents.intelligence.schemas import ExtractionStatus, FieldStatus, PageType
from app.schemas.common import EvidenceStatus
from app.schemas.common import MatchStatus as M
from app.schemas.competitor import CompanyType as T
from app.schemas.search import RetrievalStatus
from app.services.matching import match_candidate
from app.services.matching.schemas import CheckResult, Condition, MatchConfidence, PageKind, PriceStatus
from tests.matching_fakes import NOW, candidate, target

AND_RC5 = "https://www.andertons.co.uk/boss-rc-5-loop-station-pedal"


@pytest.fixture
def run(catalogue):
    def _run(pid, cand, max_age=24):
        return match_candidate(catalogue.products[pid], cand, NOW, max_age)

    return _run


def variant(m, attr):
    return next(v for v in m.variants if v.attribute == attr)


# --------------------------------------------------------------------------- exact


def test_exact_from_title_and_url_in_no_llm_mode(run):
    m = run("SB-001", candidate(AND_RC5, "Boss RC-5 Loop Station Pedal | Andertons Music Co."))
    assert m.match_status is M.EXACT_MATCH and m.match_confidence is MatchConfidence.MEDIUM
    assert m.brand.result is CheckResult.MATCH and m.model.result is CheckResult.MATCH
    assert {e.source.value for e in m.model.evidence} >= {"page_title", "url"}
    assert m.page.kind is PageKind.UNKNOWN
    # Match valid, price not: separate concepts.
    assert m.price.status is PriceStatus.NOT_EXTRACTED and m.comparison.eligible is False
    assert "Price status is NOT_EXTRACTED" in m.comparison.reasons


def test_exact_with_llm_fields_and_valid_price_is_comparable(run, catalogue):
    m = run("SB-001", candidate(AND_RC5, "Boss RC-5 Loop Station", llm=True, product_title="Boss RC-5 Loop Station",
                                brand="BOSS", model="RC-5", price="189.00", quote="£189.00 In stock"))
    assert m.match_status is M.EXACT_MATCH and m.match_confidence is MatchConfidence.HIGH
    assert m.price.status is PriceStatus.VALID and m.price.evidence_status is EvidenceStatus.VERIFIED
    assert m.price.price == Decimal("189.00") and m.price.currency == "GBP" and m.price.quote == "£189.00 In stock"
    c = m.comparison
    assert c.eligible and c.reasons == [] and c.stompbox_price == catalogue.products["SB-001"].displayed_price
    assert c.difference_gbp == Decimal("189.00") - Decimal("189.99")


def test_title_alone_is_not_enough_for_exact(run):
    m = run("SB-001", candidate("https://www.andertons.co.uk/p/123456", "Boss RC-5 Loop Station"))
    assert m.match_status is M.REVIEW_REQUIRED
    assert any("one evidence group" in r for r in m.review_reasons)


def test_descriptive_model_with_generation_split_across_title(run):
    m = run("SB-070", candidate("https://www.andertons.co.uk/keeley-caverns-v2-delay-reverb",
                                "Keeley Caverns Delay & Reverb V2"))
    assert m.match_status is M.EXACT_MATCH and variant(m, "generation").result is CheckResult.MATCH


# --------------------------------------------------------------------------- wrong brand / model


def test_sibling_model_is_similar_not_exact(run):
    m = run("SB-001", candidate("https://www.andertons.co.uk/boss-rc-500-loop-station", "Boss RC-500 Loop Station"))
    assert m.match_status is M.SIMILAR_MATCH and m.model.result is CheckResult.CONFLICT and "rc500" in m.model.found


def test_extracted_different_model_same_brand_is_similar(run):
    m = run("SB-001", candidate("https://www.andertons.co.uk/boss-loop-station", "Boss Loop Station", llm=True,
                                brand="Boss", model="RC-1", product_title="Boss Loop Station"))
    assert m.match_status is M.SIMILAR_MATCH and m.model.found == ["RC-1"]


def test_wrong_brand_is_no_match(run):
    m = run("SB-001", candidate("https://www.andertons.co.uk/tc-ditto-x2-looper", "TC Electronic Ditto X2 Looper",
                                llm=True, brand="TC Electronic", model="Ditto X2"))
    assert m.match_status is M.NO_MATCH and m.brand.result is CheckResult.CONFLICT


def test_same_code_other_family_wrong_brand_text_never_exact(run):
    # The model code appears but the extracted brand is different: never the same product.
    m = run("SB-001", candidate(AND_RC5, "Roland RC-5 adapter", llm=True, brand="Roland", model="RC-5"))
    assert m.match_status is M.NO_MATCH


def test_missing_model_number(run):
    no_llm = run("SB-001", candidate("https://www.andertons.co.uk/boss-looper-pedal", "Boss Looper Pedal"))
    assert no_llm.match_status is M.NO_MATCH  # page type unknown: not even similar
    with_llm = run("SB-001", candidate("https://www.andertons.co.uk/boss-looper-pedal", "Boss Looper Pedal",
                                       llm=True, brand="Boss", product_title="Boss Looper Pedal"))
    assert with_llm.match_status is M.SIMILAR_MATCH and with_llm.model.result is CheckResult.UNVERIFIED


# --------------------------------------------------------------------------- variants


def test_left_handed_must_be_confirmed(run):
    lh = "https://www.andertons.co.uk/epiphone-les-paul-standard-60s-left-handed-iced-tea-burst"
    m = run("SB-028", candidate(lh, "Epiphone Les Paul Standard '60s Left-Handed, Iced Tea Burst"))
    assert m.match_status is M.EXACT_MATCH and variant(m, "handedness").result is CheckResult.MATCH
    assert variant(m, "finish").result is CheckResult.MATCH
    rh = "https://www.andertons.co.uk/epiphone-les-paul-standard-60s-iced-tea-burst"
    m = run("SB-028", candidate(rh, "Epiphone Les Paul Standard '60s, Iced Tea Burst"))
    assert m.match_status is M.REVIEW_REQUIRED and any("handedness" in r for r in m.review_reasons)


def test_left_handed_listing_never_matches_right_handed_product(run):
    m = run("SB-021", candidate("https://www.thomann.co.uk/cort-g250-se-lh-vivid-burgundy",
                                "Cort G250 SE LH Vivid Burgundy"))
    assert m.match_status is M.SIMILAR_MATCH and variant(m, "handedness").result is CheckResult.CONFLICT


def test_finish_conflict_and_partial_finish(run):
    m = run("SB-021", candidate("https://www.thomann.co.uk/cort-g250-se-black", "Cort G250 SE Electric Guitar Black"))
    assert m.match_status is M.SIMILAR_MATCH and variant(m, "finish").result is CheckResult.CONFLICT
    lh = "https://www.andertons.co.uk/epiphone-les-paul-standard-60s-left-handed-bourbon-burst"
    m = run("SB-028", candidate(lh, "Epiphone Les Paul Standard '60s Left-Handed, Bourbon Burst"))
    assert m.match_status is M.REVIEW_REQUIRED and variant(m, "finish").result is CheckResult.UNVERIFIED


def test_finish_words_inside_the_model_name_are_not_finishes(run):
    # "White Light" is the model; the colour word must not be read as a conflicting finish.
    m = run("SB-054", candidate("https://www.andertons.co.uk/eqd-white-light-limited-edition-reissue-overdrive",
                                "EarthQuaker Devices White Light Limited Edition Reissue Overdrive"))
    assert variant(m, "finish").result is CheckResult.NOT_APPLICABLE and m.match_status is M.EXACT_MATCH


def test_size_and_string_count_conflicts(run):
    m = run("SB-037", candidate("https://www.thomann.co.uk/aria-209n-3-4-size", "Aria 209N 3/4 Size Acoustic"))
    assert m.match_status is M.SIMILAR_MATCH and variant(m, "size").result is CheckResult.CONFLICT
    m = run("SB-032", candidate("https://www.thomann.co.uk/cort-b5-element-4-string",
                                "Cort B5 Element 4-String Bass, Open Pore Trans Black"))
    assert m.match_status is M.SIMILAR_MATCH and variant(m, "strings").result is CheckResult.CONFLICT
    m = run("SB-022", candidate("https://www.thomann.co.uk/cort-g290-modern-6-string-pale-graphite",
                                "Cort G290 Modern 6-String Pale Graphite"))
    assert m.match_status is M.EXACT_MATCH and variant(m, "strings").result is CheckResult.MATCH


def test_generation_conflict_and_missing_generation(run):
    m = run("SB-003", candidate("https://www.andertons.co.uk/mooer-micro-looper-iii", "Mooer Micro Looper III"))
    assert m.match_status is M.SIMILAR_MATCH and variant(m, "generation").result is CheckResult.CONFLICT
    m = run("SB-003", candidate("https://www.andertons.co.uk/mooer-micro-looper", "Mooer Micro Looper Pedal"))
    assert m.match_status is M.REVIEW_REQUIRED
    m = run("SB-001", candidate("https://www.andertons.co.uk/boss-rc-5-mkii", "Boss RC-5 MkII Loop Station"))
    assert m.match_status is M.SIMILAR_MATCH  # Stompbox RC-5 has no generation marker


def test_configuration_head_versus_stack(run):
    stack = candidate("https://www.andertons.co.uk/blackstar-st-james-50-6l6-head-2x12-cab",
                      "Blackstar St. James 50 6L6 Head & 2x12 Cabinet, Black")  # fmt: skip
    assert run("SB-047", stack).match_status is M.SIMILAR_MATCH  # Stompbox SB-047 is the head only
    assert run("SB-046", stack).match_status is M.EXACT_MATCH
    head = candidate("https://www.andertons.co.uk/blackstar-st-james-50-6l6-head", "Blackstar St. James 50 6L6 Head")
    m = run("SB-046", head)
    assert m.match_status is M.REVIEW_REQUIRED and variant(m, "configuration").result is CheckResult.UNVERIFIED


def test_retailer_product_codes_are_not_cabinets(run):
    m = run("SB-060", candidate("https://www.gear4music.com/Guitar-and-Bass/Boss-FZ-5-Fuzz-Effects-Pedal/7X8",
                                "Boss FZ-5 Fuzz Effects Pedal at Gear4music"))
    assert m.match_status is M.EXACT_MATCH and variant(m, "configuration").result is CheckResult.NOT_APPLICABLE


def test_bundle_and_edition_are_not_exact(run):
    m = run("SB-001", candidate("https://www.andertons.co.uk/boss-rc-5-bundle", "Boss RC-5 Loop Station Bundle"))
    assert m.match_status is M.SIMILAR_MATCH and variant(m, "bundle").result is CheckResult.CONFLICT
    m = run("SB-001", candidate("https://www.andertons.co.uk/boss-rc-5-limited-edition", "Boss RC-5 Limited Edition"))
    assert m.match_status is M.SIMILAR_MATCH and variant(m, "edition").result is CheckResult.CONFLICT


def test_black_friday_is_not_a_finish(run):
    m = run("SB-021", candidate("https://www.thomann.co.uk/cort-g250-se-vivid-burgundy",
                                "Cort G250 SE Vivid Burgundy | Black Friday Deals"))
    assert variant(m, "finish").result is CheckResult.MATCH and m.match_status is M.EXACT_MATCH


# --------------------------------------------------------------------------- condition


@pytest.mark.parametrize("url,title,cond", [
    ("https://www.gear4music.com/Guitar-and-Bass/Boss-RC-5-Loop-Station-Secondhand/5L7W", "Boss RC-5 Secondhand", Condition.USED),
    ("https://www.andertons.co.uk/boss-rc-5-b-stock", "Boss RC-5 Loop Station B-Stock", Condition.REFURBISHED),
    ("https://www.andertons.co.uk/boss-rc-5-ex-demo", "Boss RC-5 (Ex-Demo)", Condition.REFURBISHED),
    ("https://www.andertons.co.uk/boss-rc-5-pre-owned", "Pre-Owned Boss RC-5", Condition.USED),
])  # fmt: skip
def test_used_and_refurbished_are_never_equivalent_to_new(run, url, title, cond):
    m = run("SB-001", candidate(url, title, llm=True, model="RC-5", brand="Boss", price="120.00",
                                quote="£120.00"))  # fmt: skip
    assert m.condition.condition is cond and not m.condition.comparable_to_new
    assert m.match_status is M.SIMILAR_MATCH and not m.comparison.eligible
    assert m.price.status is PriceStatus.VALID  # the price itself can still be valid


def test_explicit_new_and_unknown_condition(run):
    m = run("SB-001", candidate(AND_RC5, "Boss RC-5", llm=True, model="RC-5", availability="Brand New - In stock"))
    assert m.condition.condition is Condition.NEW and m.condition.comparable_to_new
    m = run("SB-001", candidate(AND_RC5, "Boss RC-5 Loop Station"))
    assert m.condition.condition is Condition.UNKNOWN and m.condition.comparable_to_new


# --------------------------------------------------------------------------- non-product pages


@pytest.mark.parametrize("url,title", [
    ("https://forum.gibson.com/topic/61721-the-boss-fz5-fuzz", "The Boss FZ-5 fuzz"),
    ("https://blog.andertons.co.uk/learn/distortion-pedal-beginners-guide", "Boss FZ-5 and more"),
    ("https://www.andertons.co.uk/guitar-looper-pedal-guide", "Boss RC-5 looper guide"),
    ("https://www.bopdj.com/sale?p=3", "Boss FZ-5 sale"),
    ("https://www.andertons.co.uk/boss-rc-5-vs-rc-500", "Boss RC-5 vs RC-500"),
])  # fmt: skip
def test_non_product_urls_are_no_match(run, url, title):
    m = run("SB-001" if "rc" in url else "SB-060", candidate(url, title))
    assert m.match_status is M.NO_MATCH and m.page.kind is PageKind.NON_PRODUCT_PAGE


def test_llm_listing_page_is_no_match(run):
    m = run("SB-001", candidate(AND_RC5, "Boss RC-5", llm=True, page_type=PageType.LISTING_PAGE, model="RC-5"))
    assert m.match_status is M.NO_MATCH and "listing_page" in m.page.basis


# --------------------------------------------------------------------------- missing / unsupported evidence


def test_page_not_retrieved_needs_review(run):
    m = run("SB-001", candidate(AND_RC5, "Boss RC-5 Loop Station", page_status=RetrievalStatus.BLOCKED))
    assert m.match_status is M.REVIEW_REQUIRED and any("not retrieved" in r for r in m.review_reasons)
    assert m.price.status is PriceStatus.NOT_EXTRACTED and "not retrieved" in m.price.reasons[0]


def test_no_evidence_at_all_is_no_match(run):
    m = run("SB-001", candidate("https://www.andertons.co.uk/p/1", None, page_title=None))
    assert m.match_status is M.NO_MATCH and m.brand.result is CheckResult.UNVERIFIED


def test_unverified_extracted_values_are_not_used(run):
    # Phase 5 only populates fields whose quotes were verified; an unsupported model is null.
    c = candidate("https://www.andertons.co.uk/p/1", "Loop pedal", llm=True, model=None)
    m = run("SB-001", c)
    assert m.model.result is CheckResult.UNVERIFIED and m.match_status is M.NO_MATCH


def test_marketplace_exact_match_is_never_a_benchmark(run):
    ebay = target("ebay.co.uk", T.MARKETPLACE, benchmark=False)
    m = run("SB-001", candidate("https://www.ebay.co.uk/itm/boss-rc-5-loop-station", "Boss RC-5 Loop Station",
                                llm=True, model="RC-5", brand="Boss", price="150.00", competitor=ebay))
    assert m.match_status is M.EXACT_MATCH and m.price.status is PriceStatus.VALID
    assert not m.comparison.eligible and any("not marked as a price benchmark" in r for r in m.comparison.reasons)


def test_stale_price_is_not_compared(run):
    old = NOW - timedelta(days=3)
    m = run("SB-001", candidate(AND_RC5, "Boss RC-5", llm=True, model="RC-5", brand="Boss", price="189.00",
                                retrieved_at=old))
    assert m.price.status is PriceStatus.VALID and m.price.evidence_status is EvidenceStatus.STALE
    assert not m.comparison.eligible and any("STALE" in r for r in m.comparison.reasons)


def test_llm_failure_has_no_price(run):
    m = run("SB-001", candidate(AND_RC5, "Boss RC-5 Loop Station", extraction=ExtractionStatus.FAILED))
    assert m.price.status is PriceStatus.NOT_EXTRACTED and "failed" in m.price.reasons[0]
    assert m.match_status is M.EXACT_MATCH  # identity still from title + URL


def test_phase5_field_statuses_map_to_price_statuses(run):
    cases = {
        FieldStatus.NOT_FOUND: (PriceStatus.MISSING, EvidenceStatus.NOT_FOUND),
        FieldStatus.UNSUPPORTED: (PriceStatus.UNSUPPORTED, EvidenceStatus.UNVERIFIED),
        FieldStatus.EXCLUDED_SALE_PRICE: (PriceStatus.EXCLUDED_SALE_PRICE, EvidenceStatus.UNVERIFIED),
        FieldStatus.AMBIGUOUS: (PriceStatus.AMBIGUOUS, EvidenceStatus.UNVERIFIED),
        FieldStatus.CONFLICT: (PriceStatus.CONFLICT, EvidenceStatus.CONFLICT),
        FieldStatus.INVALID: (PriceStatus.INVALID, EvidenceStatus.UNVERIFIED),
    }
    for fs, (ps, es) in cases.items():
        price = None if fs is FieldStatus.NOT_FOUND else "219.00"
        m = run("SB-001", candidate(AND_RC5, "Boss RC-5", llm=True, model="RC-5", price=price, price_status=fs))
        assert (m.price.status, m.price.evidence_status) == (ps, es), fs
        assert m.price.price is None and not m.comparison.eligible
        assert m.match_status is M.EXACT_MATCH  # price validity never changes the match
