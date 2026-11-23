"""LLM 2: product intelligence."""

from __future__ import annotations

from app.agents.discovery.rules import is_search_eligible, policy_from_settings
from app.agents.discovery.schemas import DiscoveredCompetitor, EvidenceKind, SearchTarget
from app.agents.intelligence.agent import ProductIntelligenceAgent
from app.agents.intelligence.schemas import SkippedTarget
from app.core.config import Settings
from app.schemas.common import DomainApprovalStatus
from app.schemas.competitor import CompetitorDomain
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.domains import registrable_domain
from app.services.images import build_image_finder
from app.services.llm import LLMClient, build_llm2
from app.services.search import get_configured_provider


def llm2_disabled_reason(settings: Settings) -> str | None:
    """None if LLM 2 can be used; otherwise a UI-safe reason (never a key value)."""
    if not settings.intelligence_use_llm:
        return "INTELLIGENCE_USE_LLM is false"
    key = settings.openrouter_api_key.get_secret_value().strip() if settings.openrouter_api_key else ""
    model = (settings.llm2_model or "").strip()
    missing = [name for name, ok in (("OPENROUTER_API_KEY", bool(key)), ("LLM2_MODEL", bool(model))) if not ok]
    return f"{' and '.join(missing)} not set" if missing else None


def eligible_search_targets(
    known: list[CompetitorDomain],
    decisions: list[DomainDecisionRecord],
    settings: Settings,
    candidates: list[DiscoveredCompetitor] | None = None,
    product_ids: list[str] | None = None,
    only_domains: set[str] | None = None,
) -> tuple[list[SearchTarget], list[SkippedTarget]]:
    """Domains product intelligence may search, in this order:

    1. the preconfigured competitors (on their configured path, with their workbook type
       and benchmark flag);
    2. reviewer-APPROVED domains (Phase 4), with the reviewer's benchmark flag;
    3. automatically search-eligible discovered domains (Stage 8.1): `candidates` (stored
       review records, plus this run's) that pass `rules.is_search_eligible` and have no
       human decision, while PIPELINE_SEARCH_AUTO_APPROVED is on. At most
       MAX_AUTO_SEARCH_TARGETS of them, ranked by `auto_target_rank`; the rest are listed
       as skipped with the reason. They never have benchmark permission.

    Pending-but-ineligible and rejected domains are never included; a rejection always wins,
    also over automatic eligibility. Approved domains of an excluded type (default:
    directory) are skipped with a reason. Decisions on preconfigured competitors are ignored.
    `only_domains` restricts the automatic domains before the cap (a caller's domain subset).
    Without `candidates` (e.g. the matching trust check) there are no automatic targets.
    """
    targets, skipped = _approved_targets(known, decisions, settings)
    if candidates and settings.pipeline_search_auto_approved:
        auto, over = _auto_targets(known, decisions, settings, candidates, product_ids, only_domains)
        targets += auto
        skipped += over
    return targets, skipped


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


def auto_target_rank(c: DiscoveredCompetitor, product_ids: list[str] | None = None) -> tuple:
    """Deterministic priority among automatically eligible domains (smaller sorts first).

    Grounded in discovery results only, never in LLM 1's relevance or confidence scores:
    selected products whose model was on a page of this domain, then selected products whose
    global discovery searches returned this domain, then all products seen, then distinct
    discovery pages, then the domain name."""
    selected = set(product_ids) if product_ids else None
    discovery = [e for e in c.evidence if e.kind is EvidenceKind.DISCOVERY_RESULT]
    matched = set(c.products_matched)
    searched = {e.product_id for e in discovery if e.product_id}
    if selected is not None:
        matched_sel, searched_sel = matched & selected, searched & selected
    else:
        matched_sel, searched_sel = matched, searched
    return (-len(matched_sel), -len(searched_sel), -len(matched), -len({e.canonical_url for e in discovery}),
            c.domain)  # fmt: skip


def _auto_targets(
    known: list[CompetitorDomain], decisions: list[DomainDecisionRecord], settings: Settings,
    candidates: list[DiscoveredCompetitor], product_ids: list[str] | None, only_domains: set[str] | None,
) -> tuple[list[SearchTarget], list[SkippedTarget]]:  # fmt: skip
    known_domains = {registrable_domain(c.domain) for c in known}
    decided = {d.domain for d in decisions}
    policy = policy_from_settings(settings)
    by_domain: dict[str, DiscoveredCompetitor] = {}
    for c in candidates:  # later entries (this run's) replace earlier ones (stored)
        by_domain[c.domain] = c
    eligible = [c for d, c in by_domain.items()
                if d not in known_domains and d not in decided  # a human decision always wins
                and (only_domains is None or d in only_domains)
                and is_search_eligible(c, None, policy).eligible]  # fmt: skip
    eligible.sort(key=lambda c: auto_target_rank(c, product_ids))
    cap = settings.max_auto_search_targets
    targets = [SearchTarget(domain=c.domain, company_name=c.company_name, company_type=c.company_type,
                            approval_status=DomainApprovalStatus.AUTO_APPROVED, price_benchmark=False)
               for c in eligible[:cap]]  # fmt: skip
    over = [SkippedTarget(domain=c.domain, company_type=c.company_type and c.company_type.value,
                          reason=f"Automatically searchable, but over the per-run cap of {cap} automatic domain(s) "
                                 f"(MAX_AUTO_SEARCH_TARGETS); ranked {i} of {len(eligible)}")
            for i, c in enumerate(eligible[cap:], start=cap + 1)]  # fmt: skip
    return targets, over


async def build_intelligence_agent(
    settings: Settings, targets: list[SearchTarget], skipped: list[SkippedTarget] | None
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
                                    images=build_image_finder(settings))  # fmt: skip


__all__ = ["ProductIntelligenceAgent", "build_intelligence_agent", "eligible_search_targets",
           "llm2_disabled_reason"]  # fmt: skip
