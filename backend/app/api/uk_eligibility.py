"""UK eligibility review API: reviewer assessments and explicit gate exceptions.

UK eligibility is separate from domain approval, competitor classification and
benchmark eligibility; nothing here reads or changes those. All rules live in the
models and the store (`ReviewStore.put_uk_eligibility` / `put_uk_exception`, one
transaction each); the routes only validate input and map errors to HTTP:

- Domains are canonicalised (`canonical_domain`): case and a leading 'www.' are
  normalised; URLs, other subdomains and invalid names are 422.
- Only known domains can be assessed or given an exception: the catalogue's
  configured competitors and domains discovered into the review queue. Any other
  (valid) domain is 404, like the review API's undiscovered domains. Reads are not
  restricted, so a stored record stays readable.
- Every write needs `reviewer`, a reason (`reasoning` / `reason`) and
  `expected_version` (0 = nothing stored yet). A stale version is 409.
- An exception can only be granted while the domain is INSUFFICIENT_EVIDENCE
  (unassessed counts as INSUFFICIENT_EVIDENCE); otherwise 409. A revocation
  (both scopes false) is always accepted.
- A status change revokes an active exception automatically, in the same
  transaction, so an old exception never comes back on its own.
- `gate` in every response is computed on the server from stored records; clients
  cannot submit gate decisions, permissions, benchmark flags or classifications
  (unknown fields are 422).
- History is append-only and read-only: there are no update or delete routes for it.

On a schema version 1 database, reads show every domain unassessed and blocked, and
writes are 503 until the explicit migration has been run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.schemas.uk_eligibility import (
    UkConfidence,
    UkEligibilityProposal,
    UkEligibilityRecord,
    UkEligibilityStatus,
    UkEvidence,
    UkGateDecision,
    UkGateException,
)
from app.services.domains import canonical_domain, registrable_domain
from app.services.review_store import (
    ExceptionNotAllowedError,
    MigrationRequiredError,
    ReviewStore,
    VersionConflictError,
)
from app.services.uk_evidence import saved_evidence_problems
from app.services.uk_gate import UkGateState, gate_decision

router = APIRouter(prefix="/api/review/uk-eligibility", tags=["uk-eligibility"])

DomainParam = Annotated[str, Path(min_length=3, max_length=253)]


# --------------------------------------------------------------------------- request bodies


def _clean(v):
    return (" ".join(v.split()) or None) if isinstance(v, str) else v


class _WriteBody(BaseModel):
    # Unknown fields (price_benchmark, competitor_class, search_allowed, version, decided_at, ...) are 422.
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=4000)
    # The version the reviewer saw: 0 when nothing is stored yet. Required, so no write is blind.
    expected_version: int = Field(ge=0)

    @field_validator("reviewer", "notes", mode="before")
    @classmethod
    def _strip(cls, v):
        return _clean(v)


class EligibilityRequest(_WriteBody):
    status: UkEligibilityStatus
    confidence: UkConfidence
    reasoning: str = Field(min_length=1, max_length=4000)
    evidence: list[UkEvidence] = Field(default_factory=list, max_length=50)
    review_flags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("reasoning", mode="before")
    @classmethod
    def _strip_reasoning(cls, v):
        return _clean(v)


class ExceptionRequest(_WriteBody):
    # Both scopes are stated explicitly every time; benchmarking also needs search.
    allow_search: bool
    allow_benchmark: bool
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, v):
        return _clean(v)


# --------------------------------------------------------------------------- views


class UkDomainView(BaseModel):
    domain: str
    configured_competitor: bool  # one of the catalogue workbook's competitors
    in_review_queue: bool  # discovered and saved for review
    assessable: bool  # configured or discovered: writes are accepted (otherwise 404)
    eligibility: UkEligibilityRecord | None  # None = unassessed (INSUFFICIENT_EVIDENCE)
    exception: UkGateException | None  # current version; may be a revocation
    gate: UkGateDecision  # computed on the server from the stored records; read only


class UkDomainDetail(UkDomainView):
    uk_supported: bool
    eligibility_history: list[UkEligibilityRecord]  # oldest first, append-only
    exception_history: list[UkGateException]  # oldest first, append-only
    proposal: UkEligibilityProposal | None  # an audit suggestion; never used by the gate


class UkDomainList(BaseModel):
    uk_supported: bool  # False: schema version 1, every domain unassessed and blocked, writes are 503
    schema_version: int
    domains: list[UkDomainView]


# --------------------------------------------------------------------------- helpers


def _store(request: Request) -> ReviewStore:
    return request.app.state.review_store


def _domain(raw: str) -> str:
    try:
        return canonical_domain(raw)
    except ValueError as e:
        raise HTTPException(422, detail={"error": "INVALID_DOMAIN", "message": str(e)}) from None


def _key(domain: str) -> str:
    """The canonical key of a stored or configured domain; stored keys are already canonical."""
    try:
        return canonical_domain(domain)
    except ValueError:
        return registrable_domain(domain)


def _configured(request: Request) -> set[str]:
    return {_key(c.domain) for c in request.app.state.catalogue.competitors}


def _queued(request: Request) -> set[str]:
    return {_key(c.domain) for c in _store(request).list_candidates()}


def _view(domain: str, state: UkGateState, configured: set[str], queued: set[str]) -> dict:
    return dict(domain=domain, configured_competitor=domain in configured, in_review_queue=domain in queued,
                assessable=domain in configured or domain in queued,
                eligibility=state.eligibility.get(domain), exception=state.exceptions.get(domain),
                gate=gate_decision(domain, state))  # fmt: skip


def _require_known(request: Request, domain: str) -> None:
    """Writes only for configured competitors and discovered domains (canonical match)."""
    if domain in _configured(request) or _store(request).get_candidate(domain) is not None:
        return
    raise HTTPException(404, detail={"error": "UNKNOWN_DOMAIN", "message": f"{domain} is neither a configured "
                                     "competitor nor a discovered domain; run discovery first"})  # fmt: skip


def _detail(request: Request, domain: str) -> UkDomainDetail:
    store = _store(request)
    view = _view(domain, store.uk_gate_state(), _configured(request),
                 {domain} if store.get_candidate(domain) is not None else set())  # fmt: skip
    return UkDomainDetail(**view, uk_supported=store.uk_supported,
                          eligibility_history=store.uk_eligibility_history(domain),
                          exception_history=store.uk_exception_history(domain),
                          proposal=store.get_uk_proposal(domain))  # fmt: skip


def _build(model, **values):
    """Validate the stored model (status/evidence rules, canonical domain) and map failures to 422."""
    try:
        return model(**values)
    except ValidationError as e:
        raise HTTPException(422, detail={"error": "INVALID_RECORD",
                                         "problems": [err["msg"] for err in e.errors()]}) from None  # fmt: skip


def _write(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except MigrationRequiredError as e:
        raise HTTPException(503, detail={"error": "MIGRATION_REQUIRED", "message": str(e)}) from None
    except VersionConflictError as e:
        raise HTTPException(409, detail={"error": "VERSION_CONFLICT", "current_version": e.current or 0,
                                         "message": "The record changed since it was read; reload and retry"}) from None
    except ExceptionNotAllowedError as e:
        raise HTTPException(409, detail={"error": "EXCEPTION_NOT_ALLOWED", "evidence_status": e.status.value,
                                         "message": str(e)}) from None  # fmt: skip


# --------------------------------------------------------------------------- routes


@router.get("", response_model=UkDomainList)
def list_uk_eligibility(request: Request) -> UkDomainList:
    """Every configured competitor and every discovered domain, assessed or not (unassessed ones show
    INSUFFICIENT_EVIDENCE), one entry per canonical domain, with stored assessments and exceptions
    merged in. A stored record for any other domain is listed too, with `assessable` false."""
    store = _store(request)
    state = store.uk_gate_state()
    configured, queued = _configured(request), _queued(request)
    domains = sorted(configured | queued | set(state.eligibility) | set(state.exceptions))
    return UkDomainList(uk_supported=store.uk_supported, schema_version=store.schema_version,
                        domains=[UkDomainView(**_view(d, state, configured, queued)) for d in domains])  # fmt: skip


@router.get("/{domain}", response_model=UkDomainDetail)
def get_uk_eligibility(request: Request, domain: DomainParam) -> UkDomainDetail:
    """One domain: current assessment, exception, computed gate result, both histories and any proposal.
    An unassessed domain is returned as INSUFFICIENT_EVIDENCE (not 404)."""
    return _detail(request, _domain(domain))


@router.put("/{domain}", response_model=UkDomainDetail)
def put_uk_eligibility(request: Request, domain: DomainParam, body: EligibilityRequest) -> UkDomainDetail:
    """Create (expected_version 0) or update a domain's assessment. A status change revokes an active exception."""
    domain = _domain(domain)
    _require_known(request, domain)
    store = _store(request)
    if problems := saved_evidence_problems(body.evidence, store.get_candidate(domain)):
        raise HTTPException(422, detail={"error": "UNVERIFIED_EVIDENCE", "problems": problems})
    record = _build(UkEligibilityRecord, domain=domain, status=body.status, confidence=body.confidence,
                    reasoning=body.reasoning, evidence=body.evidence, review_flags=body.review_flags,
                    reviewer=body.reviewer, decided_at=datetime.now(UTC), notes=body.notes)  # fmt: skip
    _write(store.put_uk_eligibility, record, expected_version=body.expected_version)
    return _detail(request, domain)


@router.put("/{domain}/exception", response_model=UkDomainDetail)
def put_uk_exception(request: Request, domain: DomainParam, body: ExceptionRequest) -> UkDomainDetail:
    """Grant, narrow or revoke an exception. Each scope is stated explicitly; both false revokes it."""
    domain = _domain(domain)
    _require_known(request, domain)
    record = _build(UkGateException, domain=domain, allow_search=body.allow_search,
                    allow_benchmark=body.allow_benchmark, reason=body.reason, reviewer=body.reviewer,
                    decided_at=datetime.now(UTC), notes=body.notes)  # fmt: skip
    _write(_store(request).put_uk_exception, record, expected_version=body.expected_version)
    return _detail(request, domain)
