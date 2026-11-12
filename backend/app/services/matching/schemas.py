"""Phase 6 schemas: product matching and price validation results.

Match validity and price validity are separate: a candidate can be an
EXACT_MATCH with a missing or invalid price, and a valid price says nothing
about identity. Only a candidate that passes both (plus condition and
benchmark eligibility) qualifies for direct UK price comparison.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from app.agents.discovery.schemas import SearchTarget
from app.agents.intelligence.schemas import ExtractionStatus, IntelligenceMode
from app.schemas.common import EvidenceStatus, MatchStatus


class MatchConfidence(StrEnum):
    HIGH = "HIGH"  # identity confirmed by an extracted model/SKU field plus title or URL
    MEDIUM = "MEDIUM"  # identity confirmed by page title and URL only (e.g. no-LLM mode)
    LOW = "LOW"  # anything short of a confirmed exact identity


class CheckResult(StrEnum):
    MATCH = "MATCH"  # evidence confirms the Stompbox value
    CONFLICT = "CONFLICT"  # evidence shows a different value
    UNVERIFIED = "UNVERIFIED"  # evidence neither confirms nor contradicts
    NOT_APPLICABLE = "NOT_APPLICABLE"  # the Stompbox product does not define this attribute


class EvidenceSource(StrEnum):
    PAGE_TITLE = "page_title"  # title of the retrieved page
    SEARCH_TITLE = "search_title"  # title in the search result
    URL = "url"  # product URL path
    EXTRACTED_TITLE = "extracted_title"  # LLM 2 field, quote verified in Phase 5
    EXTRACTED_BRAND = "extracted_brand"
    EXTRACTED_MODEL = "extracted_model"
    EXTRACTED_SKU = "extracted_sku"
    AVAILABILITY = "availability"


class EvidenceRef(BaseModel):
    source: EvidenceSource
    text: str  # the exact text the decision used (truncated)


class AttributeCheck(BaseModel):
    attribute: str  # brand, model, handedness, finish, strings, size, generation, configuration, wattage, bundle
    result: CheckResult
    critical: bool  # a critical CONFLICT prevents EXACT_MATCH
    expected: str | None  # Stompbox value
    found: list[str] = Field(default_factory=list)  # values seen in competitor evidence
    evidence: list[EvidenceRef] = Field(default_factory=list)
    note: str | None = None


class Condition(StrEnum):
    NEW = "NEW"
    USED = "USED"  # used / secondhand / pre-owned
    REFURBISHED = "REFURBISHED"  # refurbished / B-stock / ex-demo / open box
    UNKNOWN = "UNKNOWN"  # not stated (most new retail listings)


class ConditionCheck(BaseModel):
    condition: Condition
    comparable_to_new: bool  # USED / REFURBISHED are never equivalent to a new Stompbox product
    evidence: list[EvidenceRef] = Field(default_factory=list)
    note: str | None = None


class PageKind(StrEnum):
    PRODUCT_PAGE = "product_page"  # LLM 2 said so
    NON_PRODUCT_PAGE = "non_product_page"  # LLM 2 said listing/other, or URL is a blog/forum/guide/category
    UNKNOWN = "unknown"  # no LLM classification and no URL signal


class PageCheck(BaseModel):
    kind: PageKind
    retrieved: bool  # the page itself was retrieved (not only a search result)
    basis: str


class PriceStatus(StrEnum):
    VALID = "VALID"  # current displayed price, GBP, quote re-verified
    WRONG_CURRENCY = "WRONG_CURRENCY"  # valid amount but not GBP: never compared
    MISSING = "MISSING"  # LLM 2 looked and found no price
    NOT_EXTRACTED = "NOT_EXTRACTED"  # no extraction ran (no LLM, page not retrieved, LLM failed)
    EXCLUDED_SALE_PRICE = "EXCLUDED_SALE_PRICE"  # was / RRP / original / saving amount
    AMBIGUOUS = "AMBIGUOUS"  # conditional price, no explicit currency, not a single-product page
    UNSUPPORTED = "UNSUPPORTED"  # quote not found in the page or amount not in the quote
    CONFLICT = "CONFLICT"  # contradictory currency or values
    INVALID = "INVALID"  # unparseable, non-positive, or internally inconsistent record


class PriceValidation(BaseModel):
    status: PriceStatus
    evidence_status: EvidenceStatus
    price: Decimal | None = None  # only when status is VALID or WRONG_CURRENCY
    currency: str | None = None
    price_text: str | None = None
    quote: str | None = None  # verbatim page text supporting the price
    source_url: str
    retrieved_at: datetime | None = None
    sale_indicated: bool = False  # page shows sale/was wording; informational, no discount computed
    reasons: list[str] = Field(default_factory=list)


class PriceComparison(BaseModel):
    eligible: bool  # direct UK price comparison allowed
    reasons: list[str] = Field(default_factory=list)  # why not, when not eligible
    stompbox_price: Decimal | None = None
    stompbox_currency: str | None = None
    competitor_price: Decimal | None = None
    # competitor - Stompbox, GBP. Only when eligible. No percentages or savings.
    difference_gbp: Decimal | None = None


class CandidateMatch(BaseModel):
    candidate_id: str
    stompbox_product_id: str
    competitor: SearchTarget
    product_url: str
    extraction_status: ExtractionStatus
    match_status: MatchStatus
    match_confidence: MatchConfidence
    match_reasons: list[str]
    review_reasons: list[str] = Field(default_factory=list)  # what a reviewer must resolve (REVIEW_REQUIRED)
    page: PageCheck
    brand: AttributeCheck
    model: AttributeCheck
    variants: list[AttributeCheck]
    condition: ConditionCheck
    price: PriceValidation
    comparison: PriceComparison


class ProductMatchSummary(BaseModel):
    product_id: str
    product_name: str
    brand: str
    manufacturer_model: str
    stompbox_price: Decimal | None
    counts: dict[MatchStatus, int]
    comparable_prices: int
    candidates: list[CandidateMatch]  # EXACT first, then REVIEW, SIMILAR, NO_MATCH


class MatchingRunResult(BaseModel):
    source_run_id: str
    source_mode: IntelligenceMode
    evaluated_at: datetime
    price_max_age_hours: int
    products: list[ProductMatchSummary]
    warnings: list[str]
