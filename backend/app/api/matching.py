"""Product matching and price validation API (Phase 6).

Stateless and deterministic: takes a Phase 5 `IntelligenceRunResult` (as
returned by POST /api/intelligence/runs) and returns, for every candidate, the
match status, variant and condition checks, the price validation and whether
the pair qualifies for direct UK price comparison. No search, LLM or API key
is involved, and nothing is stored. Running Phase 5 and 6 together is
workflow orchestration (Phase 7).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.agents.intelligence.schemas import IntelligenceRunResult
from app.services.matching import evaluate_run
from app.services.matching.schemas import MatchingRunResult

router = APIRouter(prefix="/api/matching", tags=["matching"])


@router.post("/evaluate", response_model=MatchingRunResult)
def evaluate(body: IntelligenceRunResult, request: Request) -> MatchingRunResult:
    catalogue = request.app.state.catalogue
    missing = sorted({p.product.product_id for p in body.products} - set(catalogue.products))
    if missing:
        raise HTTPException(404, detail={"message": "Unknown product IDs in the run", "missing_ids": missing})
    return evaluate_run(body, catalogue.products, request.app.state.settings.matching_price_max_age_hours)
