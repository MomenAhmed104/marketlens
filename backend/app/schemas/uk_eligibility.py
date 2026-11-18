"""UK eligibility: is a domain operated by a UK business? (separate from everything else)

UK eligibility is independent of competitor classification, review approval and
benchmark eligibility. It answers one question with evidence: does the business
behind the domain have a real UK presence?

- Strong evidence: a physical UK store, a UK registered company with retail
  operations, a UK operating address, or another documented UK retail operation.
- Weak signals (GBP prices, UK shipping, a .co.uk domain, UK customers) never
  make a domain UK_ELIGIBLE on their own.

Records here are reviewer decisions (versioned, with history). Separately:
- `UkGateException`: an explicit, user-approved permission to search and/or
  benchmark a domain whose evidence is still INSUFFICIENT_EVIDENCE. It never
  changes the evidence status.
- `UkEligibilityProposal`: what an audit suggested. Shown in the UI, never used
  by the gate.
- `UkGateDecision`: the computed, effective result for one domain (never stored).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.domains import canonical_domain


class UkEligibilityStatus(StrEnum):
    UK_ELIGIBLE = "UK_ELIGIBLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"  # also the default for every unassessed domain
    NOT_UK_ELIGIBLE = "NOT_UK_ELIGIBLE"


class UkConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class UkEvidenceKind(StrEnum):
    PHYSICAL_STORE = "PHYSICAL_STORE"
    REGISTERED_COMPANY = "REGISTERED_COMPANY"  # UK registered company with retail operations
    UK_OPERATING_ADDRESS = "UK_OPERATING_ADDRESS"
    UK_RETAIL_OPERATION = "UK_RETAIL_OPERATION"
    WEAK_SIGNAL = "WEAK_SIGNAL"  # GBP prices, UK shipping, .co.uk, UK customers: never enough alone
    NON_UK_OPERATOR = "NON_UK_OPERATOR"  # evidence the operator is outside the UK
    OTHER = "OTHER"


STRONG_UK_EVIDENCE = frozenset({UkEvidenceKind.PHYSICAL_STORE, UkEvidenceKind.REGISTERED_COMPANY,
                                UkEvidenceKind.UK_OPERATING_ADDRESS, UkEvidenceKind.UK_RETAIL_OPERATION})  # fmt: skip


class UkEvidenceSource(StrEnum):
    SAVED_EVIDENCE = "SAVED_EVIDENCE"  # a stored discovery evidence item; the quote is checked against it
    REVIEWER_SUPPLIED = "REVIEWER_SUPPLIED"  # a page the reviewer read (e.g. Companies House); needs a note


def _clean(v):
    return (" ".join(v.split()) or None) if isinstance(v, str) else v


class _DomainKeyed(BaseModel):
    """`domain` is always the canonical registrable domain (`canonical_domain`): the same key
    for records, exceptions and proposals, in the database and in the gate. Ambiguous or
    invalid values are rejected, never stored under a key the gate would not look up."""

    domain: str

    @field_validator("domain")
    @classmethod
    def _canonical(cls, v: str) -> str:
        return canonical_domain(v)


class UkEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(min_length=8, max_length=2000)
    quote: str = Field(min_length=1, max_length=1000)  # verbatim text from the page
    kind: UkEvidenceKind
    source: UkEvidenceSource
    note: str | None = Field(default=None, max_length=1000)  # how/when it was read

    @field_validator("quote", "note", mode="before")
    @classmethod
    def _strip(cls, v):
        return _clean(v)

    @field_validator("url")
    @classmethod
    def _http(cls, v: str) -> str:
        v = v.strip()
        if not v.lower().startswith(("https://", "http://")):
            raise ValueError("evidence url must be an http(s) URL")
        return v

    @model_validator(mode="after")
    def _reviewer_note(self) -> UkEvidence:
        if self.source is UkEvidenceSource.REVIEWER_SUPPLIED and not self.note:
            raise ValueError("reviewer-supplied evidence needs a note saying how and when it was read")
        return self


class UkEligibilityRecord(_DomainKeyed):
    """One version of a reviewer's UK eligibility decision on a domain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str  # canonical registrable domain, configured or discovered
    status: UkEligibilityStatus
    confidence: UkConfidence
    reasoning: str = Field(min_length=1, max_length=4000)
    evidence: list[UkEvidence] = Field(default_factory=list, max_length=50)
    # Open questions the reviewer wants kept visible (e.g. a domain redirect); informational only.
    review_flags: list[str] = Field(default_factory=list, max_length=20)
    reviewer: str = Field(min_length=1, max_length=100)
    decided_at: datetime
    notes: str | None = Field(default=None, max_length=4000)
    version: int = Field(default=1, ge=1)

    @field_validator("reasoning", "reviewer", "notes", mode="before")
    @classmethod
    def _strip(cls, v):
        return _clean(v)

    @model_validator(mode="after")
    def _evidence_rules(self) -> UkEligibilityRecord:
        if self.status is UkEligibilityStatus.UK_ELIGIBLE and not any(e.kind in STRONG_UK_EVIDENCE
                                                                      for e in self.evidence):  # fmt: skip
            raise ValueError("UK_ELIGIBLE needs at least one strong evidence item (physical store, registered "
                             "company, UK operating address or UK retail operation); weak signals are not enough")
        if self.status is UkEligibilityStatus.NOT_UK_ELIGIBLE and not self.evidence:
            raise ValueError("NOT_UK_ELIGIBLE needs at least one evidence item")
        return self


EXPLICIT_USER_APPROVAL = "EXPLICIT_USER_APPROVAL"


class UkGateException(_DomainKeyed):
    """One version of an explicit, user-approved exception to the UK gate.

    It can only be granted while the domain's evidence status is INSUFFICIENT_EVIDENCE
    (the store refuses it otherwise) and never changes that status. Search and
    benchmarking are controlled separately; benchmarking also still needs the
    domain's own benchmark flag (so a reference-only manufacturer stays blocked).
    Revoke by saving a version with both scopes false. When the domain's evidence
    status changes, the store revokes an active exception automatically
    (`revoked_by_status_change`), so an old exception never comes back on its own.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str
    allow_search: bool
    allow_benchmark: bool
    reason: str = Field(min_length=1, max_length=2000)
    approval: Literal["EXPLICIT_USER_APPROVAL"] = EXPLICIT_USER_APPROVAL
    reviewer: str = Field(min_length=1, max_length=100)
    decided_at: datetime
    notes: str | None = Field(default=None, max_length=4000)
    version: int = Field(default=1, ge=1)
    # True only on the revocation the store writes when the domain's UK evidence status changed.
    revoked_by_status_change: bool = False

    @field_validator("reason", "reviewer", "notes", mode="before")
    @classmethod
    def _strip(cls, v):
        return _clean(v)

    @model_validator(mode="after")
    def _scopes(self) -> UkGateException:
        if self.allow_benchmark and not self.allow_search:
            raise ValueError("a benchmark exception needs a search exception: unsearched domains have no prices")
        if self.revoked_by_status_change and self.active:
            raise ValueError("an automatic revocation must allow neither search nor benchmarking")
        return self

    @property
    def active(self) -> bool:
        return self.allow_search or self.allow_benchmark


class UkProposalSource(StrEnum):
    AUDIT = "AUDIT"
    LLM1 = "LLM1"


class UkEligibilityProposal(_DomainKeyed):
    """A suggested status from an audit. Never used by the gate; a reviewer must decide."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str
    status: UkEligibilityStatus
    confidence: UkConfidence
    reasons: list[str] = Field(default_factory=list, max_length=20)
    evidence_urls: list[str] = Field(default_factory=list, max_length=50)
    review_flags: list[str] = Field(default_factory=list, max_length=20)
    source: UkProposalSource
    source_ref: str | None = Field(default=None, max_length=500)  # e.g. the audit report it came from
    proposed_at: datetime


class UkGateBasis(StrEnum):
    UK_ELIGIBLE = "UK_ELIGIBLE"  # allowed on evidence
    EXCEPTION = "EXCEPTION"  # allowed only by an explicit user-approved exception
    BLOCKED = "BLOCKED"


class UkGateDecision(BaseModel):
    """The effective UK gate result for one domain (computed, never stored)."""

    model_config = ConfigDict(frozen=True)

    domain: str
    evidence_status: UkEligibilityStatus  # INSUFFICIENT_EVIDENCE when unassessed
    assessed: bool  # a reviewer record exists
    search_allowed: bool
    benchmark_allowed: bool  # the UK part only; the domain's own benchmark flag is checked separately
    search_basis: UkGateBasis
    benchmark_basis: UkGateBasis
    exception_version: int | None = None
    reasons: list[str] = Field(default_factory=list)  # why anything is blocked or allowed by exception
