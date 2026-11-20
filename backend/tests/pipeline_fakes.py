"""Wiring for pipeline tests: fake discovery/intelligence factories over mocked search and LLMs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from app.agents.discovery.agent import CompetitorDiscoveryAgent
from app.agents.intelligence import ProductIntelligenceAgent
from app.orchestration.pipeline import PipelineDeps, RunManager
from app.orchestration.schemas import RunRequest
from app.orchestration.store import InMemoryRunStore
from app.services.review_store import ReviewStore
from tests.discovery_fakes import NOW as FAKE_NOW
from tests.discovery_fakes import FakeSearch
from tests.intelligence_fakes import FakeIntelSearch
from tests.search_fakes import make_settings
from tests.test_discovery_agent import GLOBAL_HITS, PROFILE_HITS
from tests.test_intelligence_agent import HITS, PAGES

NO_LLM1 = "OPENROUTER_API_KEY and LLM1_MODEL not set"
NO_LLM2 = "OPENROUTER_API_KEY and LLM2_MODEL not set"


@dataclass
class Harness:
    manager: RunManager
    review: ReviewStore
    settings: object
    discovery_agents: list = field(default_factory=list)
    intelligence_agents: list = field(default_factory=list)

    @property
    def store(self):
        return self.manager.store

    async def run(self, **req):
        run = self.manager.start(RunRequest(**req))
        await self.manager.wait(run.run_id)
        return self.store.get(run.run_id), self.store.get_result(run.run_id), self.store.events_after(run.run_id, 0)


def harness(catalogue, tmp_path, *, llm1=None, llm2=None, disc_search=None, intel_search=None, decisions=(),
            intel_agent_cls=ProductIntelligenceAgent, discovery_factory=None, intelligence_factory=None,
            clock=lambda: FAKE_NOW + timedelta(hours=1), **settings_kw) -> Harness:  # fmt: skip
    values = {"search_provider": "exa", "exa_api_key": "exa-test-key", "review_db_path": tmp_path / "review.sqlite3"}
    values.update(settings_kw)
    s = make_settings(**values)
    review = ReviewStore(s.review_db_path, s.discovery_excluded_competitor_domains)
    for d in decisions:
        review.create_decision(d)
    h = Harness(manager=None, review=review, settings=s)  # type: ignore[arg-type]
    cats = [p.category for p in catalogue.products.values()]

    async def disc_factory(decisions):
        agent = CompetitorDiscoveryAgent(disc_search() if disc_search else FakeSearch(GLOBAL_HITS, PROFILE_HITS),
                                         llm1() if llm1 else None, s, catalogue.competitors, cats,
                                         llm_disabled_reason=None if llm1 else NO_LLM1,
                                         review_decisions=decisions)  # fmt: skip
        h.discovery_agents.append(agent)
        return agent

    async def intel_factory(targets, skipped):
        agent = intel_agent_cls(intel_search() if intel_search else FakeIntelSearch(HITS, PAGES),
                                llm2() if llm2 else None, s, targets, skipped,
                                llm_disabled_reason=None if llm2 else NO_LLM2)  # fmt: skip
        h.intelligence_agents.append(agent)
        return agent

    # Fake pages are dated FAKE_NOW; the injected clock keeps their price evidence fresh (not STALE).
    deps = PipelineDeps(s, catalogue, review, discovery_factory or disc_factory, intelligence_factory or intel_factory,
                        clock=clock)  # fmt: skip
    h.manager = RunManager(InMemoryRunStore(s.pipeline_max_runs_retained, s.pipeline_run_ttl_minutes), deps)
    return h


class SlowIntelligenceAgent(ProductIntelligenceAgent):
    """run_product blocks until released (or cancelled)."""

    gate: asyncio.Event | None = None
    entered: asyncio.Event | None = None

    async def run_product(self, p):
        type(self).entered.set()
        await type(self).gate.wait()
        return await super().run_product(p)
