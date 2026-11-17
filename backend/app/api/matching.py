"""Product matching and price validation API (Phase 6).

Stateless and deterministic: takes a Phase 5 `IntelligenceRunResult` (as
returned by POST /api/intelligence/runs) and returns, for every candidate, the
match status, variant and condition checks, the price validation and whether
the pair qualifies for direct UK price comparison. No search, LLM or API key
is involved, and nothing is stored. Running Phase 5 and 6 together is
workflow orchestration (Phase 7).

The client's payload is never trusted for who a competitor is: each
candidate's `competitor` (benchmark flag, approval, type and UK gate decision)
is recomputed from server state (catalogue, review decisions, UK eligibility
and exceptions). A domain that is not a current server-side search target, or
whose page URL is on another domain, can never be a price benchmark.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from app.agents.discovery.schemas import SearchTarget
from app.agents.intelligence import eligible_search_targets
from app.agents.intelligence.schemas import IntelligenceRunResult
from app.services.domains import registrable_domain
from app.services.matching import evaluate_run
from app.services.matching.schemas import MatchingRunResult
from app.services.uk_gate import gate_decision

router = APIRouter(prefix="/api/matching", tags=["matching"])


def _domain(value: str) -> str | None:
    try:
        return registrable_domain(value)
    except ValueError:
        return None


def server_side_competitors(body: IntelligenceRunResult, request: Request) -> tuple[IntelligenceRunResult, int]:
    """The payload with every candidate's competitor replaced from server state. Returns (run, replaced)."""
    state = request.app.state
    uk = state.review_store.uk_gate_state()
    targets, _ = eligible_search_targets(state.catalogue.competitors, state.review_store.list_decisions(), uk,
                                         state.settings)  # fmt: skip
    by_domain = {t.domain: t for t in targets}
    replaced = 0
    products = []
    for pr in body.products:
        candidates = []
        for c in pr.candidates:
            claimed = _domain(c.competitor.domain)
            page = _domain(urlsplit(c.product_url).hostname or "")
            server = by_domain.get(claimed) if claimed and claimed == page else None
            if server is None:  # not a searchable target now, or the page is elsewhere: never a benchmark
                server = SearchTarget(domain=claimed or c.competitor.domain, company_name=None, company_type=None,
                                      approval_status=c.competitor.approval_status, price_benchmark=False,
                                      uk_gate=gate_decision(claimed, uk) if claimed else None)  # fmt: skip
            if server != c.competitor:
                replaced += 1
            candidates.append(c.model_copy(update={"competitor": server}))
        products.append(pr.model_copy(update={"candidates": candidates}))
    return body.model_copy(update={"products": products}), replaced


@router.post("/evaluate", response_model=MatchingRunResult)
def evaluate(body: IntelligenceRunResult, request: Request) -> MatchingRunResult:
    catalogue = request.app.state.catalogue
    missing = sorted({p.product.product_id for p in body.products} - set(catalogue.products))
    if missing:
        raise HTTPException(404, detail={"message": "Unknown product IDs in the run", "missing_ids": missing})
    run, replaced = server_side_competitors(body, request)
    result = evaluate_run(run, catalogue.products, request.app.state.settings.matching_price_max_age_hours)
    if replaced:
        result.warnings.append(f"{replaced} candidate competitor record(s) differed from server state and were "
                               "replaced: benchmark eligibility and UK eligibility are always recomputed server-side")
    return result
