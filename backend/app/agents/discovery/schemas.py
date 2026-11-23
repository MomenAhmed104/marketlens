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
from pydantic import AliasChoices, BaseModel, Field

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


# Version 1 (implicit, stored as null): every classification before Stage 8.1.
# Version 2 (Stage 8.1, 22 Nov 2026): the UK-focused prompt without UK evidence quotes.
# Version 3 (Stage 8.1 refinement): `sells_new_musical_instruments` becomes yes/no/unknown
# (true / false = used only / null), so a version-2 false ("not shown") is never read as
# "used only". No version-2 record was produced by a real run.
CLASSIFICATION_VERSION = 3


class CompetitorClass(StrEnum):
    """Competitor classification. Separate from approval and price-benchmark eligibility."""

    DIRECT_COMPETITOR = "DIRECT_COMPETITOR"  # retailer serving UK customers, meaningful product overlap
    RELATED_RETAILER = "RELATED_RETAILER"  # retailer of adjacent music products, or a marketplace
    NEEDS_REVIEW = "NEEDS_REVIEW"  # manufacturer, directory, other, or insufficient/uncertain evidence


class CompanyClassification(BaseModel):
    domain: str
    # Exactly as written in the evidence; null if the evidence does not name it.
    company_name: str | None
    company_type: CompanyType
    relevant_categories: list[str]
    # Informational only (Stage 8.1, user decision 22 Nov 2026): LLM 1's own reading of whether
    # the business sells to UK customers, with a short explanation. Python never verifies it
    # and it never affects eligibility, approval, exclusion or class. `uk_seller` is the name
    # used before Stage 8.1 (old fixtures and backfill reports).
    serves_uk_customers: UkSeller = Field(
        default=UkSeller.UNKNOWN, validation_alias=AliasChoices("serves_uk_customers", "uk_seller"))
    serves_uk_customers_note: str | None = None
    # True: sells new instruments/equipment (with or without used ones). False: the evidence
    # shows it sells only used/second-hand instruments. None: the evidence does not say.
    # Only False affects automatic search eligibility (`rules.is_search_eligible`).
    sells_new_musical_instruments: bool | None
    relevance: float = Field(ge=0, le=1)  # informational only (never a gate since Stage 8.1)
    confidence: float = Field(ge=0, le=1)  # informational only (never a gate since Stage 8.1)
    rationale: str  # trimmed by code; also explains competitor_class
    # Must be copied from the evidence provided; anything else is discarded.
    evidence_urls: list[str]
    # LLM 1's proposal; code safeguards (rules.classify_competitor) decide the final class.
    # The strict output schema makes the field required; the default only serves old fixtures.
    competitor_class: CompetitorClass = CompetitorClass.NEEDS_REVIEW


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
    classification_status: ClassificationStatus = ClassificationStatus.MISSING
    # LLM 1 proposal. All null/unknown unless classification_status is COMPLETED:
    # nothing here is ever filled in without the LLM's evidence-based answer.
    company_name: str | None = None
    company_type: CompanyType | None = None
    relevant_categories: list[str] = Field(default_factory=list)
    # Informational only (never a gate): LLM 1's answer and short explanation. Records stored
    # before Stage 8.1 carry it as `uk_seller` (then reconciled with the retired UK evidence).
    serves_uk_customers: UkSeller = Field(
        default=UkSeller.UNKNOWN, validation_alias=AliasChoices("serves_uk_customers", "uk_seller"))
    serves_uk_customers_note: str | None = None
    sells_new_musical_instruments: bool | None = None  # False = used only (see CompanyClassification)
    relevance: float | None = None  # informational only
    confidence: float | None = None  # informational only
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
    # Version of the LLM 1 classification contract that produced the classification fields
    # (CLASSIFICATION_VERSION). None = classified before Stage 8.1 (or never). Only a
    # classification at the current version can make a domain automatically searchable, so
    # older pending records must be rediscovered first (user decision, 22 Nov 2026).
    classification_version: int | None = None
    # Deterministic checks and decision
    name_verified: bool = False
    validation_notes: list[str] = Field(default_factory=list)
    # Same company name as another discovered domain or a configured competitor (set by Python
    # after classification; any entry sends the domain to human review). Empty in older records.
    identity_conflicts: list[str] = Field(default_factory=list)
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
    # PRECONFIGURED, APPROVED (reviewer) or AUTO_APPROVED (automatically search-eligible:
    # `rules.is_search_eligible`, recomputed from the stored record on every run).
    approval_status: DomainApprovalStatus
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
    # Domains eligible for targeted searches in this run: preconfigured, reviewer-approved
    # and this run's automatically search-eligible candidates.
    search_targets: list[SearchTarget] = Field(default_factory=list)
    targeted_queries: list[TargetedQuery]
    usage: DiscoveryUsage
    warnings: list[str]
