"""Exa adapter. Docs: https://docs.exa.ai/reference/search, /reference/get-contents."""

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

BASE_URL = "https://api.exa.ai"


def _cost(data: dict[str, Any]) -> CostInfo:
    total = (data.get("costDollars") or {}).get("total")
    if total is None:
        return CostInfo()
    return CostInfo(usd=Decimal(str(total)), basis=CostBasis.PROVIDER_REPORTED)


class ExaProvider(SearchProvider):
    name = "exa"
    supports_retrieval = True

    def __init__(
        self,
        http: ProviderHttpClient,
        api_key: str,
        *,
        search_type: str = "auto",
        content_max_age_hours: int | None = 24,
        livecrawl_timeout_ms: int = 15_000,
        search_timeout_s: float = 20.0,
        content_timeout_s: float = 45.0,
        max_content_chars: int = 30_000,
    ):
        super().__init__(http)
        self._headers = {"x-api-key": api_key, "content-type": "application/json"}
        self.search_type = search_type
        self.content_max_age_hours = content_max_age_hours
        # How long Exa waits for a live crawl before falling back/failing.
        self.livecrawl_timeout_ms = livecrawl_timeout_ms
        self.search_timeout_s = search_timeout_s
        self.content_timeout_s = content_timeout_s
        self.max_content_chars = max_content_chars

    async def search(self, query: SearchQuery) -> SearchResponse:
        body: dict[str, Any] = {
            "query": query.text,
            "numResults": query.max_results,
            "type": self.search_type,
            # Short highlights give a snippet comparable to the other providers.
            "contents": {"highlights": {"maxCharacters": 500}},
        }
        if query.domains:
            body["includeDomains"] = list(query.domains)
        if query.country:
            body["userLocation"] = query.country
        data, latency_ms = await self.http.request_json(
            "POST", f"{BASE_URL}/search", headers=self._headers, json=body,
            timeout_s=self.search_timeout_s,
        )  # fmt: skip
        hits = [
            RawHit(
                url=r.get("url", ""),
                title=r.get("title"),
                snippet=" … ".join(r.get("highlights") or []) or None,
                published_raw=r.get("publishedDate"),
                score=r.get("score"),
            )
            for r in data.get("results") or []
        ]
        results, dropped = finalize_results(self.name, query, hits)
        return SearchResponse(
            provider=self.name, query=query, results=results, out_of_scope_dropped=dropped,
            retrieved_at=now_utc(), latency_ms=latency_ms,
            request_id=data.get("requestId"), cost=_cost(data),
        )  # fmt: skip

    async def retrieve(self, urls: list[str]) -> RetrievalResponse:
        body: dict[str, Any] = {
            "urls": urls,
            "text": {"maxCharacters": self.max_content_chars},
            "livecrawlTimeout": self.livecrawl_timeout_ms,
        }
        if self.content_max_age_hours is not None:
            body["maxAgeHours"] = self.content_max_age_hours
        data, latency_ms = await self.http.request_json(
            "POST", f"{BASE_URL}/contents", headers=self._headers, json=body,
            timeout_s=self.content_timeout_s,
        )  # fmt: skip
        retrieved_at = now_utc()
        by_url = {r.get("url"): r for r in data.get("results") or []}
        by_id = {r.get("id"): r for r in data.get("results") or []}
        statuses = {s.get("id"): s for s in data.get("statuses") or []}
        pages = []
        for url in urls:
            status = statuses.get(url, {})
            result = by_url.get(url) or by_id.get(url)
            if status.get("status") == "error" or result is None:
                err = status.get("error") or {}
                pages.append(RetrievedPage(
                    provider=self.name, requested_url=url, status=RetrievalStatus.FAILED,
                    retrieved_at=retrieved_at,
                    error=f"{err.get('tag', 'not returned')} (HTTP {err.get('httpStatusCode')})",
                ))  # fmt: skip
                continue
            text = (result.get("text") or "").strip()
            final_url = result.get("url") or url
            pages.append(RetrievedPage(
                provider=self.name, requested_url=url, url=final_url,
                hostname=normalize_hostname(final_url),
                status=content_status(text),
                title=result.get("title"), content=text or None, content_format="text",
                retrieved_at=retrieved_at, cache_source=status.get("source"),
            ))  # fmt: skip
        return RetrievalResponse(
            provider=self.name, pages=pages, retrieved_at=retrieved_at, latency_ms=latency_ms,
            request_id=data.get("requestId"), cost=_cost(data),
        )  # fmt: skip
