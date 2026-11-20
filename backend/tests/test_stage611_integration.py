"""Stage 6.1.1: the seven integration gaps from the Stage 6.1 audit, tested at rule, agent, matching-API
and frontend-type level. No network, no LLM, no real database."""

import asyncio
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents.intelligence.rules import check_extraction
from app.agents.intelligence.schemas import ExtractedField as F
from app.agents.intelligence.schemas import (
    ExtractedSpec,
    ExtractionIssue,
    FieldStatus,
    IgnoredPriceCategory,
    IntelligenceMode,
    IntelligenceRunResult,
    IntelligenceUsage,
    IssueKind,
    NormalizedSpec,
    OptionPrice,
    ProductIntelligence,
    ProductSummary,
    SpecCategory,
)
from app.main import create_app
from app.schemas.common import MatchStatus as M
from app.services.matching import evaluate_run, match_candidate, validate_price
from app.services.matching import attributes as A
from app.services.matching.schemas import CheckResult, PriceStatus
from app.services.prices import find_prices, ignored_price_category, mentions_sale
from app.services.specs import is_meaningful_electronics, spec_category
from tests.intelligence_fakes import FakeExtractLLM, FakeIntelSearch, FakePage, extraction
from tests.matching_fakes import NOW, candidate
from tests.search_fakes import make_settings

AND_RC5 = "https://www.andertons.co.uk/boss-rc-5-loop-station-pedal"
RC5_TITLE = "Boss RC-5 Loop Station Pedal"
REPO = Path(__file__).resolve().parents[2]


def rc5(price="189.00", quote="£189.00", **kw):
    return candidate(AND_RC5, RC5_TITLE, llm=True, product_title=RC5_TITLE, brand="Boss", model="RC-5",
                     price=price, quote=quote, **kw)  # fmt: skip


def run_of(*candidates, product_id="SB-001", catalogue=None) -> IntelligenceRunResult:
    p = catalogue.products[product_id]
    for c in candidates:
        c.stompbox_product_id = product_id
    now = datetime(2026, 11, 13, 12, tzinfo=UTC)
    return IntelligenceRunResult(
        run_id="client-run", mode=IntelligenceMode.LLM_ENABLED, started_at=now, finished_at=now, search_provider="exa",
        search_targets=[c.competitor for c in candidates], skipped_targets=[], usage=IntelligenceUsage(), warnings=[],
        products=[ProductIntelligence(product=ProductSummary(product_id=p.product_id, product_name=p.product_name,
                                                             brand=p.brand, manufacturer_model=p.manufacturer_model),
                                      queries=[], domains=[], candidates=list(candidates), not_selected=[])],
    )  # fmt: skip


# --------------------------------------------------------------------------- 1. validate_price and delivery


def test_validate_price_rejects_a_delivery_charge_independently():
    v = validate_price(rc5(price="4.99", quote="Next day delivery £4.99"), NOW, 24)
    assert v.status is PriceStatus.INVALID and v.price is None and "delivery" in v.reasons[0]
    listed = rc5(price="4.99", quote="£4.99")
    listed.ignored_prices = ["NEXT available Working Day delivery £4.99"]  # older record: no categories stored
    assert validate_price(listed, NOW, 24).status is PriceStatus.AMBIGUOUS
    assert validate_price(rc5(), NOW, 24).status is PriceStatus.VALID  # a normal price is untouched


def test_matching_api_rejects_a_client_submitted_delivery_price(catalogue):
    run = run_of(rc5(price="4.99", quote="Next day delivery £4.99"), catalogue=catalogue)
    with TestClient(create_app(make_settings())) as client:
        r = client.post("/api/matching/evaluate", content=run.model_dump_json(), headers={"content-type": "application/json"})
    assert r.status_code == 200
    (m,) = r.json()["products"][0]["candidates"]
    assert m["price"]["status"] == "INVALID" and m["comparison"]["eligible"] is False
    assert m["price_kind"] == "NONE" and m["intelligence"]["price"]["difference_gbp"] is None


def test_delivery_option_prices_cannot_be_accepted(catalogue):
    black = catalogue.products["SB-021"].model_copy(update={"product_name": "Cort G250 SE Electric Guitar, Black"})
    page = "Cort G250 SE\nBlack £279.00\nBlack next day delivery £4.99\nOlympic White £289.00"
    ext = extraction(option_prices=[OptionPrice(option="Black", displayed_price="£4.99", price_quote="Black next day delivery £4.99"),
                                    OptionPrice(option="Olympic White", displayed_price="£289.00", price_quote="Olympic White £289.00")])  # fmt: skip
    c = check_extraction(ext, page, None, black)
    assert c.option_prices[0].status is FieldStatus.INVALID and c.price is None


# --------------------------------------------------------------------------- 2. issue quotes


PAGE = "Boss RC-5\n£186.00\nNext day delivery £4.99\nFinance £15.75 per month\nVintage edition £219.00"


def issue(*quotes):
    return ExtractionIssue(kind=IssueKind.MULTIPLE_PRICES, fields=["price"], description="several prices", quotes=list(quotes))


def price_of(c):
    return next(e for e in c.evidence if e.field is F.PRICE)


@pytest.mark.parametrize("quotes", [("£186.00", "Next day delivery £4.99"), ("£186.00", "Finance £15.75 per month")])
def test_delivery_or_finance_amounts_in_issue_quotes_do_not_create_ambiguity(catalogue, quotes):
    c = check_extraction(extraction(displayed_price="£186.00", currency="GBP", price_quote="£186.00",
                                    issues=[issue(*quotes)]), PAGE, None, catalogue.products["SB-001"])  # fmt: skip
    assert price_of(c).status is FieldStatus.EXTRACTED and c.price == Decimal("186.00")


def test_amount_listed_as_delivery_by_llm2_is_excluded_from_issue_quotes(catalogue):
    page = "Boss RC-5\n£186.00\n£4.99"
    c = check_extraction(extraction(displayed_price="£186.00", currency="GBP", price_quote="£186.00",
                                    ignored_prices=["Next day delivery £4.99"], issues=[issue("£186.00", "£4.99")]),
                         page, None, catalogue.products["SB-001"])  # fmt: skip
    assert price_of(c).status is FieldStatus.EXTRACTED


def test_two_genuine_product_prices_in_issue_quotes_stay_ambiguous(catalogue):
    c = check_extraction(extraction(displayed_price="£186.00", currency="GBP", price_quote="£186.00",
                                    issues=[issue("£186.00", "Vintage edition £219.00")]), PAGE, None,
                         catalogue.products["SB-001"])  # fmt: skip
    assert price_of(c).status is FieldStatus.AMBIGUOUS and c.price is None


# --------------------------------------------------------------------------- 3. sale wording around delivery


@pytest.mark.parametrize("text", ["£186.00 Save £5 on delivery", "£186.00 £5 off shipping", "£186.00 Delivery: save £5",
                                  "£186.00 50% off delivery", "£186.00 Next day delivery £4.99"])  # fmt: skip
def test_delivery_discounts_never_set_the_sale_flag(text):
    assert mentions_sale(text) is False


@pytest.mark.parametrize("text", ["Was £219.00 Now £186.00 + free delivery", "Free delivery. Was £219 Now £189",
                                  "Save £30 on this pedal - free delivery", "20% off, free UK delivery",
                                  "Sale price £186.00 with next day delivery"])  # fmt: skip
def test_genuine_sales_are_not_suppressed_by_delivery_mentions(text):
    assert mentions_sale(text) is True


@pytest.mark.parametrize("text,category", [
    ("Was £10 and free delivery", "sale"), ("Save £5 on delivery", "delivery"), ("£5 off shipping", "delivery"),
    ("RRP £249 - free delivery", "sale"), ("SATURDAY delivery, 1hr window notification £7.49", "delivery"),
])  # fmt: skip
def test_ignored_price_categories_separate_product_and_delivery_discounts(text, category):
    assert ignored_price_category(text) == category


def test_extraction_sale_flag_with_delivery_discount_in_the_price_quote():
    page = "Boss RC-5\n£186.00 Save £5 on delivery\nWas £219.00 Now £189.00"
    c = check_extraction(extraction(displayed_price="£186.00", currency="GBP", price_quote="£186.00 Save £5 on delivery"),
                         page, None)  # fmt: skip
    assert price_of(c).status is FieldStatus.EXTRACTED and c.sale_indicated is False
    c = check_extraction(extraction(displayed_price="£189.00", currency="GBP", price_quote="Was £219.00 Now £189.00",
                                    ignored_prices=["Save £5 on delivery"]), page, None)  # fmt: skip
    assert c.sale_indicated is True  # the genuine "was" price still counts


# --------------------------------------------------------------------------- 4. specification trust


@pytest.mark.parametrize("name", ["Pickup selector", "Pickup switch", "Pickup toggle", "Pickup blend", "Pickup knob",
                                  "Pickup selector switch"])  # fmt: skip
def test_pickup_controls_are_controls(name):
    assert spec_category(name, "3-way") is SpecCategory.CONTROLS
    assert spec_category(name, "HSS 5-way") is SpecCategory.CONTROLS  # even when it mentions a layout


EXOTIC = "https://www.thomann.co.uk/cort-exotic-ga-walnut-sunburst"
EXOTIC_TITLE = "Cort Exotic-GA Walnut Solid Top Guitar, Sunburst"


def exotic(*specs):
    c = candidate(EXOTIC, EXOTIC_TITLE, llm=True, product_title=EXOTIC_TITLE, brand="Cort", model="Exotic-GA")
    c.specifications = list(specs)
    return c


def electro_result(catalogue, *specs):
    m = match_candidate(catalogue.products["SB-045"], exotic(*specs), NOW, 24)
    return next(v for v in m.variants if v.attribute == "electro_acoustic").result, m


@pytest.mark.parametrize("spec", [
    NormalizedSpec(name="Pickup selector", value="3-way"),
    NormalizedSpec(name="Pickup switch", value="5-way blade"),
    NormalizedSpec(name="Electronics", value="Volume, Tone"),  # generic: not a preamp
])  # fmt: skip
def test_pickup_controls_and_generic_electronics_cannot_confirm_a_preamp(catalogue, spec):
    result, m = electro_result(catalogue, spec)
    assert result is CheckResult.UNVERIFIED and m.match_status is M.REVIEW_REQUIRED


@pytest.mark.parametrize("value", ["Fishman Presys II", "Active 3-band EQ with tuner", "Under-saddle piezo",
                                   "LR Baggs Element"])  # fmt: skip
def test_meaningful_electronics_still_confirms(catalogue, value):
    assert is_meaningful_electronics(value)
    result, m = electro_result(catalogue, NormalizedSpec(name="Electronics", value=value))
    assert result is CheckResult.MATCH and m.match_status is M.EXACT_MATCH


def test_stored_category_conflicts_cannot_influence_matching(catalogue):
    # A forged / inconsistent stored category is ignored; the derived one is used and the mismatch recorded.
    forged = NormalizedSpec(name="Controls", value="Volume, Tone", category=SpecCategory.ELECTRONICS)
    result, m = electro_result(catalogue, forged)
    assert result is CheckResult.UNVERIFIED and m.match_status is M.REVIEW_REQUIRED
    assert m.spec_category_mismatches and "stored category 'electronics' ignored; derived 'controls'" in m.spec_category_mismatches[0]
    assert any(u.startswith("specification:") for u in m.intelligence.uncertainties)
    hidden = NormalizedSpec(name="Electronics", value="Fishman Presys II", category=SpecCategory.CONTROLS)
    result, m = electro_result(catalogue, hidden)
    assert result is CheckResult.MATCH  # a wrong stored category cannot hide real evidence either


def test_records_without_categories_remain_supported(catalogue):
    result, m = electro_result(catalogue, NormalizedSpec(name="Electronics", value="Fishman Presys II"))
    assert result is CheckResult.MATCH and m.spec_category_mismatches == []
    result, _ = electro_result(catalogue, NormalizedSpec(name="Controls", value="Volume, Tone"))
    assert result is CheckResult.UNVERIFIED


def test_matching_api_ignores_a_client_submitted_category(catalogue):
    run = run_of(exotic(NormalizedSpec(name="Controls", value="Volume, Tone", category=SpecCategory.ELECTRONICS)),
                 product_id="SB-045", catalogue=catalogue)  # fmt: skip
    with TestClient(create_app(make_settings())) as client:
        r = client.post("/api/matching/evaluate", content=run.model_dump_json(), headers={"content-type": "application/json"})
    (m,) = r.json()["products"][0]["candidates"]
    assert m["match_status"] == "REVIEW_REQUIRED" and m["spec_category_mismatches"]


# --------------------------------------------------------------------------- 5. mixed finishes


@pytest.mark.parametrize("title", [
    "Epiphone Les Paul Standard '60s Left-Handed, Ice Tea / Ebony",  # alias + another finish
    "Epiphone Les Paul Standard '60s Left-Handed, Iced Tea Burst or Ebony",  # full name + another finish
    "Epiphone Les Paul Standard '60s Left-Handed Ebony (also in Iced Tea Burst)",
])  # fmt: skip
def test_mixed_finish_titles_are_never_a_finish_match(catalogue, title):
    c = candidate("https://www.andertons.co.uk/epiphone-les-paul-standard-60s-left-handed", title)
    m = match_candidate(catalogue.products["SB-028"], c, NOW, 24)
    finish = next(v for v in m.variants if v.attribute == "finish")
    assert finish.result is CheckResult.UNVERIFIED and "Mixed finish evidence" in finish.note
    assert m.match_status not in (M.EXACT_MATCH, M.EQUIVALENT_VARIANT)


@pytest.mark.parametrize("title", [
    "Epiphone Les Paul Standard '60s Left-Handed, Iced Tea Burst",
    "Epiphone Les Paul Standard 60s Left-Handed, Ice Tea",
    "Epiphone Les Paul Standard '60s Left-Handed, Iced Tea Burst with Gold Hardware",  # part colour, not a finish
    "Epiphone Les Paul Standard '60s Left-Handed, Iced Tea Burst Gloss",  # sheen word
])  # fmt: skip
def test_single_finish_titles_still_match(catalogue, title):
    c = candidate("https://www.andertons.co.uk/epiphone-les-paul-standard-60s-left-handed-iced-tea-burst", title)
    m = match_candidate(catalogue.products["SB-028"], c, NOW, 24)
    assert next(v for v in m.variants if v.attribute == "finish").result is CheckResult.MATCH


def test_mixed_finish_option_label_cannot_authorise_a_variant_price(catalogue):
    page = "Ice Tea / Ebony Left-Handed £579.00\nEbony Left-Handed £599.00"
    ext = extraction(option_prices=[
        OptionPrice(option="Ice Tea / Ebony Left-Handed", displayed_price="£579.00", price_quote="Ice Tea / Ebony Left-Handed £579.00"),
        OptionPrice(option="Ebony Left-Handed", displayed_price="£599.00", price_quote="Ebony Left-Handed £599.00")])  # fmt: skip
    c = check_extraction(ext, page, None, catalogue.products["SB-028"])
    assert price_of(c).status is FieldStatus.AMBIGUOUS and c.price is None
    assert c.option_prices[0].variant_checks["finish"] == "UNKNOWN"


def test_other_finish_colours_helper():
    assert A.other_finish_colours("Iced Tea Burst", "Ice Tea / Ebony") == {"ebony"}
    assert A.other_finish_colours("Iced Tea Burst", "Iced Tea Burst, Gold Hardware, Cream Binding") == set()
    assert A.other_finish_colours("Natural", "Natural Satin") == set()
    assert A.other_finish_colours("Iced Tea Burst", "Iced Tea Burst / Bourbon Burst") == {"burst"}


# --------------------------------------------------------------------------- agent level and serialization


def test_agent_extraction_and_serialization_preserve_the_evidence(catalogue):
    from app.agents.intelligence import ProductIntelligenceAgent, eligible_search_targets
    from app.services.search.base import RawHit

    text = ("Boss RC-5 Loop Station Pedal\nWas £219.00 Now £189.00\nIn stock\nModel: RC-5\n"
            "Power: 9V AC Adapter/Battery\nControls: Memory/Loop level, Tempo\nNext day delivery £4.99")  # fmt: skip
    ext = extraction(product_title=RC5_TITLE, title_quote=RC5_TITLE, brand="Boss", brand_quote="Boss RC-5",
                     model="RC-5", model_quote="Model: RC-5", displayed_price="£189.00", currency="GBP",
                     price_quote="Was £219.00 Now £189.00", ignored_prices=["Was £219.00", "Next day delivery £4.99"],
                     specifications=[ExtractedSpec(name="Power", value="9V AC Adapter/Battery", normalized_name="wattage"),
                                     ExtractedSpec(name="Controls", value="Memory/Loop level, Tempo",
                                                   normalized_name="electronics")])  # fmt: skip
    s = make_settings()
    targets, _ = eligible_search_targets(catalogue.competitors, [], s)
    targets = [t for t in targets if t.domain == "andertons.co.uk"]
    search = FakeIntelSearch({"andertons.co.uk": [RawHit(AND_RC5, RC5_TITLE)]}, {AND_RC5: FakePage(text, RC5_TITLE)})
    agent = ProductIntelligenceAgent(search, FakeExtractLLM({AND_RC5: ext}), s, targets)
    result = asyncio.run(agent.run([catalogue.products["SB-001"]]))
    asyncio.run(agent.aclose())
    (c,) = result.products[0].candidates
    assert c.price == Decimal("189.00") and c.sale_indicated is True
    assert [(r.text, r.category) for r in c.ignored_price_details] == [
        ("Was £219.00", IgnoredPriceCategory.SALE), ("Next day delivery £4.99", IgnoredPriceCategory.DELIVERY)]
    assert [(sp.key, sp.category) for sp in c.specifications] == [("power_supply", SpecCategory.POWER_SUPPLY),
                                                                  ("controls", SpecCategory.CONTROLS)]  # fmt: skip
    # The JSON the intelligence API returns (response_model IntelligenceRunResult) carries the same evidence.
    dumped = result.model_dump(mode="json")["products"][0]["candidates"][0]
    assert dumped["ignored_price_details"][1] == {"text": "Next day delivery £4.99", "category": "DELIVERY"}
    assert dumped["specifications"][0]["category"] == "power_supply"
    # Matching the agent's result: direct price, no spec mismatches, nothing trusted blindly.
    m = evaluate_run(result, catalogue.products, 24, now=c.page.retrieved_at).products[0].candidates[0]
    assert m.match_status is M.EXACT_MATCH and m.price.status is PriceStatus.VALID and m.price_kind.value == "DIRECT"
    assert m.spec_category_mismatches == [] and m.price.sale_indicated is True


# --------------------------------------------------------------------------- frontend types


def _ts_union(name: str) -> set[str]:
    src = (REPO / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
    m = re.search(rf"export type {name} =([^\n]+)", src)
    assert m, f"{name} missing from frontend/src/api.ts"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def test_frontend_types_match_the_backend_enums():
    assert _ts_union("IgnoredPriceCategory") == {c.value for c in IgnoredPriceCategory}
    assert _ts_union("SpecCategory") == {c.value for c in SpecCategory}
    src = (REPO / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
    for field in ("ignored_price_details?: IgnoredPriceRecord[]", "specifications?: NormalizedSpec[]",
                  "category: SpecCategory | null", "spec_category_mismatches?: string[]"):  # fmt: skip
        assert field in src, field


def test_find_prices_sanity_for_delivery_texts():
    assert [m.amount for m in find_prices("NEXT available Working Day delivery £4.99")] == [Decimal("4.99")]
