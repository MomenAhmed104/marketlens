"""Tavily adapter. Docs: https://docs.tavily.com/documentation/api-reference/endpoint/search
and /extract."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

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
from app.services.search.base import RawHit, SearchProvider, content_status, finalize_results, now_utc
from app.services.search.http import ProviderHttpClient

BASE_URL = "https://api.tavily.com"
MAX_EXTRACT_URLS = 20
# Tavily's `country` takes lowercase English country names.
COUNTRY_NAMES = {"GB": "united kingdom", "US": "united states", "IE": "ireland"}
SEARCH_CREDITS = {"basic": 1, "fast": 1, "ultra-fast": 1, "advanced": 2}
EXTRACT_CREDITS_PER_5_URLS = {"basic": 1, "advanced": 2}


class TavilyProvider(SearchProvider):
    name = "tavily"
    supports_retrieval = True

    def __init__(
        self,
        http: ProviderHttpClient,
        api_key: str,
        *,
        usd_per_credit: Decimal = Decimal("0.008"),
        search_depth: str = "basic",
        extract_depth: str = "basic",
        search_timeout_s: float = 20.0,
        content_timeout_s: float = 45.0,
    ):
        super().__init__(http)
        self._headers = {"authorization": f"Bearer {api_key}", "content-type": "application/json"}
        self.usd_per_credit = usd_per_credit
        self.search_depth = search_depth
        self.extract_depth = extract_depth
        self.search_timeout_s = search_timeout_s
        self.content_timeout_s = content_timeout_s

    def _credits_cost(self, credits: Decimal, reported: bool) -> CostInfo:
        return CostInfo(
            usd=credits * self.usd_per_credit,
            basis=CostBasis.PROVIDER_REPORTED if reported else CostBasis.ESTIMATED,
            detail=f"{credits.normalize()} credits",
        )

    async def search(self, query: SearchQuery) -> SearchResponse:
        body: dict[str, Any] = {
            "query": query.text,
            "search_depth": self.search_depth,  # explicit: auto_parameters could upgrade it
            "max_results": query.max_results,
            "topic": "general",
            "include_usage": True,
        }
        if query.domains:
            body["include_domains"] = list(query.domains)
        if query.country and query.country in COUNTRY_NAMES:
            body["country"] = COUNTRY_NAMES[query.country]
        data, latency_ms = await self.http.request_json(
            "POST", f"{BASE_URL}/search", headers=self._headers, json=body,
            timeout_s=self.search_timeout_s,
        )  # fmt: skip
        hits = [
            RawHit(
                url=r.get("url", ""),
                title=r.get("title"),
                snippet=r.get("content"),
                published_raw=r.get("published_date"),
                score=r.get("score"),
            )
            for r in data.get("results") or []
        ]
        results, dropped = finalize_results(self.name, query, hits)
        reported = (data.get("usage") or {}).get("credits")
        credits = Decimal(str(reported if reported is not None else SEARCH_CREDITS.get(self.search_depth, 1)))
        return SearchResponse(
            provider=self.name, query=query, results=results, out_of_scope_dropped=dropped,
            retrieved_at=now_utc(), latency_ms=latency_ms, request_id=data.get("request_id"),
            cost=self._credits_cost(credits, reported is not None),
        )  # fmt: skip

    async def retrieve(self, urls: list[str]) -> RetrievalResponse:
        if len(urls) > MAX_EXTRACT_URLS:
            raise ValueError(f"Tavily extract accepts at most {MAX_EXTRACT_URLS} URLs per call")
        body = {
            "urls": urls,
            "extract_depth": self.extract_depth,
            "format": "markdown",
            "include_usage": True,
            "timeout": min(60.0, self.content_timeout_s * 0.8),
        }
        data, latency_ms = await self.http.request_json(
            "POST", f"{BASE_URL}/extract", headers=self._headers, json=body,
            timeout_s=self.content_timeout_s,
        )  # fmt: skip
        retrieved_at = now_utc()
        ok = {r.get("url"): r for r in data.get("results") or []}
        failed = {r.get("url"): r for r in data.get("failed_results") or []}
        pages = []
        for url in urls:
            result = ok.get(url)
            if result is None:
                pages.append(RetrievedPage(
                    provider=self.name, requested_url=url, status=RetrievalStatus.FAILED,
                    retrieved_at=retrieved_at,
                    error=(failed.get(url) or {}).get("error") or "not returned",
                ))  # fmt: skip
                continue
            text = (result.get("raw_content") or "").strip()
            pages.append(RetrievedPage(
                provider=self.name, requested_url=url, url=url, hostname=normalize_hostname(url),
                status=content_status(text),
                content=text or None, content_format="markdown", retrieved_at=retrieved_at,
            ))  # fmt: skip
        # Tavily bills per 5 successful extractions and may report 0 below that.
        # Use the reported figure when positive, otherwise estimate pro rata.
        reported = (data.get("usage") or {}).get("credits")
        if reported:
            cost = self._credits_cost(Decimal(str(reported)), reported=True)
        else:
            successes = sum(p.status is not RetrievalStatus.FAILED for p in pages)
            credits = Decimal(successes) / 5 * EXTRACT_CREDITS_PER_5_URLS.get(self.extract_depth, 1)
            cost = self._credits_cost(credits, reported=False)
        return RetrievalResponse(
            provider=self.name, pages=pages, retrieved_at=retrieved_at, latency_ms=latency_ms,
            request_id=data.get("request_id"), cost=cost,
        )  # fmt: skip
