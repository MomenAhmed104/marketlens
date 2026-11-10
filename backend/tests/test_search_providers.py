import asyncio
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import ValidationError

from app.schemas.search import CostBasis, RetrievalStatus, SearchQuery, SearchScope
from app.services.search import build_provider, configured_providers, get_configured_provider
from app.services.search.base import RawHit, finalize_results
from app.services.search.brave import BraveProvider, build_query_text
from app.services.search.exa import ExaProvider
from app.services.search.http import (
    ProviderAuthError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderUnavailableError,
    RetrievalNotSupportedError,
)
from app.services.search.tavily import TavilyProvider
from tests.search_fakes import FAKE_KEY, FakeSleep, Recorder, http_client, ok, make_settings

GLOBAL = SearchQuery(text="Boss RC-5", product_id="SB-001")
TARGETED = SearchQuery(text="Boss RC-5", scope=SearchScope.DOMAIN, domains=("https://www.Andertons.co.uk/",))


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- query + shared


def test_query_scope_rules():
    assert TARGETED.domains == ("andertons.co.uk",)
    with pytest.raises(ValidationError):
        SearchQuery(text="x", scope=SearchScope.DOMAIN)
    with pytest.raises(ValidationError):
        SearchQuery(text="x", domains=("andertons.co.uk",))
    with pytest.raises(ValidationError):
        SearchQuery(text="x", max_results=50)


def test_finalize_enforces_scope_dedupes_and_ranks():
    hits = [
        RawHit(url="https://www.andertons.co.uk/boss-rc-5", title=" Boss RC-5 "),
        RawHit(url="https://www.andertons.co.uk/boss-rc-5/#reviews"),  # duplicate
        RawHit(url="https://shop.andertons.co.uk/rc5"),  # subdomain: in scope
        RawHit(url="https://www.gear4music.com/rc5"),  # out of scope
        RawHit(url="not-a-url"),
    ]
    results, dropped = finalize_results("x", TARGETED, hits)
    assert [r.url.host for r in results] == ["www.andertons.co.uk", "shop.andertons.co.uk"]
    assert [r.rank for r in results] == [1, 2]
    assert results[0].title == "Boss RC-5"
    assert dropped == 1


# --------------------------------------------------------------------------- HTTP behaviour


def test_retries_429_honouring_retry_after_then_succeeds():
    rec = Recorder(ok({}, 429, {"retry-after": "3"}), ok({"results": []}))
    sleep = FakeSleep()
    exa = ExaProvider(http_client("exa", rec, sleep), FAKE_KEY)
    resp = run(exa.search(GLOBAL))
    assert resp.results == [] and len(rec.requests) == 2 and sleep.calls == [3.0]


def test_rate_limit_exhausted():
    rec = Recorder(*[ok({}, 429)] * 3)
    with pytest.raises(ProviderRateLimitError):
        run(ExaProvider(http_client("exa", rec), FAKE_KEY).search(GLOBAL))
    assert len(rec.requests) == 3


def test_auth_error_not_retried_and_key_not_leaked():
    rec = Recorder(ok({"error": "bad key"}, 401))
    with pytest.raises(ProviderAuthError) as exc:
        run(TavilyProvider(http_client("tavily", rec), FAKE_KEY).search(GLOBAL))
    assert len(rec.requests) == 1
    assert FAKE_KEY not in str(exc.value)


def test_server_errors_and_timeouts_become_unavailable():
    rec = Recorder(ok({}, 503), ok({}, 502), ok({}, 500))
    with pytest.raises(ProviderUnavailableError):
        run(ExaProvider(http_client("exa", rec), FAKE_KEY).search(GLOBAL))
    rec = Recorder(*[httpx.ReadTimeout("slow")] * 3)
    with pytest.raises(ProviderUnavailableError, match="ReadTimeout after 3"):
        run(ExaProvider(http_client("exa", rec), FAKE_KEY).search(GLOBAL))


def test_client_error_not_retried():
    rec = Recorder(ok({"error": "bad"}, 400))
    with pytest.raises(ProviderRequestError):
        run(ExaProvider(http_client("exa", rec), FAKE_KEY).search(GLOBAL))
    assert len(rec.requests) == 1


# --------------------------------------------------------------------------- Exa


def test_exa_search_request_and_normalisation():
    rec = Recorder(ok({
        "requestId": "req-1",
        "costDollars": {"total": 0.012},
        "results": [
            {"url": "https://www.andertons.co.uk/boss-rc-5", "title": "Boss RC-5 Loop Station",
             "publishedDate": "2025-01-02T00:00:00.000Z", "highlights": ["£189", "In stock"]},
            {"url": "https://www.gear4music.com/x", "title": "Leak"},
        ],
    }))  # fmt: skip
    resp = run(ExaProvider(http_client("exa", rec), FAKE_KEY, search_type="fast").search(TARGETED))
    req = rec.requests[0]
    assert str(req.url) == "https://api.exa.ai/search"
    assert req.headers["x-api-key"] == FAKE_KEY
    assert rec.body() == {
        "query": "Boss RC-5", "numResults": 10, "type": "fast",
        "contents": {"highlights": {"maxCharacters": 500}},
        "includeDomains": ["andertons.co.uk"], "userLocation": "GB",
    }  # fmt: skip
    assert len(resp.results) == 1 and resp.out_of_scope_dropped == 1
    r = resp.results[0]
    assert (r.hostname, r.snippet, r.published_raw) == ("andertons.co.uk", "£189 … In stock",
                                                        "2025-01-02T00:00:00.000Z")  # fmt: skip
    assert resp.cost.usd == Decimal("0.012") and resp.cost.basis is CostBasis.PROVIDER_REPORTED
    assert resp.request_id == "req-1"


def test_exa_retrieve_maps_statuses():
    urls = ["https://a.co.uk/1", "https://b.co.uk/2", "https://c.co.uk/3"]
    rec = Recorder(ok({
        "results": [
            {"id": urls[0], "url": urls[0], "title": "A", "text": "Boss RC-5 £189.00"},
            {"id": urls[2], "url": urls[2], "text": "   "},
        ],
        "statuses": [
            {"id": urls[0], "status": "success", "source": "crawled"},
            {"id": urls[1], "status": "error", "error": {"tag": "CRAWL_NOT_FOUND", "httpStatusCode": 404}},
            {"id": urls[2], "status": "success", "source": "cached"},
        ],
        "costDollars": {"total": 0.003},
    }))  # fmt: skip
    resp = run(ExaProvider(http_client("exa", rec), FAKE_KEY, content_max_age_hours=0).retrieve(urls))
    assert rec.body()["maxAgeHours"] == 0 and rec.body()["urls"] == urls
    a, b, c = resp.pages
    assert (a.status, a.cache_source, a.content, a.hostname) == (
        RetrievalStatus.OK, "crawled", "Boss RC-5 £189.00", "a.co.uk")  # fmt: skip
    assert b.status is RetrievalStatus.FAILED and "CRAWL_NOT_FOUND" in b.error and "404" in b.error
    assert c.status is RetrievalStatus.EMPTY and c.content is None


# --------------------------------------------------------------------------- Tavily


def test_tavily_search_request_and_reported_credits():
    rec = Recorder(ok({
        "request_id": "t-1", "usage": {"credits": 2},
        "results": [{"url": "https://www.andertons.co.uk/p", "title": "T", "content": "snip", "score": 0.9}],
    }))  # fmt: skip
    tav = TavilyProvider(http_client("tavily", rec), FAKE_KEY, search_depth="advanced")
    resp = run(tav.search(TARGETED))
    assert rec.requests[0].headers["authorization"] == f"Bearer {FAKE_KEY}"
    body = rec.body()
    assert body["include_domains"] == ["andertons.co.uk"]
    assert body["country"] == "united kingdom" and body["search_depth"] == "advanced"
    assert resp.results[0].score == 0.9 and resp.results[0].snippet == "snip"
    assert resp.cost.usd == Decimal("0.016") and resp.cost.basis is CostBasis.PROVIDER_REPORTED


def test_tavily_cost_estimated_without_usage():
    rec = Recorder(ok({"results": []}))
    resp = run(TavilyProvider(http_client("tavily", rec), FAKE_KEY).search(GLOBAL))
    assert resp.cost.usd == Decimal("0.008") and resp.cost.basis is CostBasis.ESTIMATED


def test_tavily_extract():
    urls = ["https://a.co.uk/1", "https://b.co.uk/2"]
    rec = Recorder(ok({
        "results": [{"url": urls[0], "raw_content": "# RC-5\n£189"}],
        "failed_results": [{"url": urls[1], "error": "blocked"}],
    }))  # fmt: skip
    resp = run(TavilyProvider(http_client("tavily", rec), FAKE_KEY).retrieve(urls))
    assert str(rec.requests[0].url) == "https://api.tavily.com/extract"
    a, b = resp.pages
    assert a.status is RetrievalStatus.OK and a.content_format == "markdown"
    assert b.status is RetrievalStatus.FAILED and b.error == "blocked"
    assert resp.cost.usd == Decimal("0.0016")  # 1 success = 1/5 credit


def test_tavily_extract_url_limit():
    with pytest.raises(ValueError):
        run(TavilyProvider(http_client("tavily", Recorder()), FAKE_KEY).retrieve(["https://a.co.uk"] * 21))


# --------------------------------------------------------------------------- Brave


def test_brave_search_request_and_normalisation():
    rec = Recorder(ok({"web": {"results": [
        {"url": "https://www.andertons.co.uk/p", "title": "<strong>Boss</strong> RC-5",
         "description": "Loop &amp; <strong>£189</strong>", "extra_snippets": ["In stock"],
         "page_age": "2025-03-01T00:00:00"},
        {"url": "https://www.pmtonline.co.uk/p", "title": "leak"},
    ]}}))  # fmt: skip
    resp = run(BraveProvider(http_client("brave", rec), FAKE_KEY).search(TARGETED))
    req = rec.requests[0]
    assert req.method == "GET" and req.headers["x-subscription-token"] == FAKE_KEY
    params = parse_qs(urlsplit(str(req.url)).query)
    assert params["q"] == ["Boss RC-5 site:andertons.co.uk"] and params["country"] == ["GB"]
    r = resp.results[0]
    assert (r.title, r.snippet, r.published_raw) == ("Boss RC-5", "Loop & £189 … In stock", "2025-03-01T00:00:00")
    assert resp.out_of_scope_dropped == 1
    assert resp.cost.usd == Decimal("0.005") and resp.cost.basis is CostBasis.ESTIMATED


def test_brave_multi_domain_query_and_no_retrieval():
    q = SearchQuery(text="x", scope=SearchScope.DOMAIN, domains=("a.co.uk", "b.com"))
    assert build_query_text(q) == "x (site:a.co.uk OR site:b.com)"
    with pytest.raises(RetrievalNotSupportedError):
        run(BraveProvider(http_client("brave", Recorder()), FAKE_KEY).retrieve(["https://a.co.uk"]))


# --------------------------------------------------------------------------- factory / settings


def test_factory_requires_keys_and_hides_them():
    s = make_settings()
    assert configured_providers(s) == []
    with pytest.raises(ProviderNotConfiguredError, match="EXA_API_KEY"):
        build_provider("exa", s)
    with pytest.raises(ProviderNotConfiguredError, match="SEARCH_PROVIDER"):
        get_configured_provider(s)
    with pytest.raises(ValueError):
        build_provider("google", s)

    s = make_settings(exa_api_key=FAKE_KEY, tavily_api_key=FAKE_KEY, brave_search_api_key=FAKE_KEY,
                      search_provider="tavily")  # fmt: skip
    assert FAKE_KEY not in repr(s) and FAKE_KEY not in str(s.model_dump())
    assert configured_providers(s) == ["exa", "tavily", "brave"]
    for name, cls in [("exa", ExaProvider), ("tavily", TavilyProvider), ("brave", BraveProvider)]:
        prov = build_provider(name, s)
        assert isinstance(prov, cls)
        run(prov.aclose())
    prov = get_configured_provider(s)
    assert isinstance(prov, TavilyProvider)
    run(prov.aclose())


# --------------------------------------------------------------------------- Phase 2 fixes


def test_exa_livecrawl_timeout_defaults_to_15s_and_is_configurable():
    rec = Recorder(ok({"results": [], "statuses": []}), ok({"results": [], "statuses": []}))
    run(ExaProvider(http_client("exa", rec), FAKE_KEY).retrieve(["https://a.co.uk/1"]))
    assert rec.body(0)["livecrawlTimeout"] == 15_000
    s = make_settings(exa_api_key=FAKE_KEY, exa_livecrawl_timeout_ms=9_000)
    prov = build_provider("exa", s, httpx.AsyncClient(transport=httpx.MockTransport(rec)))
    run(prov.retrieve(["https://a.co.uk/1"]))
    assert rec.body(1)["livecrawlTimeout"] == 9_000
    assert make_settings().exa_livecrawl_timeout_ms == 15_000
    with pytest.raises(ValidationError):
        make_settings(exa_livecrawl_timeout_ms=0)


def test_tavily_extract_uses_reported_credits_when_positive():
    urls = [f"https://a.co.uk/{i}" for i in range(6)]
    rec = Recorder(ok({"results": [{"url": u, "raw_content": "x"} for u in urls], "usage": {"credits": 2}}))
    resp = run(TavilyProvider(http_client("tavily", rec), FAKE_KEY).retrieve(urls))
    assert resp.cost.basis is CostBasis.PROVIDER_REPORTED and resp.cost.usd == Decimal("0.016")
    assert rec.body()["include_usage"] is True


def test_bot_challenge_pages_are_blocked_not_ok():
    from app.services.search.base import content_status

    challenge = ("Client Challenge\n\nA required part of this site couldn't load. This may be due to a browser "
                 "extension, network issues, or browser settings.")  # fmt: skip
    assert content_status(challenge) is RetrievalStatus.BLOCKED
    assert content_status("Just a moment...") is RetrievalStatus.BLOCKED
    assert content_status("   ") is RetrievalStatus.EMPTY
    long_page = "Boss RC-5 £219.00 In stock. " * 100 + " protected by captcha"
    assert content_status(long_page) is RetrievalStatus.OK

    url = "https://www.bax-shop.co.uk/looper/boss-rc-5"
    rec = Recorder(ok({"results": [{"id": url, "url": url, "text": challenge}],
                       "statuses": [{"id": url, "status": "success", "source": "cached"}]}))  # fmt: skip
    page = run(ExaProvider(http_client("exa", rec), FAKE_KEY).retrieve([url])).pages[0]
    assert page.status is RetrievalStatus.BLOCKED
    rec = Recorder(ok({"results": [{"url": url, "raw_content": challenge}]}))
    page = run(TavilyProvider(http_client("tavily", rec), FAKE_KEY).retrieve([url])).pages[0]
    assert page.status is RetrievalStatus.BLOCKED
