"""The UK-only eligibility gate: one deterministic place that decides, per domain,
whether operational product searches and price benchmarking may use it.

Rules:
- The evidence status is the reviewer's UK eligibility record, or
  INSUFFICIENT_EVIDENCE when there is none (configured and discovered alike).
- UK_ELIGIBLE: search and benchmark allowed (still subject to approval, type and
  each domain's own benchmark flag, checked elsewhere).
- INSUFFICIENT_EVIDENCE: blocked, unless an explicit user-approved exception
  allows search and/or benchmarking.
- NOT_UK_ELIGIBLE: blocked. Exceptions never apply.

Discovery's evidence-gathering searches (global discovery and per-candidate
profile searches) are not operational product searches and are not gated.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.schemas.uk_eligibility import (
    UkEligibilityRecord,
    UkEligibilityStatus,
    UkGateBasis,
    UkGateDecision,
    UkGateException,
)
from app.services.domains import registrable_domain

S = UkEligibilityStatus


@dataclass(frozen=True)
class UkGateState:
    """A consistent snapshot of the stored UK records and exceptions, keyed by registrable domain."""

    eligibility: Mapping[str, UkEligibilityRecord] = field(default_factory=dict)
    exceptions: Mapping[str, UkGateException] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> UkGateState:
        """No assessments: every domain is INSUFFICIENT_EVIDENCE and blocked."""
        return cls()

    @classmethod
    def of(cls, records: Iterable[UkEligibilityRecord] = (), exceptions: Iterable[UkGateException] = ()) -> UkGateState:
        return cls({r.domain: r for r in records}, {e.domain: e for e in exceptions})


def _key(domain: str) -> str:
    try:
        return registrable_domain(domain)
    except ValueError:
        return domain.strip().lower()


def gate_decision(domain: str, state: UkGateState) -> UkGateDecision:
    key = _key(domain)
    record = state.eligibility.get(key)
    status = record.status if record else S.INSUFFICIENT_EVIDENCE
    exc = state.exceptions.get(key)
    label = f"UK eligibility {status.value}" + ("" if record else " (not assessed)")
    common = dict(domain=key, evidence_status=status, assessed=record is not None)
    if status is S.UK_ELIGIBLE:
        return UkGateDecision(**common, search_allowed=True, benchmark_allowed=True,
                              search_basis=UkGateBasis.UK_ELIGIBLE, benchmark_basis=UkGateBasis.UK_ELIGIBLE)  # fmt: skip
    reasons = [label]
    if status is S.NOT_UK_ELIGIBLE:
        if exc is not None and exc.active:
            reasons.append("Exception ignored: exceptions never apply to NOT_UK_ELIGIBLE domains")
        return UkGateDecision(**common, search_allowed=False, benchmark_allowed=False, reasons=reasons,
                              search_basis=UkGateBasis.BLOCKED, benchmark_basis=UkGateBasis.BLOCKED)  # fmt: skip
    search = bool(exc and exc.allow_search)
    bench = bool(exc and exc.allow_search and exc.allow_benchmark)
    if search:
        reasons.append(f"Search allowed by explicit user-approved exception (v{exc.version})")
    else:
        reasons.append("Search blocked: no search exception")
    if bench:
        reasons.append(f"Benchmarking allowed by explicit user-approved exception (v{exc.version})")
    else:
        reasons.append("Benchmarking blocked: no benchmark exception")
    return UkGateDecision(
        **common, search_allowed=search, benchmark_allowed=bench, reasons=reasons,
        search_basis=UkGateBasis.EXCEPTION if search else UkGateBasis.BLOCKED,
        benchmark_basis=UkGateBasis.EXCEPTION if bench else UkGateBasis.BLOCKED,
        exception_version=exc.version if exc else None,
    )  # fmt: skip


def blocked_reason(d: UkGateDecision) -> str:
    """One UI-safe line for a domain the gate keeps out of product searches."""
    return f"UK gate: {d.reasons[0]}; not searched" if d.reasons else "UK gate: blocked; not searched"


def benchmark_block_reason(d: UkGateDecision | None, domain: str) -> str | None:
    """Why the UK gate keeps a domain out of price benchmarks; None if it does not."""
    if d is None:
        return f"{domain}: UK eligibility unknown (no UK gate decision), so it is not a UK price benchmark"
    if d.benchmark_allowed:
        return None
    return f"{domain}: {d.reasons[0] if d.reasons else 'UK gate blocked'}; not a UK price benchmark"
