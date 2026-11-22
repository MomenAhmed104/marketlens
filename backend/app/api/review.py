"""Competitor domain review API (Phase 4).

Discovered domains enter the review queue when a discovery run finishes. A
reviewer approves or rejects each one; decisions persist (SQLite) and apply to
every later discovery run. The 13 preconfigured competitors are not reviewable
here: they stay exactly as configured in the catalogue workbook.

Rules (deterministic):
- Approve needs an explicit `company_type`; `null` means "reviewed, still
  unknown" and then the domain can never be a benchmark.
- `benchmark_eligible` is only allowed for retailer / specialist_retailer / manufacturer
  (BENCHMARK_ELIGIBLE_TYPES).
- Known marketplaces (amazon, ebay, ...) can only be approved as 'marketplace'.
- Approve/reject on an already-decided domain is a 409: use PUT to update.

Stage 8.1: a pending domain can also be searchable without a decision, when the
deterministic `rules.is_search_eligible` says so (recomputed from the stored record on
every request; nothing is written). A rejection always wins. Automatic search
eligibility never grants benchmark permission.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.discovery.rules import is_known_marketplace, is_search_eligible, policy_from_settings
from app.agents.discovery.schemas import (
    ClassificationStatus,
    CompetitorClass,
    DiscoveryMode,
    EvidenceItem,
    UkSeller,
)
from app.schemas.common import DomainApprovalStatus
from app.schemas.competitor import CompanyType
from app.schemas.review import (
    DomainDecisionRecord,
    ReviewDecision,
    ReviewStatus,
    benchmark_type_error,
)
from app.services.domains import registrable_domain
from app.services.review_store import (
    DecisionConflictError,
    DecisionNotFoundError,
    ReviewStore,
    StoredCandidate,
)

router = APIRouter(prefix="/api/review", tags=["review"])

DomainParam = Annotated[str, Path(min_length=3, max_length=253)]


# --------------------------------------------------------------------------- request bodies


class _DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)
    company_name: str | None = Field(default=None, max_length=200)

    @field_validator("reviewer", "notes", "company_name", mode="before")
    @classmethod
    def _strip(cls, v):
        return (" ".join(v.split()) or None) if isinstance(v, str) else v


class ApproveRequest(_DecisionBody):
    # Required, no default: the reviewer must state the type, or send null to
    # keep it explicitly unknown (then it can never be a benchmark).
    company_type: CompanyType | None
    benchmark_eligible: bool = False


class RejectRequest(_DecisionBody):
    company_type: CompanyType | None = None


class UpdateDecisionRequest(_DecisionBody):
    decision: ReviewDecision
    company_type: CompanyType | None
    benchmark_eligible: bool = False


# --------------------------------------------------------------------------- views


class SearchBasis(StrEnum):
    """Why a reviewed or pending domain is (not) searched for products."""

    MANUAL_APPROVAL = "MANUAL_APPROVAL"  # a reviewer approved it
    AUTOMATIC = "AUTOMATIC"  # pending, and automatically search-eligible (rules.is_search_eligible)
    NOT_SEARCHED = "NOT_SEARCHED"  # rejected, or pending and not eligible (or automatic search is off)


class CandidateSummary(BaseModel):
    domain: str
    homepage_url: str
    review_status: ReviewStatus
    decision: DomainDecisionRecord | None
    # Separate concepts: searchable at all vs. counts as a retailer price benchmark.
    # search_eligible: reviewer-approved, or automatically eligible while automatic search
    # is on (PIPELINE_SEARCH_AUTO_APPROVED). An automatic domain is still subject to the
    # per-run cap. benchmark_eligible: only a reviewer's explicit flag, never automatic.
    search_eligible: bool
    search_basis: SearchBasis
    auto_search_eligible: bool  # the rule's answer, whatever the decision or setting
    search_eligibility_reasons: list[str]
    benchmark_eligible: bool
    # What discovery found. Classification fields are null unless LLM 1 classified it.
    discovery_mode: DiscoveryMode
    classification_status: ClassificationStatus
    llm_classified: bool
    automatic_status: DomainApprovalStatus  # from the deterministic rules, before review
    proposed_company_name: str | None
    name_verified: bool
    proposed_company_type: CompanyType | None
    confidence: float | None
    relevance: float | None
    # LLM 1's informational answer (never a gate). Older records give their stored value.
    serves_uk_customers: UkSeller
    serves_uk_customers_note: str | None
    classification_version: int | None  # None: classified before Stage 8.1 (or not at all)
    # LLM 1: True new instruments, False only used (never searched automatically), None unknown.
    sells_new_musical_instruments: bool | None
    products_matched: list[str]
    known_marketplace: bool
    # Competitor classification (additive; separate from approval and benchmark eligibility).
    # None on records stored before classification existed.
    competitor_class: CompetitorClass | None = None
    proposed_competitor_class: CompetitorClass | None = None
    evidence_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    times_seen: int
    last_run_id: str


class CandidateDetail(CandidateSummary):
    evidence: list[EvidenceItem]
    cited_evidence_urls: list[str]
    relevant_categories: list[str]
    rationale: str | None
    automatic_reasons: list[str]
    validation_notes: list[str]
    competitor_class_reasons: list[str] = []
    classified_at: datetime | None = None
    classification_model: str | None = None
    history: list[DomainDecisionRecord]  # oldest first


# --------------------------------------------------------------------------- helpers


def _store(request: Request) -> ReviewStore:
    return request.app.state.review_store


def _normalise(domain: str) -> str:
    try:
        return registrable_domain(domain)
    except ValueError:
        raise HTTPException(422, detail=f"Invalid domain: {domain!r}") from None


def _known_domains(request: Request) -> set[str]:
    return {registrable_domain(c.domain) for c in request.app.state.catalogue.competitors}


def _status(d: DomainDecisionRecord | None) -> ReviewStatus:
    return ReviewStatus(d.decision.value) if d else ReviewStatus.PENDING


def _summary(s: StoredCandidate, d: DomainDecisionRecord | None, settings) -> dict:
    c = s.candidate
    labels = settings.marketplace_domain_labels
    approved = d is not None and d.decision is ReviewDecision.APPROVED
    rejected = d is not None and d.decision is ReviewDecision.REJECTED
    auto = is_search_eligible(c, d, policy_from_settings(settings))
    if approved:
        basis, reasons = SearchBasis.MANUAL_APPROVAL, [f"Approved by {d.reviewer}"]
    elif rejected:
        basis, reasons = SearchBasis.NOT_SEARCHED, [f"Rejected by {d.reviewer}: never searched"]
    elif auto.eligible and settings.pipeline_search_auto_approved:
        basis, reasons = SearchBasis.AUTOMATIC, [*auto.reasons, f"At most {settings.max_auto_search_targets} "
                                                 "automatic domain(s) are searched per run"]  # fmt: skip
    elif auto.eligible:
        basis, reasons = SearchBasis.NOT_SEARCHED, [*auto.reasons, "Automatic search is switched off "
                                                    "(PIPELINE_SEARCH_AUTO_APPROVED=false)"]  # fmt: skip
    else:
        basis, reasons = SearchBasis.NOT_SEARCHED, [f"Not searched until approved: {r}" for r in auto.reasons]
    return dict(
        domain=s.domain, homepage_url=c.homepage_url, review_status=_status(d), decision=d,
        search_eligible=basis is not SearchBasis.NOT_SEARCHED, search_basis=basis,
        auto_search_eligible=auto.eligible, search_eligibility_reasons=reasons,
        benchmark_eligible=approved and d.benchmark_eligible,
        discovery_mode=s.discovery_mode, classification_status=c.classification_status,
        llm_classified=c.classification_status is ClassificationStatus.COMPLETED,
        automatic_status=c.automatic_approval_status or c.approval_status,
        proposed_company_name=c.company_name, name_verified=c.name_verified,
        proposed_company_type=c.company_type, confidence=c.confidence, relevance=c.relevance,
        serves_uk_customers=c.serves_uk_customers, serves_uk_customers_note=c.serves_uk_customers_note,
        classification_version=c.classification_version, products_matched=c.products_matched,
        sells_new_musical_instruments=c.sells_new_musical_instruments,
        known_marketplace=is_known_marketplace(s.domain, labels), evidence_count=len(c.evidence),
        competitor_class=c.competitor_class, proposed_competitor_class=c.proposed_competitor_class,
        first_seen_at=s.first_seen_at, last_seen_at=s.last_seen_at, times_seen=s.times_seen,
        last_run_id=s.last_run_id,
    )  # fmt: skip


def _reviewable(request: Request, domain: str) -> tuple[str, StoredCandidate]:
    domain = _normalise(domain)
    if domain in _known_domains(request):
        raise HTTPException(409, detail=f"{domain} is a preconfigured competitor from the catalogue workbook "
                                        "and is not reviewed here")  # fmt: skip
    stored = _store(request).get_candidate(domain)
    if stored is None:
        raise HTTPException(404, detail=f"{domain} has not been discovered; run discovery first")
    return domain, stored


def _build_record(
    request: Request, domain: str, stored: StoredCandidate, decision: ReviewDecision, body: _DecisionBody,
    company_type: CompanyType | None, benchmark_eligible: bool,
) -> DomainDecisionRecord:
    if decision is ReviewDecision.APPROVED:
        labels = request.app.state.settings.marketplace_domain_labels
        if is_known_marketplace(domain, labels) and company_type is not CompanyType.MARKETPLACE:
            raise HTTPException(422, detail=f"{domain} is a known marketplace; approve it with company_type "
                                            "'marketplace' (it can never be a retailer benchmark)")  # fmt: skip
    if benchmark_eligible:
        if decision is ReviewDecision.REJECTED:
            raise HTTPException(422, detail="A rejected domain cannot be benchmark-eligible")
        if err := benchmark_type_error(company_type):
            raise HTTPException(422, detail=f"benchmark_eligible not allowed: {err}")
    return DomainDecisionRecord(
        domain=domain, decision=decision, company_name=body.company_name, company_type=company_type,
        benchmark_eligible=benchmark_eligible, reviewer=body.reviewer, decided_at=datetime.now(UTC),
        notes=body.notes, evidence_urls=[e.url for e in stored.candidate.evidence],
        source_run_id=stored.last_run_id,
    )  # fmt: skip


def _create(request: Request, record: DomainDecisionRecord) -> DomainDecisionRecord:
    try:
        return _store(request).create_decision(record)
    except DecisionConflictError:
        current = _store(request).get_decision(record.domain)
        raise HTTPException(409, detail={
            "message": f"{record.domain} already has a decision; use PUT /api/review/decisions/{record.domain} "
                       "to change it",
            "current_decision": current.decision.value if current else None,
        }) from None  # fmt: skip


# --------------------------------------------------------------------------- routes


@router.get("/candidates", response_model=list[CandidateSummary])
def list_candidates(request: Request, status_filter: Annotated[ReviewStatus | None, Query(alias="status")] = None):
    """Discovered domains with their review status. Filter with ?status=PENDING|APPROVED|REJECTED."""
    store = _store(request)
    decisions = {d.domain: d for d in store.list_decisions()}
    settings = request.app.state.settings
    out = [_summary(s, decisions.get(s.domain), settings) for s in store.list_candidates()]
    return [o for o in out if status_filter is None or o["review_status"] is status_filter]


@router.get("/candidates/{domain}", response_model=CandidateDetail)
def get_candidate(request: Request, domain: DomainParam):
    """One domain with its evidence, automatic decision reasons and decision history."""
    store = _store(request)
    domain = _normalise(domain)
    stored = store.get_candidate(domain)
    if stored is None:
        raise HTTPException(404, detail=f"{domain} is not in the review queue")
    c = stored.candidate
    return CandidateDetail(
        **_summary(stored, store.get_decision(domain), request.app.state.settings),
        evidence=c.evidence, cited_evidence_urls=c.cited_evidence_urls,
        relevant_categories=c.relevant_categories, rationale=c.rationale,
        automatic_reasons=[r for r in c.decision_reasons if not r.startswith("Reviewer decision")],
        validation_notes=c.validation_notes, history=store.history(domain),
        competitor_class_reasons=c.competitor_class_reasons, classified_at=c.classified_at,
        classification_model=c.classification_model,
    )  # fmt: skip


@router.post("/candidates/{domain}/approve", response_model=DomainDecisionRecord,
             status_code=status.HTTP_201_CREATED)  # fmt: skip
def approve(request: Request, domain: DomainParam, body: ApproveRequest):
    domain, stored = _reviewable(request, domain)
    return _create(request, _build_record(request, domain, stored, ReviewDecision.APPROVED, body,
                                          body.company_type, body.benchmark_eligible))  # fmt: skip


@router.post("/candidates/{domain}/reject", response_model=DomainDecisionRecord,
             status_code=status.HTTP_201_CREATED)  # fmt: skip
def reject(request: Request, domain: DomainParam, body: RejectRequest):
    domain, stored = _reviewable(request, domain)
    return _create(request, _build_record(request, domain, stored, ReviewDecision.REJECTED, body,
                                          body.company_type, False))  # fmt: skip


@router.put("/decisions/{domain}", response_model=DomainDecisionRecord)
def update_decision(request: Request, domain: DomainParam, body: UpdateDecisionRequest):
    """Explicitly change an existing decision. The previous version stays in the history."""
    domain, stored = _reviewable(request, domain)
    record = _build_record(request, domain, stored, body.decision, body, body.company_type, body.benchmark_eligible)
    try:
        return _store(request).update_decision(record)
    except DecisionNotFoundError:
        raise HTTPException(404, detail=f"{domain} has no decision yet; approve or reject it first") from None


@router.get("/decisions", response_model=list[DomainDecisionRecord])
def list_decisions(request: Request, decision: ReviewDecision | None = None):
    """Current decisions. Filter with ?decision=APPROVED|REJECTED."""
    return _store(request).list_decisions(decision)
