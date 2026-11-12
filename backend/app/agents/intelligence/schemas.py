"""Product intelligence (LLM 2) schemas.

- `PageExtraction` is the LLM 2 output contract: what the model may *propose*
  for one retrieved page. Every value must come with a verbatim quote.
- `CompetitorProduct` is what the application returns, after deterministic
  checks (quotes present in the page, values present in their quotes, price
  parsed and checked for sale/conditional context). A field is only populated
  when its check passed; otherwise it is null and its `FieldEvidence` says why.

Nothing here is a match decision. Matching and price validation are Phase 6.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from app.agents.discovery.schemas import SearchTarget
from app.schemas.search import RetrievalStatus

# --------------------------------------------------------------------------- LLM contract


class PageType(StrEnum):
    PRODUCT_PAGE = "product_page"  # one product's own page
    LISTING_PAGE = "listing_page"  # category/search/brand listing with several products
    OTHER = "other"  # article, review, help page, error page...


class ExtractedSpec(BaseModel):
    name: str
    value: str  # verbatim from the page


class PageExtraction(BaseModel):
    page_type: PageType
    product_title: str | None
    title_quote: str | None
    brand: str | None
    brand_quote: str | None
    model: str | None
    model_quote: str | None
    sku: str | None
    sku_quote: str | None
    # The current standard selling price exactly as displayed, e.g. "£579.00".
    displayed_price: str | None
    currency: str | None  # ISO 4217 code, only if the page makes it explicit
    price_quote: str | None
    price_is_conditional: bool  # "from", per month, finance, deposit, bundle, option-dependent
    # Crossed-out / was / RRP / saving / percentage texts seen and NOT used.
    ignored_prices: list[str]
    availability: str | None
    availability_quote: str | None
    specifications: list[ExtractedSpec]
    confidence: float = Field(ge=0, le=1)  # self-reported, uncalibrated
    notes: str | None


# --------------------------------------------------------------------------- application records


class IntelligenceMode(StrEnum):
    LLM_ENABLED = "LLM_ENABLED"  # LLM 2 extracts from retrieved pages
    LLM_DISABLED = "LLM_DISABLED"  # search + retrieval only; nothing extracted


class ExtractionStatus(StrEnum):
    COMPLETED = "COMPLETED"  # LLM 2 ran and its output was checked
    SKIPPED_NO_LLM = "SKIPPED_NO_LLM"  # LLM 2 not configured or disabled: nothing extracted
    SKIPPED_NOT_RETRIEVED = "SKIPPED_NOT_RETRIEVED"  # page failed, blocked, empty or not fetched
    FAILED = "FAILED"  # LLM call failed or output stayed invalid after repair


class ExtractedField(StrEnum):
    PRODUCT_TITLE = "product_title"
    BRAND = "brand"
    MODEL = "model"
    SKU = "sku"
    PRICE = "price"
    AVAILABILITY = "availability"


class FieldStatus(StrEnum):
    EXTRACTED = "EXTRACTED"  # value present in its quote, quote present in the page
    NOT_FOUND = "NOT_FOUND"  # LLM 2 found no value on the page
    UNSUPPORTED = "UNSUPPORTED"  # quote missing/not in page, or value not in its quote
    EXCLUDED_SALE_PRICE = "EXCLUDED_SALE_PRICE"  # amount shown as was/RRP/original/saving
    AMBIGUOUS = "AMBIGUOUS"  # conditional price, no explicit currency, or not a single-product page
    CONFLICT = "CONFLICT"  # e.g. stated currency disagrees with the symbol shown
    INVALID = "INVALID"  # unparseable or non-positive amount


class FieldEvidence(BaseModel):
    field: ExtractedField
    status: FieldStatus
    value: str | None  # as proposed by LLM 2 (kept for audit even when rejected)
    quote: str | None  # verbatim page text supporting it
    note: str | None = None


class SearchHit(BaseModel):
    """One domain-restricted search result (evidence of where the page came from)."""

    domain: str  # the approved search target
    query_text: str
    provider: str
    provider_request_id: str | None
    rank: int
    url: str  # as returned by the provider
    canonical_url: str
    hostname: str
    title: str | None
    snippet: str | None  # never used as price evidence
    published_raw: str | None
    retrieved_at: datetime
    mentions_model: bool  # deterministic, title or URL path
    mentions_brand: bool


class PageRecord(BaseModel):
    """Retrieval metadata. Full page text is not returned (third-party content)."""

    status: RetrievalStatus | None  # None = not fetched (provider has no retrieval)
    provider: str
    provider_request_id: str | None = None
    requested_url: str
    final_url: str | None = None
    title: str | None = None
    content_chars: int = 0
    chars_sent_to_llm: int = 0
    retrieved_at: datetime | None = None
    cache_source: str | None = None
    error: str | None = None


class CompetitorProduct(BaseModel):
    """A candidate competitor product page and what could be extracted from it.

    This is NOT a match: whether it is the same product as the Stompbox item
    is decided in Phase 6.
    """

    candidate_id: str
    stompbox_product_id: str
    competitor: SearchTarget  # domain, name, type, approval status, benchmark eligibility
    product_url: str  # final page URL when retrieved, else the search URL
    search: SearchHit
    page: PageRecord
    extraction_status: ExtractionStatus
    extraction_error: str | None = None
    page_type: PageType | None = None
    # Populated only when the field's evidence status is EXTRACTED.
    product_title: str | None = None
    brand: str | None = None
    model: str | None = None
    sku: str | None = None
    price: Decimal | None = None  # standard currently displayed price; never a was/RRP/saving
    currency: str | None = None
    price_text: str | None = None  # the price exactly as displayed
    availability: str | None = None
    specifications: list[ExtractedSpec] = Field(default_factory=list)
    # Informational: the page shows sale/was/RRP wording. No discount is computed.
    sale_indicated: bool = False
    ignored_prices: list[str] = Field(default_factory=list)
    field_evidence: list[FieldEvidence] = Field(default_factory=list)
    llm_confidence: float | None = None  # self-reported by LLM 2, uncalibrated
    llm_notes: str | None = None
    llm_request_ids: list[str] = Field(default_factory=list)


class SkippedHit(BaseModel):
    domain: str
    url: str
    title: str | None
    reason: str


class DomainSearchSummary(BaseModel):
    domain: str
    queries: list[str]
    results: int = 0
    out_of_scope_dropped: int = 0
    pages_selected: int = 0
    errors: list[str] = Field(default_factory=list)


class ProductSummary(BaseModel):
    product_id: str
    product_name: str
    brand: str
    manufacturer_model: str


class ProductIntelligence(BaseModel):
    product: ProductSummary
    queries: list[str]
    domains: list[DomainSearchSummary]
    candidates: list[CompetitorProduct]
    not_selected: list[SkippedHit]


class SkippedTarget(BaseModel):
    domain: str
    company_type: str | None
    reason: str


class IntelligenceUsage(BaseModel):
    search_requests: int = 0
    search_errors: int = 0
    search_cost_usd: Decimal = Decimal(0)
    retrieval_requests: int = 0
    retrieval_errors: int = 0
    pages_requested: int = 0
    pages_ok: int = 0
    retrieval_cost_usd: Decimal = Decimal(0)
    llm_model: str | None = None
    llm_calls: int = 0
    llm_failures: int = 0
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_cost_usd: Decimal | None = None  # None when OpenRouter reported no cost


class IntelligenceRunResult(BaseModel):
    run_id: str
    mode: IntelligenceMode
    llm_disabled_reason: str | None = None
    started_at: datetime
    finished_at: datetime
    search_provider: str
    search_targets: list[SearchTarget]
    skipped_targets: list[SkippedTarget]
    products: list[ProductIntelligence]
    usage: IntelligenceUsage
    warnings: list[str]
