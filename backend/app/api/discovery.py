"""Competitor discovery API (Phase 3).

Runs synchronously and returns the result. The run itself is not persisted
(temporary execution state); its discovered candidates and their evidence are
added to the review queue (Phase 4, see app/api/review.py), and persisted
reviewer decisions are applied to the run.

Works in two modes: LLM_ENABLED when OPENROUTER_API_KEY and LLM1_MODEL are set,
otherwise LLM_DISABLED (baseline queries, no classification). Only a missing
search provider makes discovery unavailable (503).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agents.discovery import CompetitorDiscoveryAgent, build_discovery_agent, llm1_disabled_reason
from app.agents.discovery.schemas import DiscoveryMode, DiscoveryRunResult, DiscoveryThresholds
from app.services.search.http import ProviderError, ProviderNotConfiguredError

router = APIRouter(prefix="/api/discovery", tags=["discovery"])

MAX_PRODUCTS_PER_RUN = 10

AgentFactory = Callable[[], Awaitable[CompetitorDiscoveryAgent]]


class DiscoveryRunRequest(BaseModel):
    product_ids: list[str] = Field(min_length=1, max_length=MAX_PRODUCTS_PER_RUN)


class DiscoveryConfig(BaseModel):
    mode: DiscoveryMode
    llm_disabled_reason: str | None
    llm1_model: str | None
    search_provider: str | None
    thresholds: DiscoveryThresholds


@router.get("/config", response_model=DiscoveryConfig)
def discovery_config(request: Request) -> DiscoveryConfig:
    """Which mode a run would use. Never includes key values."""
    s = request.app.state.settings
    reason = llm1_disabled_reason(s)
    return DiscoveryConfig(
        mode=DiscoveryMode.LLM_DISABLED if reason else DiscoveryMode.LLM_ENABLED,
        llm_disabled_reason=reason, llm1_model=None if reason else s.llm1_model,
        search_provider=s.search_provider,
        thresholds=DiscoveryThresholds(
            auto_approve_min_confidence=s.discovery_auto_approve_min_confidence,
            auto_approve_min_relevance=s.discovery_auto_approve_min_relevance,
            auto_approve_min_evidence_urls=s.discovery_auto_approve_min_evidence_urls,
            reject_max_relevance=s.discovery_reject_max_relevance,
        ),
    )  # fmt: skip


def get_agent_factory(request: Request) -> AgentFactory:
    """Overridable in tests. Default: the configured search provider + LLM 1,
    with the persisted reviewer decisions applied."""
    state = request.app.state
    return lambda: build_discovery_agent(state.settings, state.catalogue,
                                         review_decisions=state.review_store.list_decisions(),
                                         uk_state=state.review_store.uk_gate_state())  # fmt: skip


@router.post("/runs", response_model=DiscoveryRunResult)
async def run_discovery(
    body: DiscoveryRunRequest,
    request: Request,
    factory: Annotated[AgentFactory, Depends(get_agent_factory)],
) -> DiscoveryRunResult:
    catalogue = request.app.state.catalogue
    products, missing = catalogue.lookup(body.product_ids)
    if missing:
        raise HTTPException(404, detail={"message": "Unknown product IDs", "missing_ids": missing})
    try:
        agent = await factory()
    except ProviderNotConfiguredError as e:  # search provider missing (LLM absence is not an error)
        raise HTTPException(503, detail=f"Discovery is not configured: {e}") from None
    try:
        result = await agent.run(products)
    except ProviderError as e:
        raise HTTPException(502, detail=f"Upstream provider error: {e}") from None
    finally:
        await agent.aclose()
    request.app.state.review_store.record_run(result)
    return result
