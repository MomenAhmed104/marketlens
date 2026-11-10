"""Brave Search adapter.

Docs: https://api-dashboard.search.brave.com/app/documentation/web-search/query
Brave's Web Search API returns snippets only; it has no page-content endpoint
equivalent to Exa /contents or Tavily /extract.
"""

from __future__ import annotations

import html
import re
from decimal import Decimal
from typing import Any

from app.schemas.search import (
    CostBasis,
    CostInfo,
    RetrievalResponse,
    SearchQuery,
    SearchResponse,
)
from app.services.search.base import RawHit, SearchProvider, finalize_results, now_utc
from app.services.search.http import ProviderHttpClient, RetrievalNotSupportedError

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_TAG = re.compile(r"<[^>]+>")


def _strip_html(value: str | None) -> str | None:
    return html.unescape(_TAG.sub("", value)) if value else None


def build_query_text(query: SearchQuery) -> str:
    """Brave has no domain-filter parameter; scope is expressed with site:."""
    if not query.domains:
        return query.text
    sites = " OR ".join(f"site:{d}" for d in query.domains)
    return f"{query.text} ({sites})" if len(query.domains) > 1 else f"{query.text} {sites}"


class BraveProvider(SearchProvider):
    name = "brave"
    supports_retrieval = False

    def __init__(
        self,
        http: ProviderHttpClient,
        api_key: str,
        *,
        usd_per_1k_requests: Decimal = Decimal("5"),
        search_timeout_s: float = 20.0,
    ):
        super().__init__(http)
        self._headers = {"x-subscription-token": api_key, "accept": "application/json"}
        self.usd_per_1k_requests = usd_per_1k_requests
        self.search_timeout_s = search_timeout_s

    async def search(self, query: SearchQuery) -> SearchResponse:
        params: dict[str, Any] = {
            "q": build_query_text(query),
            "count": query.max_results,
            "search_lang": "en",
            "extra_snippets": "true",
        }
        if query.country:
            params["country"] = query.country
        data, latency_ms = await self.http.request_json(
            "GET", SEARCH_URL, headers=self._headers, params=params,
            timeout_s=self.search_timeout_s,
        )  # fmt: skip
        hits = []
        for r in (data.get("web") or {}).get("results") or []:
            parts = [_strip_html(r.get("description"))]
            parts += [_strip_html(s) for s in r.get("extra_snippets") or []]
            hits.append(RawHit(
                url=r.get("url", ""),
                title=_strip_html(r.get("title")),
                snippet=" … ".join(p for p in parts if p) or None,
                published_raw=r.get("page_age") or r.get("age"),
            ))  # fmt: skip
        results, dropped = finalize_results(self.name, query, hits)
        return SearchResponse(
            provider=self.name, query=query, results=results, out_of_scope_dropped=dropped,
            retrieved_at=now_utc(), latency_ms=latency_ms,
            cost=CostInfo(usd=self.usd_per_1k_requests / 1000, basis=CostBasis.ESTIMATED,
                          detail="1 request"),
        )  # fmt: skip

    async def retrieve(self, urls: list[str]) -> RetrievalResponse:
        raise RetrievalNotSupportedError(self.name, "Brave Web Search does not retrieve page content")
