"""Product intelligence API (Phase 5, LLM 2).

Searches approved competitor domains for pages about the selected Stompbox
products, retrieves them and (when LLM 2 is configured) extracts structured,
evidence-checked product fields. Runs synchronously; the result is returned,
not stored (temporary execution state).

Search targets = the 13 preconfigured competitors + reviewer-APPROVED domains
(Phase 4). There is no separate UK gate (retired with Option B, 19 Nov 2026): UK
relevance is verified evidence reviewed before approval. Pending and rejected
domains are never searched; rejected and excluded-type (directory) domains are listed as skipped.

Modes: LLM_ENABLED when OPENROUTER_API_KEY and LLM2_MODEL are set, otherwise
LLM_DISABLED (search + retrieval only). Only a missing search provider makes
the endpoint unavailable (503). No match decision or price comparison happens
here (Phase 6).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agents.discovery.schemas import SearchTarget
from app.agents.intelligence import (
    ProductIntelligenceAgent,
    build_intelligence_agent,
    eligible_search_targets,
    llm2_disabled_reason,
)
from app.agents.intelligence.schemas import IntelligenceMode, IntelligenceRunResult, SkippedTarget
from app.services.domains import registrable_domain
from app.services.search.http import ProviderNotConfiguredError

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])

MAX_PRODUCTS_PER_RUN = 5

AgentFactory = Callable[[list[SearchTarget], list[SkippedTarget]], Awaitable[ProductIntelligenceAgent]]


class IntelligenceRunRequest(BaseModel):
    product_ids: list[str] = Field(min_length=1, max_length=MAX_PRODUCTS_PER_RUN)
    # Optional subset of the eligible search targets. Omit to search all of them.
    domains: list[str] | None = Field(default=None, min_length=1, max_length=50)


class IntelligenceConfig(BaseModel):
    mode: IntelligenceMode
    llm_disabled_reason: str | None
    llm2_model: str | None
    search_provider: str | None
    search_targets: list[SearchTarget]
    skipped_targets: list[SkippedTarget]
    queries_per_domain: int
    max_pages_per_domain: int
    max_pages_per_product: int
    max_products_per_run: int = MAX_PRODUCTS_PER_RUN


def _targets(request: Request) -> tuple[list[SearchTarget], list[SkippedTarget]]:
    state = request.app.state
    return eligible_search_targets(state.catalogue.competitors, state.review_store.list_decisions(), state.settings)


@router.get("/config", response_model=IntelligenceConfig)
def intelligence_config(request: Request) -> IntelligenceConfig:
    """Mode and the domains a run would search. Never includes key values."""
    s = request.app.state.settings
    reason = llm2_disabled_reason(s)
    targets, skipped = _targets(request)
    return IntelligenceConfig(
        mode=IntelligenceMode.LLM_DISABLED if reason else IntelligenceMode.LLM_ENABLED,
        llm_disabled_reason=reason, llm2_model=None if reason else s.llm2_model,
        search_provider=s.search_provider, search_targets=targets, skipped_targets=skipped,
        queries_per_domain=s.intelligence_queries_per_domain,
        max_pages_per_domain=s.intelligence_max_pages_per_domain,
        max_pages_per_product=s.intelligence_max_pages_per_product,
    )  # fmt: skip


def get_agent_factory(request: Request) -> AgentFactory:
    """Overridable in tests. Default: the configured search provider + LLM 2."""
    return lambda targets, skipped: build_intelligence_agent(request.app.state.settings, targets, skipped)


@router.post("/runs", response_model=IntelligenceRunResult)
async def run_intelligence(
    body: IntelligenceRunRequest,
    request: Request,
    factory: Annotated[AgentFactory, Depends(get_agent_factory)],
) -> IntelligenceRunResult:
    products, missing = request.app.state.catalogue.lookup(body.product_ids)
    if missing:
        raise HTTPException(404, detail={"message": "Unknown product IDs", "missing_ids": missing})
    targets, skipped = _targets(request)
    if body.domains is not None:
        try:
            wanted = {registrable_domain(d) for d in body.domains}
        except ValueError:
            raise HTTPException(422, detail="Invalid domain in 'domains'") from None
        eligible = {t.domain for t in targets}
        not_allowed = sorted(wanted - eligible)
        if not_allowed:
            raise HTTPException(422, detail={
                "message": "Only approved search targets can be searched (preconfigured or reviewer-approved)",
                "not_allowed": not_allowed,
            })  # fmt: skip
        targets = [t for t in targets if t.domain in wanted]
    try:
        agent = await factory(targets, skipped)
    except ProviderNotConfiguredError as e:  # search provider missing (LLM absence is not an error)
        raise HTTPException(503, detail=f"Product intelligence is not configured: {e}") from None
    try:
        return await agent.run(products)
    finally:
        await agent.aclose()
