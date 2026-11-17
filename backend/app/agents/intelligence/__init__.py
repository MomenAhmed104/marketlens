"""LLM 2: product intelligence."""

from __future__ import annotations

from app.agents.discovery.schemas import SearchTarget
from app.agents.intelligence.agent import ProductIntelligenceAgent
from app.agents.intelligence.schemas import SkippedTarget
from app.core.config import Settings
from app.schemas.competitor import CompetitorDomain
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.domains import registrable_domain
from app.services.llm import LLMClient, build_llm2
from app.services.search import get_configured_provider
from app.services.uk_gate import UkGateState, blocked_reason, gate_decision


def llm2_disabled_reason(settings: Settings) -> str | None:
    """None if LLM 2 can be used; otherwise a UI-safe reason (never a key value)."""
    if not settings.intelligence_use_llm:
        return "INTELLIGENCE_USE_LLM is false"
    key = settings.openrouter_api_key.get_secret_value().strip() if settings.openrouter_api_key else ""
    model = (settings.llm2_model or "").strip()
    missing = [name for name, ok in (("OPENROUTER_API_KEY", bool(key)), ("LLM2_MODEL", bool(model))) if not ok]
    return f"{' and '.join(missing)} not set" if missing else None


def apply_uk_gate(targets: list[SearchTarget], uk_state: UkGateState) -> tuple[list[SearchTarget], list[SkippedTarget]]:
    """The UK gate for operational product searches. Every target gets its effective
    `uk_gate` decision attached; only those whose search is allowed (UK_ELIGIBLE, or an
    explicit user-approved search exception on an INSUFFICIENT_EVIDENCE domain) are kept.
    Blocked targets are returned as skipped, with the UK reason. Benchmark flags are
    never changed here; `compare()` needs both the flag and `uk_gate.benchmark_allowed`."""
    allowed: list[SearchTarget] = []
    skipped: list[SkippedTarget] = []
    for t in targets:
        d = gate_decision(t.domain, uk_state)
        if d.search_allowed:
            allowed.append(t.model_copy(update={"uk_gate": d}))
        else:
            skipped.append(SkippedTarget(domain=t.domain, company_type=t.company_type and t.company_type.value,
                                         reason=blocked_reason(d), uk_gate=d))  # fmt: skip
    return allowed, skipped


def eligible_search_targets(
    known: list[CompetitorDomain], decisions: list[DomainDecisionRecord], uk_state: UkGateState, settings: Settings
) -> tuple[list[SearchTarget], list[SkippedTarget]]:
    """Domains product intelligence may search: the preconfigured competitors plus
    reviewer-APPROVED domains (Phase 4), then the UK gate (`apply_uk_gate`). Pending
    and rejected domains are never included. Approved domains of an excluded type
    (default: directory) are skipped with a reason. Decisions on preconfigured
    competitors are ignored. Benchmark eligibility is carried on each target, never
    widened. `uk_state` is required: an empty state blocks every domain.
    """
    targets, skipped = _approved_targets(known, decisions, settings)
    allowed, blocked = apply_uk_gate(targets, uk_state)
    return allowed, skipped + blocked


def _approved_targets(
    known: list[CompetitorDomain], decisions: list[DomainDecisionRecord], settings: Settings
) -> tuple[list[SearchTarget], list[SkippedTarget]]:
    known_domains = {registrable_domain(c.domain) for c in known}
    targets = [SearchTarget.from_known(c) for c in known]
    skipped: list[SkippedTarget] = []
    excluded_types = set(settings.intelligence_excluded_company_types)
    for d in sorted(decisions, key=lambda d: d.domain):
        if d.domain in known_domains:
            continue
        if d.decision is not ReviewDecision.APPROVED:
            skipped.append(SkippedTarget(domain=d.domain, company_type=d.company_type and d.company_type.value,
                                         reason="Rejected in review"))  # fmt: skip
            continue
        if d.company_type is not None and d.company_type.value in excluded_types:
            skipped.append(SkippedTarget(domain=d.domain, company_type=d.company_type.value,
                                         reason=f"Approved, but type '{d.company_type.value}' is not searched for "
                                                "offers (lists other sellers)"))  # fmt: skip
            continue
        targets.append(SearchTarget.from_decision(d))
    return targets, skipped


async def build_intelligence_agent(
    settings: Settings, targets: list[SearchTarget], skipped: list[SkippedTarget] | None, uk_state: UkGateState
) -> ProductIntelligenceAgent:
    """Agent wired to the configured search provider and, when configured, LLM 2.

    Missing LLM configuration selects LLM_DISABLED mode; it is never an error.
    A missing search provider or key raises ProviderNotConfiguredError.
    Callers must `await agent.aclose()`.
    """
    search = get_configured_provider(settings)
    reason = llm2_disabled_reason(settings)
    llm: LLMClient | None = None
    if reason is None:
        try:
            llm = build_llm2(settings)
        except Exception:
            await search.aclose()
            raise
    return ProductIntelligenceAgent(search, llm, settings, targets, skipped, llm_disabled_reason=reason,
                                    uk_state=uk_state)  # fmt: skip


__all__ = ["ProductIntelligenceAgent", "apply_uk_gate", "build_intelligence_agent", "eligible_search_targets",
           "llm2_disabled_reason"]  # fmt: skip
