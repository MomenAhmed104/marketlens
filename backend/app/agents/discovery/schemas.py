"""Competitor discovery schemas.

Two kinds of model live here:
- LLM output contracts (`DiscoveryPlan`, `ClassificationBatch`): what LLM 1 may
  *propose*. They are validated and then checked again by deterministic code.
- Run records (`DiscoveredCompetitor`, `DiscoveryRunResult`, ...): what the
  application stores and shows. Decisions in them are made by Python rules.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, ValidationError, model_validator

from app.schemas.common import DomainApprovalStatus
from app.schemas.competitor import CompanyType, CompetitorDomain
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.schemas.search import SearchQuery

# --------------------------------------------------------------------------- LLM contracts


class ProductQueryPlan(BaseModel):
    product_id: str
    # Whole-web queries to find UK shops selling this product or its category.
    global_queries: list[str]
    # Search phrasings for use inside approved competitor domains.
    targeted_query_variants: list[str]


class DiscoveryPlan(BaseModel):
    products: list[ProductQueryPlan]


class UkSeller(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class CompetitorClass(StrEnum):
    """Competitor classification. Separate from approval and price-benchmark eligibility."""

    DIRECT_COMPETITOR = "DIRECT_COMPETITOR"  # UK retailer with meaningful catalogue product overlap
    RELATED_RETAILER = "RELATED_RETAILER"  # retailer of adjacent music products, or a marketplace
    NEEDS_REVIEW = "NEEDS_REVIEW"  # manufacturer, directory, other, or insufficient/uncertain evidence


class UkRelevanceEvidenceKind(StrEnum):
    UK_PHYSICAL_STORE = "UK_PHYSICAL_STORE"
    UK_ADDRESS = "UK_ADDRESS"  # must contain a UK postcode
    UK_COMPANY_REGISTRATION = "UK_COMPANY_REGISTRATION"  # e.g. "registered in England and Wales, company number ..."
    UK_DELIVERY = "UK_DELIVERY"  # delivery or shipping to the UK
    NON_UK_OPERATOR = "NON_UK_OPERATOR"  # the business is based or registered outside the UK
    NO_UK_SERVICE = "NO_UK_SERVICE"  # e.g. "we only ship within the US"
    WEAK_SIGNAL = "WEAK_SIGNAL"  # GBP prices, a .uk domain, "UK" wording: recorded, never sufficient


class UkRelevanceQuote(BaseModel):
    """LLM 1 cites this: an exact quote from one evidence item. Checked by code, never trusted."""

    url: str
    quote: str
    kind: UkRelevanceEvidenceKind


class VerifiedUkQuote(UkRelevanceQuote):
    verified: bool  # the quote is verbatim in the stored title or snippet of that URL
    supports: bool  # verified, and its text fits its kind, so it counts toward the result
    note: str | None = None  # why it does not count


class UkEvidenceAssessment(BaseModel):
    """The deterministic result of checking LLM 1's UK evidence quotes (`rules.assess_uk_evidence`).

    There is no UK category or confidence: LLM 1 cites quotes, Python verifies them and
    decides whether they are sufficient. Separate from approval, competitor class and
    benchmark permission. Older stored records may carry extra legacy keys (e.g.
    `relevance`, `confidence`); they are ignored on load and never used."""

    evidence: list[VerifiedUkQuote] = Field(default_factory=list)  # supporting (verified) quotes only
    # Quotes LLM 1 cited that did NOT count (not found verbatim, or text that does not fit
    # its kind). Reviewer context only: never evidence, never shown as verified.
    unverified_claims: list[VerifiedUkQuote] = Field(default_factory=list)
    # Verified evidence of UK relevance: a UK shop, UK address or UK company registration;
    # or a based-abroad quote together with a UK delivery quote.
    sufficient: bool = False
    # A verified quote says the UK is not delivered to or served.
    contradicted: bool = False
    # Verified UK-presence evidence and a verified based-abroad quote at the same time.
    # Not a contradiction (the business may still serve the UK), but never auto-approved.
    mixed_location: bool = False
    # Distinct evidence pages (canonical URLs) among the supporting quotes; several quotes
    # from one page count as one source.
    independent_sources: int = 0
    reasons: list[str] = Field(default_factory=list)


class UkReview(BaseModel):
    """The UK review view of one domain (`rules.uk_review`). Derived, never stored:
    computed from the stored assessment and the reviewer's decision whenever it is shown.
    It never approves, rejects or changes benchmark permission."""

    assessed: bool  # False: no UK evidence assessment (LLM disabled, failed, or an older record)
    sufficient: bool
    contradicted: bool
    mixed_location: bool
    independent_sources: int
    summary: str  # one plain-English sentence for the reviewer
    reasons: list[str] = Field(default_factory=list)  # how the assessment was reached
    missing_evidence: list[str] = Field(default_factory=list)  # what would settle an uncertain case
    verified_evidence: list[VerifiedUkQuote] = Field(default_factory=list)
    unverified_claims: list[VerifiedUkQuote] = Field(default_factory=list)  # context only, never facts


UK_CLASSIFICATION_KEYS = ("uk_evidence",)


def _isolate_uk_fields(data: dict) -> tuple[dict, list[str]]:
    """Clean only the UK evidence of one raw classification, so a bad UK answer never
    fails the core classification (or the batch). Returns (cleaned data, warnings).

    `uk_evidence` that is missing or not a list -> []; each malformed item is dropped.
    Everything else is passed through untouched and validated exactly as before. Legacy
    `uk_relevance` / `uk_confidence` keys (older fixtures) are dropped: LLM 1 gives no
    UK verdict. Data without `uk_evidence` gets [] and no warning."""
    data = {k: v for k, v in data.items() if k not in ("uk_relevance", "uk_confidence")}
    if "uk_evidence" not in data:
        return data, []
    warnings: list[str] = []
    raw = data.get("uk_evidence")
    if raw is None:
        warnings.append("uk_evidence missing: no UK quotes")
        raw = []
    elif not isinstance(raw, list):
        warnings.append("uk_evidence is not a list: no UK quotes")
        raw = []
    kept = []
    for i, item in enumerate(raw):
        try:
            kept.append(UkRelevanceQuote.model_validate(item))
        except ValidationError as e:
            warnings.append(f"uk_evidence[{i}] dropped: {e.errors()[0]['msg']}")
    data["uk_evidence"] = kept
    return data, warnings


class CompanyClassification(BaseModel):
    domain: str
    # Exactly as written in the evidence; null if the evidence does not name it.
    company_name: str | None
    company_type: CompanyType
    relevant_categories: list[str]
    uk_seller: UkSeller
    sells_new_musical_instruments: bool
    relevance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    rationale: str  # trimmed by code; also explains competitor_class
    # Must be copied from the evidence provided; anything else is discarded.
    evidence_urls: list[str]
    # LLM 1's proposal; code safeguards (rules.classify_competitor) decide the final class.
    # The strict output schema makes the field required; the default only serves old fixtures.
    competitor_class: CompetitorClass = CompetitorClass.NEEDS_REVIEW
    # UK relevance evidence: quotes with their kind, no verdict. Required by the strict
    # output schema; the default serves old fixtures. Checked by code
    # (`rules.assess_uk_evidence`) and never trusted. A malformed UK answer is cleaned by
    # `_isolate_uk_fields` (never a validation error); see `uk_validation_warnings`.
    uk_evidence: list[UkRelevanceQuote] = Field(default_factory=list)

    _uk_warnings: list[str] = PrivateAttr(default_factory=list)

    @model_validator(mode="wrap")
    @classmethod
    def _isolate_uk(cls, data: Any, handler):
        if not isinstance(data, dict):  # e.g. an existing instance: keep it (and its warnings) as is
            return handler(data)
        data, warnings = _isolate_uk_fields(data)
        obj = handler(data)  # core fields: validated exactly as before (errors still raise)
        obj._uk_warnings = warnings
        return obj

    @property
    def uk_validation_warnings(self) -> list[str]:
        """What `_isolate_uk_fields` had to replace or drop in LLM 1's UK answer."""
        return list(self._uk_warnings)


class ClassificationBatch(BaseModel):
    companies: list[CompanyClassification]


# --------------------------------------------------------------------------- run records


class DiscoveryMode(StrEnum):
    LLM_ENABLED = "LLM_ENABLED"  # LLM 1 plans queries and classifies candidates
    LLM_DISABLED = "LLM_DISABLED"  # baseline queries only; no classification


class ClassificationStatus(StrEnum):
    COMPLETED = "COMPLETED"
    SKIPPED_LLM_DISABLED = "SKIPPED_LLM_DISABLED"
    FAILED = "FAILED"  # the LLM call failed or its output was unusable
    MISSING = "MISSING"  # the LLM answered but returned nothing for this domain


class EvidenceKind(StrEnum):
    DISCOVERY_RESULT = "discovery_result"  # from a global discovery query
    DOMAIN_PROFILE = "domain_profile"  # from a search restricted to the candidate domain


class EvidenceItem(BaseModel):
    url: str  # as returned by the provider
    canonical_url: str
    hostname: str
    title: str | None
    snippet: str | None
    kind: EvidenceKind
    query_text: str
    product_id: str | None
    provider: str
    provider_request_id: str | None
    retrieved_at: datetime
    # Deterministic signals (not identity decisions).
    mentions_product_model: bool = False
    mentions_brand: bool = False


class DiscoveredCompetitor(BaseModel):
    domain: str  # registrable domain, the deduplication key
    hostnames: list[str]
    homepage_url: str
    evidence: list[EvidenceItem]
    products_matched: list[str]  # product ids with a model-mentioning page on this domain
    uk_signals: list[str]
    classification_status: ClassificationStatus = ClassificationStatus.MISSING
    # LLM 1 proposal. All null/unknown unless classification_status is COMPLETED:
    # nothing here is ever filled in without the LLM's evidence-based answer.
    company_name: str | None = None
    company_type: CompanyType | None = None
    relevant_categories: list[str] = Field(default_factory=list)
    uk_seller: UkSeller = UkSeller.UNKNOWN
    sells_new_musical_instruments: bool | None = None
    relevance: float | None = None
    confidence: float | None = None
    rationale: str | None = None
    cited_evidence_urls: list[str] = Field(default_factory=list)
    # Competitor classification (separate from approval and benchmark eligibility).
    # proposed_*: LLM 1's answer, unchanged. competitor_class: final value after the
    # code safeguards; None only on records stored before classification existed.
    proposed_competitor_class: CompetitorClass | None = None
    competitor_class: CompetitorClass | None = None
    competitor_class_reasons: list[str] = Field(default_factory=list)
    classified_at: datetime | None = None  # when LLM 1's classification was applied
    classification_model: str | None = None  # model that produced it (as reported by OpenRouter)
    # UK relevance evidence: LLM 1's quotes checked against the evidence by code. Set for
    # every completed classification. None = not assessed (LLM disabled, classification
    # failed or missing, or an older record). Separate from approval, class and benchmarking.
    uk_assessment: UkEvidenceAssessment | None = None
    # The UK review view for this run: set by discovery after reviewer decisions are
    # applied, so the discovery API and events match the review API. Derived from
    # `uk_assessment` and `review_decision`; never persisted (the store clears it).
    uk_review: UkReview | None = None
    # Deterministic checks and decision
    name_verified: bool = False
    validation_notes: list[str] = Field(default_factory=list)
    approval_status: DomainApprovalStatus = DomainApprovalStatus.APPROVAL_REQUIRED
    decision_reasons: list[str] = Field(default_factory=list)
    price_benchmark: bool = False
    discovered_at: datetime
    # Persisted reviewer decision (Phase 4), if any. It overrides approval_status
    # and price_benchmark above; the LLM proposal fields are left as proposed.
    review_decision: DomainDecisionRecord | None = None
    # Status from the deterministic rules before a reviewer decision overrode it.
    automatic_approval_status: DomainApprovalStatus | None = None

    def as_search_target(self) -> SearchTarget:
        d = self.review_decision
        return SearchTarget(
            domain=self.domain, company_name=(d.company_name if d else None) or self.company_name,
            company_type=d.company_type if d else self.company_type,
            approval_status=self.approval_status, price_benchmark=self.price_benchmark,
        )  # fmt: skip


class SearchTarget(BaseModel):
    """A domain allowed into targeted product searches for this run."""

    domain: str
    company_name: str | None
    company_type: CompanyType | None  # None = reviewer approved it with the type kept unknown
    approval_status: DomainApprovalStatus  # PRECONFIGURED, AUTO_APPROVED (this run only) or APPROVED
    # The configured (workbook) or reviewer-set benchmark flag, never widened.
    price_benchmark: bool

    @classmethod
    def from_known(cls, c: CompetitorDomain) -> SearchTarget:
        return cls(domain=c.domain, company_name=c.name, company_type=c.company_type,
                   approval_status=c.approval_status, price_benchmark=c.price_benchmark)  # fmt: skip

    @classmethod
    def from_decision(cls, d: DomainDecisionRecord) -> SearchTarget:
        if d.decision is not ReviewDecision.APPROVED:
            raise ValueError(f"{d.domain} is not approved")
        return cls(domain=d.domain, company_name=d.company_name, company_type=d.company_type,
                   approval_status=DomainApprovalStatus.APPROVED, price_benchmark=d.benchmark_eligible)  # fmt: skip


class ExcludedDomain(BaseModel):
    domain: str
    reason: str
    evidence_count: int
    sample_urls: list[str]


class KnownCompetitorEvidence(BaseModel):
    """Search evidence that a configured competitor lists a selected product."""

    domain: str
    products_seen: list[str]
    evidence: list[EvidenceItem]


class QuerySource(StrEnum):
    BASELINE = "baseline"  # deterministic, always present
    LLM = "llm"  # proposed by LLM 1 and validated


class PlannedQuery(BaseModel):
    product_id: str
    source: QuerySource
    query: SearchQuery


class TargetedQuery(BaseModel):
    product_id: str
    domain: str
    company_type: CompanyType | None  # None only for reviewer-approved domains of unknown type
    approval_status: DomainApprovalStatus
    # False for manufacturer references (e.g. Gibson): never a retailer price benchmark.
    price_benchmark: bool
    source: QuerySource
    query: SearchQuery


class DiscoveryUsage(BaseModel):
    search_requests: int = 0
    search_errors: int = 0
    search_cost_usd: Decimal = Decimal(0)
    llm_model: str | None = None
    llm_calls: int = 0
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_cost_usd: Decimal | None = None


class DiscoveryThresholds(BaseModel):
    auto_approve_min_confidence: float
    auto_approve_min_relevance: float
    auto_approve_min_evidence_urls: int
    reject_max_relevance: float
    calibrated: bool = False  # starting values until evaluated on labelled runs


class DiscoveryRunResult(BaseModel):
    run_id: str
    mode: DiscoveryMode
    # Why LLM 1 was not used (LLM_DISABLED only), e.g. "OPENROUTER_API_KEY is not set".
    llm_disabled_reason: str | None = None
    started_at: datetime
    finished_at: datetime
    product_ids: list[str]
    search_provider: str
    known_competitors: list[CompetitorDomain]
    known_competitor_evidence: list[KnownCompetitorEvidence]
    global_queries: list[PlannedQuery]
    candidates: list[DiscoveredCompetitor]
    excluded: list[ExcludedDomain]
    # Domains eligible for targeted searches in this run: preconfigured,
    # reviewer-approved (persisted) and auto-approved (this run only).
    search_targets: list[SearchTarget] = Field(default_factory=list)
    targeted_queries: list[TargetedQuery]
    thresholds: DiscoveryThresholds
    usage: DiscoveryUsage
    warnings: list[str]
