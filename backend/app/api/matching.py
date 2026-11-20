"""Product matching and price validation API (Phase 6).

Stateless and deterministic: takes a Phase 5 `IntelligenceRunResult` (as
returned by POST /api/intelligence/runs) and returns, for every candidate, the
match status, variant and condition checks, the price validation and whether
the pair qualifies for direct UK price comparison. No search, LLM or API key
is involved, and nothing is stored. Running Phase 5 and 6 together is
workflow orchestration (Phase 7).

The client's payload is never trusted for who a competitor is: each
candidate's `competitor` (benchmark flag, approval and type) is recomputed from
server state (catalogue and review decisions). A domain that is not a current
server-side search target, or
whose page URL is on another domain, can never be a price benchmark.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.agents.intelligence.schemas import IntelligenceRunResult
from app.services.matching import evaluate_run
from app.services.matching.schemas import MatchingRunResult
from app.services.matching.trusted import REPLACED_WARNING, trusted_competitors

router = APIRouter(prefix="/api/matching", tags=["matching"])


def server_side_competitors(body: IntelligenceRunResult, request: Request) -> tuple[IntelligenceRunResult, int]:
    """The payload with every candidate's competitor replaced from server state. Returns (run, replaced)."""
    state = request.app.state
    return trusted_competitors(body, state.catalogue.competitors, state.review_store.list_decisions(),
                               state.settings)  # fmt: skip


@router.post("/evaluate", response_model=MatchingRunResult)
def evaluate(body: IntelligenceRunResult, request: Request) -> MatchingRunResult:
    catalogue = request.app.state.catalogue
    missing = sorted({p.product.product_id for p in body.products} - set(catalogue.products))
    if missing:
        raise HTTPException(404, detail={"message": "Unknown product IDs in the run", "missing_ids": missing})
    run, replaced = server_side_competitors(body, request)
    result = evaluate_run(run, catalogue.products, request.app.state.settings.matching_price_max_age_hours)
    if replaced:
        result.warnings.append(REPLACED_WARNING.format(n=replaced))
    return result
