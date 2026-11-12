"""Product intelligence agent end to end with fake search/retrieval and fake LLM 2 (no network)."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.agents.intelligence import ProductIntelligenceAgent, eligible_search_targets
from app.agents.intelligence.schemas import ExtractedField as F
from app.agents.intelligence.schemas import ExtractionStatus as X
from app.agents.intelligence.schemas import FieldStatus, IntelligenceMode
from app.schemas.common import DomainApprovalStatus as S
from app.schemas.competitor import CompanyType as T
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.schemas.search import RetrievalStatus
from app.services.llm import OpenRouterClient
from app.services.search.base import RawHit
from app.services.search.http import ProviderUnavailableError
from tests.intelligence_fakes import FakeExtractLLM, FakeIntelSearch, FakePage, extraction
from tests.discovery_fakes import llm_failure
from tests.search_fakes import FAKE_KEY, Recorder, http_client, make_settings, ok

NOW = datetime(2026, 11, 12, tzinfo=UTC)
AND = "https://www.andertons.co.uk/boss-rc-5-loop-station"
AND2 = "https://www.andertons.co.uk/boss-rc-5-loop-station-bundle"
AND3 = "https://www.andertons.co.uk/boss-rc-1"
AND_BLOG = "https://www.andertons.co.uk/blog/best-loopers"
G4M = "https://www.gear4music.com/Guitar-and-Bass/BOSS-RC-5-Loop-Station/3K0X"
GIB = "https://www.gibson.com/en-gb/boss-rc-5"
BECK = "https://beckettsmusic.co.uk/products/boss-rc-5"
EBAY = "https://www.ebay.co.uk/itm/boss-rc-5"
IDEALO = "https://www.idealo.co.uk/compare/boss-rc-5"

HITS = {
    "andertons.co.uk": [RawHit(AND, "Boss RC-5 Loop Station | Andertons"), RawHit(AND2, "Boss RC-5 bundle"),
                        RawHit(AND3, "Boss RC-1 Loop Station"), RawHit(AND_BLOG, "Best loopers 2026"),
                        RawHit("https://www.thomann.co.uk/boss_rc5.htm", "Boss RC-5 (wrong domain)")],
    "gear4music.com": [RawHit(G4M, "BOSS RC-5 Loop Station at Gear4music")],
    "gibson.com": [RawHit(GIB, "Boss RC-5")],
    "beckettsmusic.co.uk": [RawHit(BECK, "Boss RC-5 - Beckett's Music")],
    "ebay.co.uk": [RawHit(EBAY, "Boss RC-5 Loop Station | eBay")],
    "idealo.co.uk": [RawHit(IDEALO, "Boss RC-5 price comparison")],
}  # fmt: skip

AND_TEXT = "Boss RC-5 Loop Station\nWas £219.00 Now £189.00\nIn stock\nModel: RC-5"
PAGES = {
    AND: FakePage(AND_TEXT, "Boss RC-5 Loop Station | Andertons"),
    AND2: FakePage("Just a moment...", status=RetrievalStatus.BLOCKED),
    G4M: FakePage("BOSS RC-5 Loop Station £189\nOut of stock", "BOSS RC-5"),
    BECK: FakePage("Boss RC-5 £185.00 In stock", "Boss RC-5"),
    EBAY: FakePage("Boss RC-5 Loop Station £150.00 Used", "eBay"),
    # GIB missing -> FAILED retrieval
}
EXTRACTIONS = {
    AND: extraction(product_title="Boss RC-5 Loop Station", title_quote="Boss RC-5 Loop Station", brand="Boss",
                    brand_quote="Boss RC-5", model="RC-5", model_quote="Model: RC-5", displayed_price="£189.00",
                    currency="GBP", price_quote="Was £219.00 Now £189.00", ignored_prices=["Was £219.00"],
                    availability="In stock", availability_quote="In stock"),
    # The model returns the crossed-out price: must be excluded by code.
    G4M: extraction(product_title="BOSS RC-5 Loop Station", title_quote="BOSS RC-5 Loop Station",
                    displayed_price="£199.00", price_quote="BOSS RC-5 Loop Station £189",
                    availability="Out of stock", availability_quote="Out of stock"),
    BECK: llm_failure(),
    EBAY: extraction(product_title="Boss RC-5 Loop Station", title_quote="Boss RC-5 Loop Station",
                     displayed_price="£150.00", price_quote="£150.00 Used"),
}  # fmt: skip


def dec(domain, decision=ReviewDecision.APPROVED, ctype=T.RETAILER, bench=False):
    return DomainDecisionRecord(domain=domain, decision=decision, company_type=ctype, benchmark_eligible=bench,
                                reviewer="Alex", decided_at=NOW)  # fmt: skip


DECISIONS = [
    dec("beckettsmusic.co.uk", ctype=T.SPECIALIST_RETAILER, bench=True),
    dec("ebay.co.uk", ctype=T.MARKETPLACE),
    dec("guitarworld.com", ReviewDecision.REJECTED, T.OTHER),
    dec("idealo.co.uk", ctype=T.DIRECTORY),
    dec("gibson.com", ReviewDecision.REJECTED, T.MANUFACTURER),  # ignored: preconfigured
]


def make(catalogue, llm="fake", decisions=DECISIONS, search=None, **settings):
    s = make_settings(**settings)
    targets, skipped = eligible_search_targets(catalogue.competitors, decisions, s)
    search = search or FakeIntelSearch(HITS, PAGES)
    llm = FakeExtractLLM(EXTRACTIONS) if llm == "fake" else llm
    return ProductIntelligenceAgent(search, llm, s, targets, skipped,
                                    llm_disabled_reason=None if llm else "OPENROUTER_API_KEY and LLM2_MODEL not set")


def run(agent, catalogue, ids=("SB-001",)):
    result = asyncio.run(agent.run([catalogue.products[i] for i in ids]))
    asyncio.run(agent.aclose())
    return result


@pytest.fixture(scope="module")
def outcome(catalogue):
    agent = make(catalogue)
    return run(agent, catalogue), agent


def cands(result):
    return {c.product_url: c for c in result.products[0].candidates}


# --------------------------------------------------------------------------- search targets


def test_only_preconfigured_and_reviewer_approved_domains_are_targets(catalogue):
    targets, skipped = eligible_search_targets(catalogue.competitors, DECISIONS, make_settings())
    by = {t.domain: t for t in targets}
    assert len(by) == 15  # 13 preconfigured + beckett + ebay
    assert by["beckettsmusic.co.uk"].approval_status is S.APPROVED and by["beckettsmusic.co.uk"].price_benchmark
    assert by["ebay.co.uk"].price_benchmark is False and by["ebay.co.uk"].company_type is T.MARKETPLACE
    assert by["gibson.com"].approval_status is S.PRECONFIGURED and by["gibson.com"].price_benchmark is False
    assert "guitarworld.com" not in by and "idealo.co.uk" not in by
    reasons = {s.domain: s.reason for s in skipped}
    assert reasons["guitarworld.com"] == "Rejected in review" and "directory" in reasons["idealo.co.uk"]
    assert "gibson.com" not in reasons


def test_pending_domains_are_never_targets(catalogue):
    targets, _ = eligible_search_targets(catalogue.competitors, [], make_settings())
    assert {t.domain for t in targets} == {c.domain for c in catalogue.competitors}


def test_approved_unknown_type_is_searched_but_never_a_benchmark(catalogue):
    targets, _ = eligible_search_targets(catalogue.competitors, [dec("shop.co.uk", ctype=None)], make_settings())
    t = next(t for t in targets if t.domain == "shop.co.uk")
    assert t.company_type is None and t.price_benchmark is False


def test_searches_are_domain_restricted_to_targets(outcome):
    result, agent = outcome
    targets = {t.domain for t in result.search_targets}
    assert agent.search.queries and all(q.scope.value == "DOMAIN" and len(q.domains) == 1 for q in agent.search.queries)
    assert {q.domains[0] for q in agent.search.queries} == targets
    assert not {"guitarworld.com", "idealo.co.uk"} & {q.domains[0] for q in agent.search.queries}
    assert all(q.text == "Boss RC-5 Loop Station Guitar Effect Pedal" for q in agent.search.queries)
    andertons = next(d for d in result.products[0].domains if d.domain == "andertons.co.uk")
    assert andertons.out_of_scope_dropped == 1  # thomann URL returned for an andertons search


# --------------------------------------------------------------------------- selection and retrieval


def test_page_selection_is_deterministic_and_bounded(outcome):
    result, _ = outcome
    pr = result.products[0]
    selected = {c.product_url for c in pr.candidates}
    assert AND in selected and AND2 in selected and AND3 not in selected
    not_sel = {s.url: s.reason for s in pr.not_selected}
    assert "per-domain page limit" in not_sel[AND3] and "Neither brand nor model" in not_sel[AND_BLOG]
    assert next(d for d in pr.domains if d.domain == "andertons.co.uk").pages_selected == 2


def test_retrieval_statuses(outcome):
    result, agent = outcome
    c = cands(result)
    assert c[AND].page.status is RetrievalStatus.OK and c[AND].page.content_chars == len(AND_TEXT)
    assert c[AND2].page.status is RetrievalStatus.BLOCKED and c[AND2].extraction_status is X.SKIPPED_NOT_RETRIEVED
    assert c[GIB].page.status is RetrievalStatus.FAILED and "404" in c[GIB].extraction_error
    assert len(agent.search.retrieved) == 1  # one batched retrieval call per product
    assert result.usage.pages_requested == 6 and result.usage.pages_ok == 4


# --------------------------------------------------------------------------- extraction


def test_extracted_record_with_field_evidence(outcome):
    result, _ = outcome
    a = cands(result)[AND]
    assert a.extraction_status is X.COMPLETED and a.competitor.domain == "andertons.co.uk"
    assert a.competitor.price_benchmark is True and a.competitor.approval_status is S.PRECONFIGURED
    assert (a.product_title, a.brand, a.model, a.availability) == ("Boss RC-5 Loop Station", "Boss", "RC-5", "In stock")
    assert a.price == Decimal("189.00") and a.currency == "GBP" and a.price_text == "£189.00"
    assert a.sale_indicated and a.ignored_prices == ["Was £219.00"]
    assert a.sku is None and next(e for e in a.field_evidence if e.field is F.SKU).status is FieldStatus.NOT_FOUND
    assert a.search.query_text and a.search.provider_request_id and a.page.retrieved_at and a.llm_request_ids
    assert a.llm_confidence == 0.9
    assert not hasattr(a, "match_status")  # Phase 6


def test_price_not_supported_by_its_quote_is_dropped(outcome):
    g = cands(outcome[0])[G4M]
    assert g.extraction_status is X.COMPLETED and g.price is None
    assert next(e for e in g.field_evidence if e.field is F.PRICE).status is FieldStatus.UNSUPPORTED
    assert g.availability == "Out of stock"


def test_llm_failure_is_isolated_to_that_page(outcome):
    result, _ = outcome
    b = cands(result)[BECK]
    assert b.extraction_status is X.FAILED and "simulated invalid output" in b.extraction_error
    assert b.price is None and b.competitor.price_benchmark is True
    assert result.usage.llm_failures == 1 and any("failed for 1 page" in w for w in result.warnings)


def test_marketplace_page_extracted_but_not_benchmark(outcome):
    e = cands(outcome[0])[EBAY]
    assert e.price == Decimal("150.00") and e.competitor.price_benchmark is False
    assert e.competitor.company_type is T.MARKETPLACE


def test_usage_and_cost(outcome):
    result, _ = outcome
    u = result.usage
    assert u.search_requests == 15 and u.search_cost_usd == Decimal("0.105")
    assert u.llm_calls == 4 and u.llm_prompt_tokens == 6000 and u.llm_cost_usd == Decimal("0.009")
    assert u.llm_model == "fake/llm-2@2026" and u.retrieval_cost_usd == Decimal("0.006")
    assert result.mode is IntelligenceMode.LLM_ENABLED


# --------------------------------------------------------------------------- failure modes


def test_no_llm_mode_searches_and_retrieves_but_extracts_nothing(catalogue):
    result = run(make(catalogue, llm=None), catalogue)
    assert result.mode is IntelligenceMode.LLM_DISABLED and "LLM2_MODEL" in result.llm_disabled_reason
    c = cands(result)
    ok_pages = {AND, G4M, BECK, EBAY}
    assert {u for u, x in c.items() if x.page.status is RetrievalStatus.OK} == ok_pages
    for url, x in c.items():
        # Explicit status: retrieved pages are SKIPPED_NO_LLM, failed/blocked ones SKIPPED_NOT_RETRIEVED.
        assert x.extraction_status is (X.SKIPPED_NO_LLM if url in ok_pages else X.SKIPPED_NOT_RETRIEVED)
        if url in ok_pages:
            assert "LLM 2 not used" in x.extraction_error and "LLM2_MODEL" in x.extraction_error
            assert x.page.content_chars > 0 and x.page.chars_sent_to_llm == 0
        # Never fabricated: every extracted field stays empty.
        assert (x.product_title, x.brand, x.model, x.sku, x.price, x.currency, x.price_text, x.availability) == (None,) * 8
        assert x.page_type is None and x.field_evidence == [] and x.specifications == [] and x.ignored_prices == []
        assert x.llm_confidence is None and x.llm_request_ids == [] and x.sale_indicated is False
        # Search and retrieval evidence is still recorded.
        assert x.search.url and x.search.query_text and x.search.provider_request_id
    assert result.usage.llm_calls == 0 and result.usage.llm_cost_usd is None and result.usage.pages_ok == 4
    assert result.usage.search_requests == 15 and result.usage.retrieval_requests == 1
    assert any("LLM 2 extraction skipped" in w for w in result.warnings)


def test_no_llm_mode_respects_targets(catalogue):
    """Pending, rejected and approved-directory domains are never searched, with or without an LLM."""
    agent = make(catalogue, llm=None)
    result = run(agent, catalogue)
    searched = {q.domains[0] for q in agent.search.queries}
    assert "beckettsmusic.co.uk" in searched and "ebay.co.uk" in searched  # approved
    assert not searched & {"guitarworld.com", "idealo.co.uk", "lookalike-music.co.uk"}  # rejected / directory / pending
    assert {s.domain for s in result.skipped_targets} == {"guitarworld.com", "idealo.co.uk"}
    assert all(r[0] not in (IDEALO,) for r in agent.search.retrieved)


def _openrouter_error(status):
    return ok({"error": {"message": "nope", "code": status}}, status=status)


@pytest.mark.parametrize("status", [401, 402, 403])
def test_account_level_llm_error_stops_further_calls(catalogue, status):
    rec = Recorder(*[_openrouter_error(status)] * 10)
    llm = OpenRouterClient(http_client("openrouter", rec), FAKE_KEY, "test/model")
    result = run(make(catalogue, llm=llm, search=FakeIntelSearch(HITS, PAGES)), catalogue)
    failed = [c for c in result.products[0].candidates if c.extraction_status is X.FAILED]
    assert len(failed) == 4 and len(rec.requests) == 1  # only the first page was sent
    assert sum("Not attempted" in c.extraction_error for c in failed) == 3
    assert all(c.price is None and FAKE_KEY not in (c.extraction_error or "") for c in failed)
    assert any("account-level error" in w for w in result.warnings)


def test_search_failure_on_one_domain_does_not_stop_the_run(catalogue):
    search = FakeIntelSearch(HITS, PAGES, fail_domains={"andertons.co.uk"})
    result = run(make(catalogue, search=search), catalogue)
    assert result.usage.search_errors == 1
    assert next(d for d in result.products[0].domains if d.domain == "andertons.co.uk").errors
    assert G4M in cands(result) and AND not in cands(result)


def test_retrieval_call_failure_marks_pages_not_retrieved(catalogue):
    search = FakeIntelSearch(HITS, PAGES, retrieve_error=ProviderUnavailableError("fake-exa", "down"))
    result = run(make(catalogue, search=search), catalogue)
    assert all(c.extraction_status is X.SKIPPED_NOT_RETRIEVED and "down" in c.extraction_error
               for c in result.products[0].candidates)  # fmt: skip
    assert result.usage.retrieval_errors == 1 and result.usage.llm_calls == 0


def test_no_targets(catalogue):
    s = make_settings()
    agent = ProductIntelligenceAgent(FakeIntelSearch(HITS, PAGES), None, s, [], [])
    result = run(agent, catalogue)
    assert result.products[0].candidates == [] and any("No approved search targets" in w for w in result.warnings)


def test_malformed_llm_output_via_openrouter_client_fails_cleanly(catalogue):
    """Real OpenRouterClient over mocked HTTP: invalid JSON twice -> FAILED, nothing invented."""
    bad = {"id": "gen-x", "model": "m", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0001},
           "choices": [{"finish_reason": "stop", "message": {"content": '{"page_type": "product_page"'}}]}  # fmt: skip
    missing_fields = dict(bad, choices=[{"finish_reason": "stop",
                                         "message": {"content": json.dumps({"page_type": "product_page"})}}])  # fmt: skip
    rec = Recorder(*([ok(bad), ok(missing_fields)] * 10))
    llm = OpenRouterClient(http_client("openrouter", rec), FAKE_KEY, "test/model")
    hits = {"andertons.co.uk": [RawHit(AND, "Boss RC-5 Loop Station")]}
    result = run(make(catalogue, llm=llm, decisions=[], search=FakeIntelSearch(hits, PAGES)), catalogue)
    a = cands(result)[AND]
    assert a.extraction_status is X.FAILED and "schema validation" in a.extraction_error
    assert a.price is None and a.product_title is None
    assert FAKE_KEY not in a.extraction_error


def test_locale_storefront_urls_are_fetched_from_the_uk_store(catalogue):
    us = "https://www.gear4music.com/us/en/Guitar-and-Bass/Boss-RC-5/3LJT"
    uk = "https://www.gear4music.com/Guitar-and-Bass/Boss-RC-5/3LJT"
    search = FakeIntelSearch({"gear4music.com": [RawHit(us, "Boss RC-5 Loop Station")]},
                             {uk: FakePage("Boss RC-5 £189.00", "Boss RC-5")})  # fmt: skip
    result = run(make(catalogue, llm=None, decisions=[], search=search), catalogue)
    (c,) = result.products[0].candidates
    assert search.retrieved == [[uk]] and c.product_url == uk and c.page.requested_url == uk
    assert c.search.url == us  # the provider's original URL is kept as evidence
    assert c.page.status is RetrievalStatus.OK
