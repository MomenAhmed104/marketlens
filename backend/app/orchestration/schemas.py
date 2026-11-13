"""Phase 7 schemas: temporary execution runs, stages, events and consolidated results.

Run state lives in memory (`RunStore`) and is never persisted. The only
durable side effect of a run is Phase 4's: newly discovered domains are added
to the review queue (pending), exactly as a standalone discovery run does.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.agents.discovery.schemas import DiscoveryRunResult, SearchTarget
from app.agents.intelligence.schemas import (
    IntelligenceMode,
    IntelligenceUsage,
    ProductIntelligence,
    SkippedTarget,
)
from app.services.matching.schemas import ProductMatchSummary


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"  # every stage completed or was deliberately skipped
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"  # results exist, but a stage or product failed
    FAILED = "FAILED"  # no usable results
    CANCELLED = "CANCELLED"


TERMINAL = {RunStatus.COMPLETED, RunStatus.PARTIALLY_COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


class StageName(StrEnum):
    VALIDATE = "validate"  # product ids and configuration
    DISCOVERY = "discovery"  # Phase 3 (LLM 1 when configured)
    REVIEW_QUEUE = "review_queue"  # Phase 4: new candidates saved as pending
    TARGETS = "targets"  # configured + approved eligible domains
    PRODUCT_SEARCH = "product_search"  # Phase 5 search + page retrieval
    EXTRACTION = "extraction"  # Phase 5 LLM 2 (or SKIPPED_NO_LLM)
    MATCHING = "matching"  # Phase 6 matching + price validation


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"  # finished with some failures (domains, pages, products)
    SKIPPED = "SKIPPED"  # deliberately not run (e.g. no LLM 2, discovery disabled)
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StageState(BaseModel):
    name: StageName
    status: StageStatus = StageStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    done: int = 0  # units processed (products, candidates...)
    total: int = 0
    message: str | None = None  # UI-safe summary, incl. why it was skipped or failed
    errors: list[str] = Field(default_factory=list)


class ProductRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProductRunResult(BaseModel):
    product_id: str
    status: ProductRunStatus = ProductRunStatus.PENDING
    error: str | None = None
    intelligence: ProductIntelligence | None = None  # Phase 5 candidates with search/retrieval evidence
    matching: ProductMatchSummary | None = None  # Phase 6 decisions per candidate


class PipelineResult(BaseModel):
    """Consolidated, progressively filled result of one run."""

    discovery: DiscoveryRunResult | None = None
    new_pending_domains: list[str] = Field(default_factory=list)  # need Phase 4 approval; never searched
    search_targets: list[SearchTarget] = Field(default_factory=list)
    skipped_targets: list[SkippedTarget] = Field(default_factory=list)
    intelligence_mode: IntelligenceMode | None = None
    llm2_disabled_reason: str | None = None
    intelligence_usage: IntelligenceUsage | None = None
    products: list[ProductRunResult] = Field(default_factory=list)


class RunRequest(BaseModel):
    product_ids: list[str] = Field(min_length=1)
    run_discovery: bool = True
    # Optional subset of the eligible search targets (preconfigured or reviewer-approved).
    domains: list[str] | None = Field(default=None, min_length=1, max_length=50)


class RunSummary(BaseModel):
    products_total: int = 0
    products_completed: int = 0
    products_failed: int = 0
    candidates: int = 0
    exact_matches: int = 0
    similar_matches: int = 0
    review_required: int = 0
    no_match: int = 0
    comparable_prices: int = 0


class ExecutionRun(BaseModel):
    """Run state without the (large) result. GET /api/runs/{id}."""

    run_id: str
    status: RunStatus = RunStatus.PENDING
    request: RunRequest
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested: bool = False
    stages: list[StageState]
    summary: RunSummary = Field(default_factory=RunSummary)
    error: str | None = None  # UI-safe reason for FAILED
    warnings: list[str] = Field(default_factory=list)
    last_event_seq: int = 0


class ExecutionEvent(BaseModel):
    """Stable SSE envelope (see CLAUDE.md §5)."""

    seq: int  # 1-based, per run; also the SSE `id`
    event_id: str  # "<run_id>:<seq>"
    run_id: str
    type: str  # run_started, stage_started, competitor_discovered, match_validated, ...
    timestamp: datetime
    product_id: str | None = None
    stage: StageName | None = None
    message: str  # UI-safe
    payload: dict[str, Any] = Field(default_factory=dict)  # small structured data; never secrets or page text
