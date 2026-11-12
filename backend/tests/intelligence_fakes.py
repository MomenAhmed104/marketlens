"""In-memory search provider (with retrieval) and LLM 2 for product intelligence tests."""

from __future__ import annotations

from decimal import Decimal

from app.agents.intelligence.schemas import PageExtraction, PageType
from app.schemas.search import (
    CostBasis,
    CostInfo,
    RetrievalResponse,
    RetrievalStatus,
    RetrievedPage,
    SearchQuery,
    SearchResponse,
)
from app.services.domains import normalize_hostname
from app.services.llm.base import LLMClient, LLMResult, LLMUsage
from app.services.search.base import RawHit, SearchProvider, finalize_results
from app.services.search.http import ProviderUnavailableError
from tests.discovery_fakes import NOW


class FakePage:
    def __init__(self, text: str | None, title: str | None = None, status: RetrievalStatus = RetrievalStatus.OK):
        self.text, self.title, self.status = text, title, status


class FakeIntelSearch(SearchProvider):
    name = "fake-exa"
    supports_retrieval = True

    def __init__(self, hits: dict[str, list[RawHit]], pages: dict[str, FakePage],
                 fail_domains: set[str] = frozenset(), retrieve_error: Exception | None = None):  # fmt: skip
        self.hits, self.pages, self.fail_domains, self.retrieve_error = hits, pages, fail_domains, retrieve_error
        self.queries: list[SearchQuery] = []
        self.retrieved: list[list[str]] = []
        self.closed = False

    async def search(self, query: SearchQuery) -> SearchResponse:
        self.queries.append(query)
        domain = query.domains[0]
        if domain in self.fail_domains:
            raise ProviderUnavailableError(self.name, "simulated outage")
        results, dropped = finalize_results(self.name, query, self.hits.get(domain, []))
        return SearchResponse(provider=self.name, query=query, results=results, out_of_scope_dropped=dropped,
                              retrieved_at=NOW, latency_ms=5, request_id=f"s-{len(self.queries)}",
                              cost=CostInfo(usd=Decimal("0.007"), basis=CostBasis.PROVIDER_REPORTED))  # fmt: skip

    async def retrieve(self, urls: list[str]) -> RetrievalResponse:
        self.retrieved.append(list(urls))
        if self.retrieve_error:
            raise self.retrieve_error
        pages = []
        for u in urls:
            p = self.pages.get(u)
            if p is None:
                pages.append(RetrievedPage(provider=self.name, requested_url=u, status=RetrievalStatus.FAILED,
                                           retrieved_at=NOW, error="CRAWL_NOT_FOUND (HTTP 404)"))  # fmt: skip
            else:
                pages.append(RetrievedPage(provider=self.name, requested_url=u, url=u, hostname=normalize_hostname(u),
                                           status=p.status, title=p.title, content=p.text, content_format="text",
                                           retrieved_at=NOW, cache_source="cached"))  # fmt: skip
        return RetrievalResponse(provider=self.name, pages=pages, retrieved_at=NOW, latency_ms=9,
                                 request_id=f"r-{len(self.retrieved)}",
                                 cost=CostInfo(usd=Decimal("0.001") * len(urls), basis=CostBasis.PROVIDER_REPORTED))  # fmt: skip

    async def aclose(self):
        self.closed = True


class FakeExtractLLM(LLMClient):
    """Returns the extraction registered for the page URL found in the prompt."""

    model = "fake/llm-2"

    def __init__(self, by_url: dict[str, PageExtraction | Exception]):
        self.by_url = by_url
        self.calls: list[dict] = []
        self.closed = False

    async def complete_structured(self, *, system, user, output_model, schema_name):
        self.calls.append({"system": system, "user": user, "schema": schema_name})
        assert output_model is PageExtraction
        for url, value in self.by_url.items():
            if f'"url": "{url}"' in user:
                if isinstance(value, Exception):
                    raise value
                return LLMResult(value=value, model="fake/llm-2@2026", request_ids=[f"gen-{len(self.calls)}"],
                                 latency_ms=3, usage=LLMUsage(2000, 300, Decimal("0.003")))  # fmt: skip
        raise AssertionError(f"no fake extraction for prompt: {user[:200]}")

    async def aclose(self):
        self.closed = True


def extraction(**kw) -> PageExtraction:
    base = dict(page_type=PageType.PRODUCT_PAGE, product_title=None, title_quote=None, brand=None, brand_quote=None,
                model=None, model_quote=None, sku=None, sku_quote=None, displayed_price=None, currency=None,
                price_quote=None, price_is_conditional=False, ignored_prices=[], availability=None,
                availability_quote=None, specifications=[], confidence=0.9, notes=None)  # fmt: skip
    base.update(kw)
    return PageExtraction(**base)
