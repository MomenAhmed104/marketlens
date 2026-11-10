"""Provider interface and shared deterministic post-processing."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

from pydantic import ValidationError

from app.schemas.search import (
    RetrievalResponse,
    RetrievalStatus,
    SearchQuery,
    SearchResponse,
    SearchResult,
    SearchScope,
)
from app.services.domains import normalize_hostname
from app.services.search.http import ProviderHttpClient
from app.services.urls import canonical_url


def now_utc() -> datetime:
    return datetime.now(UTC)


_BLOCK_PAGE = re.compile(
    r"client challenge|captcha|are you a robot|verify (?:that )?you are (?:a )?human|access denied|"
    r"just a moment\.\.\.|attention required|enable javascript and cookies|request blocked",
    re.IGNORECASE,
)
BLOCK_PAGE_MAX_CHARS = 2000


def content_status(text: str | None) -> RetrievalStatus:
    """OK, EMPTY, or BLOCKED for short bot-check / access-denied pages.

    Only short pages are checked, so a real product page that happens to
    mention e.g. "captcha" is not misclassified.
    """
    if not text or not text.strip():
        return RetrievalStatus.EMPTY
    if len(text) <= BLOCK_PAGE_MAX_CHARS and _BLOCK_PAGE.search(text):
        return RetrievalStatus.BLOCKED
    return RetrievalStatus.OK


def host_in_domains(hostname: str, domains: tuple[str, ...]) -> bool:
    """True if hostname is one of domains or a subdomain of one."""
    return any(hostname == d or hostname.endswith("." + d) for d in domains)


@dataclass
class RawHit:
    """Provider-agnostic intermediate form, before validation and scoping."""

    url: str
    title: str | None = None
    snippet: str | None = None
    published_raw: str | None = None
    score: float | None = None


def finalize_results(
    provider: str, query: SearchQuery, hits: list[RawHit]
) -> tuple[list[SearchResult], int]:
    """Validate URLs, drop duplicates, enforce domain scope, assign ranks.

    Duplicates are detected on the canonical URL; the first original URL is kept
    as evidence. Returns (results, number dropped as out of scope). Scope is
    enforced here, deterministically, whatever the provider's own filter did.
    """
    results: list[SearchResult] = []
    seen: set[str] = set()
    out_of_scope = 0
    for hit in hits:
        try:
            hostname = normalize_hostname(hit.url)
            key = canonical_url(hit.url)
        except ValueError:
            continue
        if query.scope is SearchScope.DOMAIN and not host_in_domains(hostname, query.domains):
            out_of_scope += 1
            continue
        if key in seen:
            continue
        try:
            result = SearchResult(
                provider=provider,
                rank=len(results) + 1,
                url=hit.url,
                canonical_url=key,
                hostname=hostname,
                title=(hit.title or "").strip() or None,
                snippet=(hit.snippet or "").strip() or None,
                published_raw=hit.published_raw,
                score=hit.score,
            )
        except ValidationError:
            continue
        seen.add(key)
        results.append(result)
    return results, out_of_scope


class SearchProvider(ABC):
    name: ClassVar[str]
    supports_retrieval: ClassVar[bool]

    def __init__(self, http: ProviderHttpClient):
        self.http = http

    @abstractmethod
    async def search(self, query: SearchQuery) -> SearchResponse: ...

    @abstractmethod
    async def retrieve(self, urls: list[str]) -> RetrievalResponse:
        """Fetch main page content for the given URLs (one RetrievedPage per URL)."""

    async def aclose(self) -> None:
        await self.http.aclose()

    async def __aenter__(self) -> "SearchProvider":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()
