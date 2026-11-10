"""Provider-neutral search and retrieval schemas.

Every search provider adapter returns these models, so the rest of the
application never sees provider-specific payloads.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.services.domains import normalize_hostname


class SearchScope(StrEnum):
    GLOBAL = "GLOBAL"  # whole web
    DOMAIN = "DOMAIN"  # restricted to approved competitor domains


class CostBasis(StrEnum):
    PROVIDER_REPORTED = "PROVIDER_REPORTED"  # provider returned the cost/credits used
    ESTIMATED = "ESTIMATED"  # computed from configured list prices
    UNKNOWN = "UNKNOWN"


class CostInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    usd: Decimal | None = None
    basis: CostBasis = CostBasis.UNKNOWN
    detail: str | None = None  # e.g. "2 credits"


class SearchQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=400)
    scope: SearchScope = SearchScope.GLOBAL
    # Normalised hostnames. Required for DOMAIN scope, forbidden for GLOBAL.
    domains: tuple[str, ...] = ()
    max_results: int = Field(default=10, ge=1, le=20)
    # ISO 3166-1 alpha-2 country used for localisation (not a hard filter).
    country: str | None = Field(default="GB", pattern=r"^[A-Z]{2}$")
    product_id: str | None = None

    @field_validator("domains")
    @classmethod
    def _normalise_domains(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(normalize_hostname(d) for d in v))

    @model_validator(mode="after")
    def _check_scope(self) -> "SearchQuery":
        if self.scope is SearchScope.DOMAIN and not self.domains:
            raise ValueError("DOMAIN scope requires at least one domain")
        if self.scope is SearchScope.GLOBAL and self.domains:
            raise ValueError("GLOBAL scope must not restrict domains")
        return self


class SearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    rank: int = Field(ge=1)
    # Exactly as returned by the provider (evidence).
    url: HttpUrl
    # Deduplication / fetch form (see app.services.urls.canonical_url).
    canonical_url: str
    hostname: str
    title: str | None = None
    # Search snippet. Never sufficient evidence of a current price.
    snippet: str | None = None
    # Provider-reported publication/crawl date, verbatim. Not proof of the
    # retailer's last update.
    published_raw: str | None = None
    score: float | None = None


class SearchResponse(BaseModel):
    provider: str
    query: SearchQuery
    results: list[SearchResult]
    # Results the provider returned outside the requested domains (dropped).
    out_of_scope_dropped: int = 0
    retrieved_at: datetime
    latency_ms: float
    request_id: str | None = None
    cost: CostInfo = CostInfo()


class RetrievalStatus(StrEnum):
    OK = "OK"
    EMPTY = "EMPTY"  # fetched but no usable content
    BLOCKED = "BLOCKED"  # fetched a bot-check / access-denied page, not the product page
    FAILED = "FAILED"


class RetrievedPage(BaseModel):
    provider: str
    requested_url: str
    url: HttpUrl | None = None
    hostname: str | None = None
    status: RetrievalStatus
    title: str | None = None
    content: str | None = None
    content_format: str | None = None  # "markdown" or "text"
    retrieved_at: datetime
    # Provider cache metadata when exposed (e.g. Exa "cached"/"crawled").
    cache_source: str | None = None
    error: str | None = None

    @property
    def content_chars(self) -> int:
        return len(self.content or "")


class RetrievalResponse(BaseModel):
    provider: str
    pages: list[RetrievedPage]
    retrieved_at: datetime
    latency_ms: float
    request_id: str | None = None
    cost: CostInfo = CostInfo()
