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

from pydantic import BaseModel, Field

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
    rationale: str  # trimmed by code
    # Must be copied from the evidence provided; anything else is discarded.
    evidence_urls: list[str]


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
