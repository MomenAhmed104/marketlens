"""Competitor domain review (Phase 4): persistent human decisions.

Discovery runs stay temporary. A reviewer's decision on a discovered domain is
stored separately and applies to every later run.

Approval and benchmark eligibility are separate:
- `decision` says whether the domain may be searched at all (APPROVED) or
  never (REJECTED).
- `benchmark_eligible` says whether its prices may count as a direct retailer
  price benchmark. Only retailers can be eligible; enforced here on every
  record, whatever the request says.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.competitor import BENCHMARK_ELIGIBLE_TYPES, CompanyType


class ReviewStatus(StrEnum):
    PENDING = "PENDING"  # discovered, no human decision yet
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReviewDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


def benchmark_type_error(company_type: CompanyType | None) -> str | None:
    """Why this type can't be a price benchmark; None if it can (BENCHMARK_ELIGIBLE_TYPES)."""
    if company_type is None:
        return "company type is unknown; set a reviewed retailer or manufacturer type to enable benchmarking"
    if company_type not in BENCHMARK_ELIGIBLE_TYPES:
        return (f"company type '{company_type.value}' is not a direct retailer or manufacturer and can never "
                "be a price benchmark")  # fmt: skip
    return None


class DomainDecisionRecord(BaseModel):
    """One version of a reviewer's decision on a domain."""

    model_config = ConfigDict(frozen=True)

    domain: str  # registrable domain, the unique key
    decision: ReviewDecision
    company_name: str | None = None  # as entered/confirmed by the reviewer; null = unknown
    company_type: CompanyType | None = None  # reviewed type; null = explicitly kept unknown
    benchmark_eligible: bool = False
    reviewer: str = Field(min_length=1, max_length=100)
    decided_at: datetime
    notes: str | None = None
    # Traceability: the evidence the reviewer was shown when deciding.
    evidence_urls: list[str] = Field(default_factory=list)
    source_run_id: str | None = None  # discovery run that last supplied the evidence
    version: int = Field(default=1, ge=1)  # increments on every update

    @model_validator(mode="after")
    def _benchmark_rules(self) -> DomainDecisionRecord:
        if self.benchmark_eligible:
            if self.decision is ReviewDecision.REJECTED:
                raise ValueError("a rejected domain cannot be benchmark-eligible")
            if err := benchmark_type_error(self.company_type):
                raise ValueError(f"benchmark_eligible not allowed: {err}")
        return self
