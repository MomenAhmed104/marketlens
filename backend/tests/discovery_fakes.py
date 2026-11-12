"""In-memory search provider and LLM for discovery tests (no network)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.agents.discovery.schemas import ClassificationBatch, DiscoveryPlan
from app.schemas.search import CostBasis, CostInfo, RetrievalResponse, SearchQuery, SearchResponse, SearchScope
from app.services.llm.base import LLMClient, LLMOutputError, LLMResult, LLMUsage
from app.services.search.base import RawHit, SearchProvider, finalize_results
from app.services.search.http import ProviderUnavailableError

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


class FakeSearch(SearchProvider):
    name = "fake-exa"
    supports_retrieval = False

    def __init__(self, global_hits: dict[str, list[RawHit]], profile_hits: dict[str, list[RawHit]],
                 fail_texts: set[str] = frozenset()):  # fmt: skip
        self.global_hits, self.profile_hits, self.fail_texts = global_hits, profile_hits, fail_texts
        self.queries: list[SearchQuery] = []
        self.closed = False

    async def search(self, query: SearchQuery) -> SearchResponse:
        self.queries.append(query)
        if query.text in self.fail_texts:
            raise ProviderUnavailableError(self.name, "simulated outage")
        if query.scope is SearchScope.DOMAIN:
            hits = self.profile_hits.get(query.domains[0], [])
        else:
            hits = self.global_hits.get(query.product_id, [])
        results, dropped = finalize_results(self.name, query, hits)
        return SearchResponse(provider=self.name, query=query, results=results, out_of_scope_dropped=dropped,
                              retrieved_at=NOW, latency_ms=5, request_id=f"req-{len(self.queries)}",
                              cost=CostInfo(usd=Decimal("0.007"), basis=CostBasis.PROVIDER_REPORTED))  # fmt: skip

    async def retrieve(self, urls):  # pragma: no cover - not used in Phase 3
        raise NotImplementedError

    async def aclose(self):
        self.closed = True


class FakeLLM(LLMClient):
    model = "fake/llm-1"

    def __init__(self, plan: DiscoveryPlan | Exception, classifications: ClassificationBatch | Exception):
        self.plan, self.classifications = plan, classifications
        self.calls: list[dict] = []
        self.closed = False

    async def complete_structured(self, *, system, user, output_model, schema_name):
        self.calls.append({"system": system, "user": user, "schema": schema_name})
        value = self.plan if output_model is DiscoveryPlan else self.classifications
        if isinstance(value, Exception):
            raise value
        if output_model is ClassificationBatch:
            # Only return entries for domains present in this batch's prompt.
            value = ClassificationBatch(companies=[c for c in value.companies if f'"{c.domain}"' in user
                                                   or c.domain == "never-given.example"])  # fmt: skip
        return LLMResult(value=value, model="fake/llm-1@2026", request_ids=[f"gen-{len(self.calls)}"],
                         latency_ms=3, usage=LLMUsage(1000, 200, Decimal("0.002")))  # fmt: skip

    async def aclose(self):
        self.closed = True


def llm_failure() -> LLMOutputError:
    return LLMOutputError("openrouter", "simulated invalid output")
