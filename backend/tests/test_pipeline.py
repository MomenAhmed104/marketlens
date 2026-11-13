"""Phase 7 pipeline orchestration (mocked search and LLMs, no keys, no network)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.agents.intelligence import ProductIntelligenceAgent
from app.agents.intelligence.schemas import ExtractionStatus
from app.orchestration.pipeline import RunRequestError
from app.orchestration.schemas import (
    ExecutionRun,
    PipelineResult,
    ProductRunStatus,
    RunRequest,
    RunStatus,
    StageName,
    StageStatus,
)
from app.orchestration.store import InMemoryRunStore
from app.schemas.common import DomainApprovalStatus as S
from app.schemas.common import MatchStatus
from app.schemas.competitor import CompanyType as T
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.search import build_provider
from app.services.search.http import ProviderUnavailableError
from tests.discovery_fakes import FakeLLM, llm_failure
from tests.intelligence_fakes import FakeExtractLLM, FakeIntelSearch
from tests.pipeline_fakes import NO_LLM1, NO_LLM2, SlowIntelligenceAgent, harness
from tests.search_fakes import Recorder, ok
from tests.test_discovery_agent import CLASSIFICATIONS, PLAN
from tests.test_intelligence_agent import AND, EXTRACTIONS, HITS, PAGES

NOW = datetime(2026, 11, 13, tzinfo=UTC)
IDS = ["SB-001", "SB-028"]


def stages(run):
    return {st.name: st for st in run.stages}


def dec(domain, decision=ReviewDecision.APPROVED, ctype=T.RETAILER, bench=False):
    return DomainDecisionRecord(domain=domain, decision=decision, company_type=ctype, benchmark_eligible=bench,
                                reviewer="Alex", decided_at=NOW)  # fmt: skip


def searched_domains(h):
    return {q.domains[0] for a in h.intelligence_agents for q in a.search.queries}


# --------------------------------------------------------------------------- complete pipelines


def test_no_llm_pipeline_end_to_end(catalogue, tmp_path):
    h = harness(catalogue, tmp_path)
    run, result, events = asyncio.run(h.run(product_ids=IDS))
    st = stages(run)
    assert run.status is RunStatus.COMPLETED and run.error is None
    assert [s.status for s in run.stages] == [StageStatus.COMPLETED, StageStatus.COMPLETED, StageStatus.COMPLETED,
                                              StageStatus.COMPLETED, StageStatus.COMPLETED, StageStatus.SKIPPED,
                                              StageStatus.COMPLETED]  # fmt: skip
    assert NO_LLM1 in st[StageName.DISCOVERY].message and NO_LLM2 in st[StageName.EXTRACTION].message
    # Discovery ran in no-LLM mode; new domains are pending and saved to the Phase 4 queue.
    assert result.discovery.mode.value == "LLM_DISABLED"
    assert set(result.new_pending_domains) >= {"beckettsmusic.co.uk", "ebay.co.uk"}
    assert {c.domain for c in h.review.list_candidates()} >= set(result.new_pending_domains)
    assert h.review.list_decisions() == []  # nothing approved automatically
    # Pending domains are never searched; only the 13 configured competitors are.
    assert len(result.search_targets) == 13
    assert not searched_domains(h) & set(result.new_pending_domains)
    assert searched_domains(h) == {c.domain for c in catalogue.competitors}
    # Extraction explicitly skipped; matching ran on title/URL evidence and invented nothing.
    for pr in result.products:
        assert pr.status is ProductRunStatus.COMPLETED and pr.intelligence and pr.matching
        for c in pr.intelligence.candidates:
            assert c.extraction_status in (ExtractionStatus.SKIPPED_NO_LLM, ExtractionStatus.SKIPPED_NOT_RETRIEVED)
            assert c.price is None and c.product_title is None
        assert pr.matching.comparable_prices == 0
    assert run.summary.products_completed == 2 and run.summary.comparable_prices == 0
    assert run.summary.exact_matches >= 1  # Andertons RC-5 page: model in title + URL
    assert result.intelligence_mode.value == "LLM_DISABLED" and result.llm2_disabled_reason == NO_LLM2
    # All agents closed.
    assert all(a.search.closed for a in h.discovery_agents + h.intelligence_agents)


def test_events_are_ordered_and_complete(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, openrouter_api_key="sk-or-never-in-events")
    run, _, events = asyncio.run(h.run(product_ids=IDS))
    assert [e.seq for e in events] == list(range(1, len(events) + 1)) and run.last_event_seq == len(events)
    assert events[0].type == "run_started" and events[-1].type == "run_completed"
    types = [e.type for e in events]
    for t in ("product_loaded", "stage_started", "stage_completed", "competitor_discovered",
              "domain_approval_required", "known_domains_loaded", "search_started", "page_retrieved",
              "extraction_skipped", "match_validated", "price_validated", "product_completed"):  # fmt: skip
        assert t in types, t
    assert all(e.event_id == f"{run.run_id}:{e.seq}" and e.run_id == run.run_id and e.message for e in events)
    assert types.index("known_domains_loaded") < types.index("search_started")
    loaded = next(e for e in events if e.type == "known_domains_loaded")
    assert loaded.payload["configured"] == 13 and loaded.payload["pending_not_searched"]
    dump = json.dumps([e.model_dump(mode="json") for e in events])
    assert "sk-or-never-in-events" not in dump and "exa-test-key" not in dump


def test_full_llm_pipeline_with_comparable_price(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS),
                llm2=lambda: FakeExtractLLM(EXTRACTIONS))  # fmt: skip
    run, result, events = asyncio.run(h.run(product_ids=["SB-001"]))
    # LLM 1 auto-approved Beckett's for this run only: searched, but not persisted as a decision.
    targets = {t.domain: t for t in result.search_targets}
    assert targets["beckettsmusic.co.uk"].approval_status is S.AUTO_APPROVED
    assert "beckettsmusic.co.uk" in searched_domains(h) and h.review.list_decisions() == []
    assert "ebay.co.uk" in result.new_pending_domains and "ebay.co.uk" not in searched_domains(h)
    assert any(e.type == "domain_auto_approved" for e in events)
    # Extraction ran; one page's LLM call failed (fake) -> extraction PARTIAL -> run partially completed.
    assert stages(run)[StageName.EXTRACTION].status is StageStatus.PARTIAL
    assert run.status is RunStatus.PARTIALLY_COMPLETED
    pr = result.products[0]
    andertons = next(m for m in pr.matching.candidates if m.product_url == AND)
    assert andertons.match_status is MatchStatus.EXACT_MATCH and andertons.comparison.eligible
    assert andertons.comparison.difference_gbp == Decimal("189.00") - Decimal("189.99")
    assert run.summary.comparable_prices >= 1
    assert "competitor_product_extracted" in [e.type for e in events]


def test_auto_approved_domains_can_be_excluded(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS), pipeline_search_auto_approved=False)
    _, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
    assert "beckettsmusic.co.uk" not in {t.domain for t in result.search_targets}


def test_approved_pending_rejected_and_directory_domains(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, decisions=[
        dec("beckettsmusic.co.uk", ctype=T.SPECIALIST_RETAILER, bench=True),
        dec("ebay.co.uk", ReviewDecision.REJECTED, T.MARKETPLACE),
        dec("idealo.co.uk", ctype=T.DIRECTORY),
    ])  # fmt: skip
    _, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
    searched = searched_domains(h)
    assert "beckettsmusic.co.uk" in searched  # reviewer-approved
    assert not searched & {"ebay.co.uk", "idealo.co.uk", "lookalike-music.co.uk", "guitarworld.com"}
    assert {s.domain for s in result.skipped_targets} == {"ebay.co.uk", "idealo.co.uk"}
    beck = next(t for t in result.search_targets if t.domain == "beckettsmusic.co.uk")
    assert beck.approval_status is S.APPROVED and beck.price_benchmark
    # The reviewer's rejection is applied in discovery too; it is not re-queued as pending.
    assert "ebay.co.uk" not in result.new_pending_domains


def test_discovery_can_be_skipped_and_domains_narrowed(catalogue, tmp_path):
    h = harness(catalogue, tmp_path)
    run, result, _ = asyncio.run(h.run(product_ids=["SB-001"], run_discovery=False,
                                       domains=["www.andertons.co.uk", "gear4music.com"]))  # fmt: skip
    st = stages(run)
    assert st[StageName.DISCOVERY].status is StageStatus.SKIPPED
    assert st[StageName.REVIEW_QUEUE].status is StageStatus.SKIPPED
    assert run.status is RunStatus.COMPLETED and h.discovery_agents == []
    assert searched_domains(h) == {"andertons.co.uk", "gear4music.com"}


# --------------------------------------------------------------------------- partial failures


def test_discovery_failure_does_not_stop_product_search(catalogue, tmp_path):
    async def broken(decisions):
        raise ProviderUnavailableError("exa", "simulated outage")

    h = harness(catalogue, tmp_path, discovery_factory=broken)
    run, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
    st = stages(run)
    assert st[StageName.DISCOVERY].status is StageStatus.FAILED and "simulated outage" in st[StageName.DISCOVERY].message
    assert st[StageName.REVIEW_QUEUE].status is StageStatus.SKIPPED
    assert st[StageName.PRODUCT_SEARCH].status is StageStatus.COMPLETED and result.products[0].matching
    assert run.status is RunStatus.PARTIALLY_COMPLETED and any("Discovery failed" in w for w in run.warnings)


def test_discovery_timeout(catalogue, tmp_path):
    class Hanging:
        async def run(self, products):
            await asyncio.sleep(30)

        async def aclose(self):
            self.closed = True

    hanging = Hanging()

    async def factory(decisions):
        return hanging

    h = harness(catalogue, tmp_path, discovery_factory=factory, pipeline_discovery_timeout_seconds=0.05)
    run, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
    assert stages(run)[StageName.DISCOVERY].status is StageStatus.FAILED and hanging.closed
    assert "timed out" in stages(run)[StageName.DISCOVERY].message
    assert result.products[0].status is ProductRunStatus.COMPLETED


def test_one_failed_competitor_does_not_block_the_others(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, intel_search=lambda: FakeIntelSearch(HITS, PAGES, fail_domains={"andertons.co.uk"}))
    run, result, events = asyncio.run(h.run(product_ids=["SB-001"], run_discovery=False))
    assert stages(run)[StageName.PRODUCT_SEARCH].status is StageStatus.PARTIAL
    assert run.status is RunStatus.PARTIALLY_COMPLETED
    domains = {c.competitor.domain for c in result.products[0].intelligence.candidates}
    assert "gear4music.com" in domains and "andertons.co.uk" not in domains
    retrieved = next(e for e in events if e.type == "page_retrieved")
    assert retrieved.payload["failed_domains"] == ["andertons.co.uk"]


def test_failed_product_does_not_block_the_next(catalogue, tmp_path):
    class FailsFor028(ProductIntelligenceAgent):
        async def run_product(self, p):
            if p.product_id == "SB-028":
                raise RuntimeError("boom")
            return await super().run_product(p)

    h = harness(catalogue, tmp_path, intel_agent_cls=FailsFor028)
    run, result, events = asyncio.run(h.run(product_ids=["SB-028", "SB-001"], run_discovery=False))
    by = {p.product_id: p for p in result.products}
    assert by["SB-028"].status is ProductRunStatus.FAILED and "boom" in by["SB-028"].error
    assert by["SB-001"].status is ProductRunStatus.COMPLETED and by["SB-001"].matching
    assert run.status is RunStatus.PARTIALLY_COMPLETED and run.summary.products_failed == 1
    assert "product_failed" in [e.type for e in events]


def test_product_timeout(catalogue, tmp_path):
    class Slow(ProductIntelligenceAgent):
        async def run_product(self, p):
            await asyncio.sleep(30)

    h = harness(catalogue, tmp_path, intel_agent_cls=Slow, pipeline_product_timeout_seconds=0.05)
    run, result, _ = asyncio.run(h.run(product_ids=["SB-001"], run_discovery=False))
    assert run.status is RunStatus.FAILED and run.error == "No product completed"
    assert "Timed out" in result.products[0].error


def test_intelligence_cannot_start(catalogue, tmp_path):
    async def broken(targets, skipped):
        raise ProviderUnavailableError("exa", "down")

    h = harness(catalogue, tmp_path, intelligence_factory=broken)
    run, result, events = asyncio.run(h.run(product_ids=["SB-001"]))
    assert run.status is RunStatus.FAILED and events[-1].type == "run_failed"
    assert stages(run)[StageName.PRODUCT_SEARCH].status is StageStatus.FAILED
    assert result.products[0].status is ProductRunStatus.FAILED
    assert result.discovery is not None  # earlier stage results are kept


def test_llm2_errors_are_isolated_per_page(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, llm2=lambda: FakeExtractLLM({u: llm_failure() for u in PAGES}))
    run, result, _ = asyncio.run(h.run(product_ids=["SB-001"], run_discovery=False))
    assert stages(run)[StageName.EXTRACTION].status is StageStatus.FAILED
    assert result.products[0].status is ProductRunStatus.COMPLETED  # matching still ran on title/URL
    assert run.status is RunStatus.PARTIALLY_COMPLETED


def test_transient_provider_errors_are_retried_by_the_transport(catalogue, tmp_path):
    """A 503 on search is retried by the provider HTTP client inside a pipeline run."""
    search_ok = {"requestId": "r1", "costDollars": {"total": 0.007}, "results": [
        {"url": AND, "title": "Boss RC-5 Loop Station | Andertons", "highlights": ["£189"]}]}
    contents_ok = {"requestId": "r2", "costDollars": {"total": 0.001},
                   "results": [{"id": AND, "url": AND, "title": "Boss RC-5", "text": PAGES[AND].text}],
                   "statuses": [{"id": AND, "status": "success", "source": "cached"}]}  # fmt: skip
    rec = Recorder(ok({"error": "busy"}, status=503), ok(search_ok), ok(contents_ok))

    async def factory(targets, skipped):
        import httpx

        s = h.settings
        provider = build_provider("exa", s, client=httpx.AsyncClient(transport=httpx.MockTransport(rec)))
        provider.http._sleep = _no_sleep
        return ProductIntelligenceAgent(provider, None, s, targets, skipped, llm_disabled_reason=NO_LLM2)

    h = harness(catalogue, tmp_path, intelligence_factory=factory)
    run, result, _ = asyncio.run(h.run(product_ids=["SB-001"], run_discovery=False, domains=["andertons.co.uk"]))
    assert len(rec.requests) == 3 and run.status is RunStatus.COMPLETED
    (c,) = result.products[0].intelligence.candidates
    assert c.page.status.value == "OK" and c.extraction_status is ExtractionStatus.SKIPPED_NO_LLM


async def _no_sleep(_):
    return None


# --------------------------------------------------------------------------- cancellation


def test_cancel_during_product_search(catalogue, tmp_path):
    SlowIntelligenceAgent.gate, SlowIntelligenceAgent.entered = None, None
    h = harness(catalogue, tmp_path, intel_agent_cls=SlowIntelligenceAgent)

    async def scenario():
        SlowIntelligenceAgent.gate, SlowIntelligenceAgent.entered = asyncio.Event(), asyncio.Event()
        run = h.manager.start(RunRequest(product_ids=IDS))
        await asyncio.wait_for(SlowIntelligenceAgent.entered.wait(), 5)
        assert h.store.get(run.run_id).status is RunStatus.RUNNING
        h.manager.cancel(run.run_id)
        await h.manager.wait(run.run_id)
        with pytest.raises(RunRequestError) as e:
            h.manager.cancel(run.run_id)
        assert e.value.status_code == 409
        return h.store.get(run.run_id), h.store.get_result(run.run_id), h.store.events_after(run.run_id, 0)

    run, result, events = asyncio.run(scenario())
    st = stages(run)
    assert run.status is RunStatus.CANCELLED and run.cancel_requested and run.finished_at
    assert st[StageName.DISCOVERY].status is StageStatus.COMPLETED  # finished before the cancel
    assert st[StageName.PRODUCT_SEARCH].status is StageStatus.CANCELLED
    assert st[StageName.MATCHING].status is StageStatus.CANCELLED
    assert all(p.status is ProductRunStatus.CANCELLED for p in result.products)
    assert result.discovery is not None  # partial results kept
    assert events[-1].type == "run_cancelled"
    assert all(a.search.closed for a in h.intelligence_agents)  # clients closed on cancel


def test_cancel_unknown_run(catalogue, tmp_path):
    h = harness(catalogue, tmp_path)
    with pytest.raises(RunRequestError) as e:
        h.manager.cancel("run-nope")
    assert e.value.status_code == 404


# --------------------------------------------------------------------------- invalid input and limits


@pytest.mark.parametrize("req,code", [
    ({"product_ids": ["SB-001", "SB-999"]}, 404),
    ({"product_ids": [f"SB-{i:03d}" for i in range(1, 7)]}, 422),
    ({"product_ids": ["SB-001"], "domains": ["pending-shop.co.uk"]}, 422),
    ({"product_ids": ["SB-001"], "domains": ["not a domain!"]}, 422),
])  # fmt: skip
def test_invalid_requests(catalogue, tmp_path, req, code):
    h = harness(catalogue, tmp_path)
    with pytest.raises(RunRequestError) as e:
        h.manager.validate(RunRequest(**req))
    assert e.value.status_code == code


def test_search_provider_must_be_configured(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, exa_api_key=None)
    with pytest.raises(RunRequestError) as e:
        h.manager.validate(RunRequest(product_ids=["SB-001"]))
    assert e.value.status_code == 503 and "SEARCH_PROVIDER" in str(e.value.detail)


def test_product_ids_are_normalised_and_deduplicated(catalogue, tmp_path):
    h = harness(catalogue, tmp_path)
    products, req = h.manager.validate(RunRequest(product_ids=["sb-001", "SB-001 ", "SB-028"]))
    assert req.product_ids == ["SB-001", "SB-028"] and [p.product_id for p in products] == req.product_ids


def test_active_run_limit(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, intel_agent_cls=SlowIntelligenceAgent, pipeline_max_active_runs=1)

    async def scenario():
        SlowIntelligenceAgent.gate, SlowIntelligenceAgent.entered = asyncio.Event(), asyncio.Event()
        first = h.manager.start(RunRequest(product_ids=["SB-001"], run_discovery=False))
        with pytest.raises(RunRequestError) as e:
            h.manager.start(RunRequest(product_ids=["SB-001"]))
        assert e.value.status_code == 429
        SlowIntelligenceAgent.gate.set()
        await h.manager.wait(first.run_id)
        h.manager.start(RunRequest(product_ids=["SB-001"], run_discovery=False))  # allowed again
        await h.manager.shutdown()

    asyncio.run(scenario())


# --------------------------------------------------------------------------- temporary state


def _finished(run_id, minutes_ago, now):
    from app.orchestration.schemas import StageState

    return ExecutionRun(run_id=run_id, status=RunStatus.COMPLETED, request=RunRequest(product_ids=["SB-001"]),
                        created_at=now - timedelta(minutes=minutes_ago + 1),
                        finished_at=now - timedelta(minutes=minutes_ago),
                        stages=[StageState(name=n) for n in StageName])  # fmt: skip


def test_store_prunes_by_age_and_count():
    store = InMemoryRunStore(max_retained=2, ttl_minutes=60)
    for i, age in enumerate((90, 30, 20, 10)):
        store.create(_finished(f"r{i}", age, NOW), PipelineResult())
    dropped = store.prune(NOW)
    assert set(dropped) == {"r0", "r1"} and [r.run_id for r in store.list_runs()] == ["r3", "r2"]


def test_runs_are_retrievable_but_not_persisted(catalogue, tmp_path):
    h = harness(catalogue, tmp_path)
    run, result, _ = asyncio.run(h.run(product_ids=["SB-001"], run_discovery=False))
    assert h.store.get(run.run_id).status is RunStatus.COMPLETED and h.store.get_result(run.run_id) is result
    # A new manager (e.g. after a restart) starts empty; nothing was written to disk except the review DB.
    fresh = harness(catalogue, tmp_path)
    assert fresh.store.get(run.run_id) is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["review.sqlite3"]


def test_wait_for_events_wakes_up_and_times_out(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, intel_agent_cls=SlowIntelligenceAgent)

    async def scenario():
        SlowIntelligenceAgent.gate, SlowIntelligenceAgent.entered = asyncio.Event(), asyncio.Event()
        run = h.manager.start(RunRequest(product_ids=["SB-001"], run_discovery=False))
        await SlowIntelligenceAgent.entered.wait()
        last = h.store.get(run.run_id).last_event_seq
        assert await h.store.wait_for_events(run.run_id, last, 0.05) == []  # nothing new: times out
        SlowIntelligenceAgent.gate.set()
        new = await h.store.wait_for_events(run.run_id, last, 5)
        assert new and new[0].seq == last + 1
        await h.manager.wait(run.run_id)

    asyncio.run(scenario())


def test_stale_evidence_is_never_compared(catalogue, tmp_path):
    from tests.discovery_fakes import NOW as FAKE_NOW

    h = harness(catalogue, tmp_path, llm2=lambda: FakeExtractLLM(EXTRACTIONS),
                clock=lambda: FAKE_NOW + timedelta(days=3))  # fmt: skip
    _, result, _ = asyncio.run(h.run(product_ids=["SB-001"], run_discovery=False))
    andertons = next(m for m in result.products[0].matching.candidates if m.product_url == AND)
    assert andertons.match_status is MatchStatus.EXACT_MATCH and andertons.price.evidence_status.value == "STALE"
    assert not andertons.comparison.eligible
