"""Product Intelligence Agent (LLM 2) schemas.

LLM 2 is one logical agent with three tasks (implementation plan §3.6, §8.6):

- Task B, extraction: `PageExtraction` is what the model may *propose* for one
  retrieved page. Every value must come with a verbatim quote.
- Task A, match proposal: `MatchProposal` is the model's proposed relation of
  the page's product to the catalogue product, with verbatim quotes.
- Task C, combined output: assembled in Python (`app.services.matching.view`),
  no LLM call.

`CompetitorProduct` is what the application returns, after deterministic
checks (quotes present in the page, values present in their quotes, price
parsed and checked for sale/conditional context). A field is only populated
when its check passed; otherwise it is null and its `FieldEvidence` says why.

Nothing here is a final match decision: Python's matcher makes it, and an LLM 2
proposal can only add uncertainty (approved decisions D-26, D-34).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.agents.discovery.schemas import SearchTarget
from app.schemas.common import AvailabilityStatus, MatchStatus
from app.schemas.search import RetrievalStatus
from app.schemas.images import ProductImage

# --------------------------------------------------------------------------- LLM contract, Task B


class PageType(StrEnum):
    PRODUCT_PAGE = "product_page"  # one product's own page
    LISTING_PAGE = "listing_page"  # category/search/brand listing with several products
    OTHER = "other"  # article, review, help page, error page...


class ExtractedSpec(BaseModel):
    name: str
    value: str  # verbatim from the page
    # LLM 2's proposed normalised name (e.g. "strings"). Python derives the final key.
    normalized_name: str | None = None


class ProductOption(BaseModel):
    """A selectable product option shown on the page (e.g. finish, handedness)."""

    name: str  # as written, e.g. "Colour"
    values: list[str]  # every value shown, verbatim
    selected_value: str | None  # the value shown as selected, if any
    quote: str  # verbatim page text showing the option


class OptionPrice(BaseModel):
    """A current price the page shows for one specific option or variant."""

    option: str  # the option/variant it belongs to, as written
    displayed_price: str  # e.g. "£599.00"
    price_quote: str


class IssueKind(StrEnum):
    AMBIGUOUS_VARIANT = "AMBIGUOUS_VARIANT"  # unclear which variant the page (or its price) is for
    CONFLICTING_INFORMATION = "CONFLICTING_INFORMATION"  # the page contradicts itself
    MULTIPLE_PRICES = "MULTIPLE_PRICES"  # several possible current prices
    MISSING_INFORMATION = "MISSING_INFORMATION"  # an expected field is not shown


class ExtractionIssue(BaseModel):
    kind: IssueKind
    fields: list[str]  # e.g. ["price"], ["model", "finish"]
    description: str
    quotes: list[str]  # verbatim page excerpts showing the issue


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
    # Stage 1 additions (defaults keep older fakes/fixtures valid; strict schema requires them).
    options: list[ProductOption] = Field(default_factory=list)
    option_prices: list[OptionPrice] = Field(default_factory=list)
    # Proposal only; None = no proposal (older fakes). UNKNOWN disagrees with any definite mapping.
    availability_normalized: AvailabilityStatus | None = None
    issues: list[ExtractionIssue] = Field(default_factory=list)


# --------------------------------------------------------------------------- LLM contract, Task A


class ProposedMatch(StrEnum):
    EXACT_MATCH = "EXACT_MATCH"
    EQUIVALENT_VARIANT = "EQUIVALENT_VARIANT"
    SIMILAR_ALTERNATIVE = "SIMILAR_ALTERNATIVE"
    NOT_A_MATCH = "NOT_A_MATCH"


# Approved mapping (P-3): used only to compare the proposal with Python's decision.
PROPOSAL_STATUS: dict[ProposedMatch, MatchStatus] = {
    ProposedMatch.EXACT_MATCH: MatchStatus.EXACT_MATCH,
    ProposedMatch.EQUIVALENT_VARIANT: MatchStatus.EQUIVALENT_VARIANT,
    ProposedMatch.SIMILAR_ALTERNATIVE: MatchStatus.SIMILAR_MATCH,
    ProposedMatch.NOT_A_MATCH: MatchStatus.NO_MATCH,
}


class ProposalDifference(BaseModel):
    attribute: str  # e.g. "finish", "handedness", "pickups"
    catalogue_value: str | None
    page_value: str | None  # verbatim from the page
    quote: str | None


class ProposalReason(BaseModel):
    text: str
    quote: str | None  # verbatim page text supporting the reason


class MatchProposal(BaseModel):
    classification: ProposedMatch
    differing_attributes: list[ProposalDifference]
    reasons: list[ProposalReason]
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

    @classmethod
    def _missing_(cls, value: object) -> ExtractionStatus | None:
        """Read-only aliases for values written by earlier versions (saved run files).

        SKIPPED_LLM_DISABLED was the name of SKIPPED_NO_LLM until the Phase 5 finalisation
        (12 Nov 2026): a retrieved page that LLM 2 did not read because it was not configured or
        disabled. Same meaning, so it loads as SKIPPED_NO_LLM. Never written again; not part of
        the JSON schema. Anything else is still rejected."""
        return LEGACY_EXTRACTION_STATUS.get(value) if isinstance(value, str) else None


# Legacy value -> current status (see ExtractionStatus._missing_).
LEGACY_EXTRACTION_STATUS: dict[str, ExtractionStatus] = {
    "SKIPPED_LLM_DISABLED": ExtractionStatus.SKIPPED_NO_LLM,
}


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


class SpecCategory(StrEnum):
    """Deterministic specification categories (Stage 6.1). Only ELECTRONICS and
    PICKUP_CONFIGURATION specs take part in the electronics matching rules."""

    POWER_SUPPLY = "power_supply"  # 9V adapter, battery, mains, current draw
    OUTPUT_POWER = "output_power"  # amplifier output in watts
    CONTROLS = "controls"  # knobs / switches: never evidence of electronics type
    ELECTRONICS = "electronics"  # electronics, preamp, pickups, pickup system
    PICKUP_CONFIGURATION = "pickup_configuration"  # HSS / SSS / HH ... layout
    OTHER = "other"  # anything else, or ambiguous


class NormalizedSpec(BaseModel):
    name: str  # as written on the page
    value: str  # verbatim, present in the page text
    key: str | None = None  # deterministic normalised name (e.g. "strings"); None in older records
    category: SpecCategory | None = None  # None in older records: the matcher derives it deterministically


class IgnoredPriceCategory(StrEnum):
    SALE = "SALE"  # was / RRP / original / saving / crossed-out
    DELIVERY = "DELIVERY"  # delivery, shipping, postage, click & collect: never sale evidence
    FINANCE = "FINANCE"  # per month, deposit, finance
    OTHER = "OTHER"  # another product, accessory, unclear


class IgnoredPriceRecord(BaseModel):
    text: str
    category: IgnoredPriceCategory


class VerifiedOption(BaseModel):
    name: str
    values: list[str]
    # Only set when the quote explicitly marks it as selected ("selected: Black", "Black (selected)", "✓ Black").
    selected_value: str | None = None
    claimed_selected_value: str | None = None  # what LLM 2 said was selected, kept for audit even if unverified
    selection_evidence: str | None = None  # the quote text that marks the selection
    quote: str


class OptionPriceEvidence(BaseModel):
    """A per-option current price, checked like the main price. Never auto-selected."""

    option: str
    status: FieldStatus
    price_text: str | None
    amount: Decimal | None = None  # only when EXTRACTED
    currency: str | None = None
    quote: str | None
    note: str | None = None
    # Set when several current prices were shown and Python tried to associate them with the
    # catalogue variant; non-matching options stay here as alternative-product information.
    matches_catalogue_variant: bool | None = None
    variant_checks: dict[str, str] = Field(default_factory=dict)  # attribute -> MATCH/CONFLICT/UNKNOWN/ABSENT


class IssueRecord(BaseModel):
    kind: IssueKind
    fields: list[str]
    description: str
    quotes: list[str]
    verified: bool  # every quote found in the page text; unverified issues never affect decisions


class ProposalStatus(StrEnum):
    COMPLETED = "COMPLETED"  # LLM 2 proposed and at least one supporting quote was verified
    UNSUPPORTED = "UNSUPPORTED"  # no supporting quote verified: ignored by the matcher
    SKIPPED_NO_LLM = "SKIPPED_NO_LLM"
    SKIPPED_NOT_ELIGIBLE = "SKIPPED_NOT_ELIGIBLE"  # not a retrieved single-product page, extraction failed, or disabled
    FAILED = "FAILED"  # LLM call failed or output stayed invalid after repair


class VerifiedReason(BaseModel):
    text: str
    quote: str | None
    verified: bool


class VerifiedDifference(BaseModel):
    attribute: str
    catalogue_value: str | None
    page_value: str | None
    quote: str | None
    verified: bool  # quote in the page and page value in the quote
    cosmetic: bool  # decided in Python from the attribute name (finish / colour only)


class MatchProposalRecord(BaseModel):
    classification: ProposedMatch
    corresponding_status: MatchStatus  # the P-3 mapping; never the final status
    reasons: list[VerifiedReason] = Field(default_factory=list)
    differing_attributes: list[VerifiedDifference] = Field(default_factory=list)
    confidence: float | None = None  # self-reported by LLM 2, uncalibrated, never used in decisions
    notes: str | None = None
    request_ids: list[str] = Field(default_factory=list)


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

    @model_validator(mode="before")
    @classmethod
    def _note_legacy_status(cls, data: Any) -> Any:
        """Keep the original legacy status visible: a record saved as SKIPPED_LLM_DISABLED loads
        as SKIPPED_NO_LLM (same meaning) and, when it had no explanation, says so in
        extraction_error. The input is never modified (a copy is returned)."""
        if isinstance(data, dict):
            legacy = data.get("extraction_status")
            if isinstance(legacy, str) and legacy in LEGACY_EXTRACTION_STATUS and not data.get("extraction_error"):
                current = LEGACY_EXTRACTION_STATUS[legacy].value
                data = {**data, "extraction_error": f"LLM 2 not used; page retrieved, nothing extracted (saved as legacy "
                                                    f"status {legacy}, now {current})"}  # fmt: skip
        return data
    # Populated only when the field's evidence status is EXTRACTED.
    product_title: str | None = None
    brand: str | None = None
    model: str | None = None
    sku: str | None = None
    price: Decimal | None = None  # standard currently displayed price; never a was/RRP/saving
    currency: str | None = None
    price_text: str | None = None  # the price exactly as displayed
    availability: str | None = None
    specifications: list[NormalizedSpec] = Field(default_factory=list)
    # Informational: the page shows sale/was/RRP wording (never delivery charges). No discount is computed.
    sale_indicated: bool = False
    ignored_prices: list[str] = Field(default_factory=list)
    ignored_price_details: list[IgnoredPriceRecord] = Field(default_factory=list)  # same texts, with a category
    field_evidence: list[FieldEvidence] = Field(default_factory=list)
    llm_confidence: float | None = None  # self-reported by LLM 2, uncalibrated
    llm_notes: str | None = None
    llm_request_ids: list[str] = Field(default_factory=list)
    # Task B additions (verified in Python).
    options: list[VerifiedOption] = Field(default_factory=list)
    option_prices: list[OptionPriceEvidence] = Field(default_factory=list)
    extraction_issues: list[IssueRecord] = Field(default_factory=list)
    # Deterministic availability from the verified text (UNKNOWN when missing/unclear).
    availability_status: AvailabilityStatus = AvailabilityStatus.UNKNOWN
    availability_proposed: AvailabilityStatus | None = None  # LLM 2's proposal, for audit
    availability_reason: str | None = None
    # Task A: LLM 2's match proposal (None in older records or when not run).
    match_proposal: MatchProposalRecord | None = None
    match_proposal_status: ProposalStatus | None = None
    match_proposal_error: str | None = None
    # This listing's own primary image (never the catalogue image). Display-only: never used by
    # matching or price validation. None on records made before product images existed.
    image: ProductImage | None = None


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
    llm_calls: int = 0  # LLM 2 requests actually sent (extraction + match proposal)
    # Failed requests PLUS pages/proposals not attempted after an account-level error (those are
    # never sent, so they are not in llm_calls). This count can therefore exceed llm_calls.
    llm_failures: int = 0
    llm_match_calls: int = 0  # Task A requests actually sent (part of llm_calls)
    llm_match_failures: int = 0  # Task A failures incl. not attempted (part of llm_failures; can exceed llm_match_calls)
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
