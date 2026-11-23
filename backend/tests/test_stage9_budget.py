"""Stage 9 spending control: ledger, worst-case estimate, HTTP meter, budgeted runs.

No network and no paid call: Exa and OpenRouter are real adapters over httpx.MockTransport, and
every unit price below is a made-up TEST value (not a provider price).
"""

import asyncio
import json
import threading
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.discovery.agent import CompetitorDiscoveryAgent
from app.agents.intelligence import ProductIntelligenceAgent
from app.main import create_app
from app.orchestration.pipeline import PipelineDeps, RunRequestError
from app.orchestration.schemas import RunRequest, RunStatus as PipelineRunStatus
from app.orchestration.store import InMemoryRunStore
from app.services.llm import build_llm2
from app.services.search import build_provider
from app.services.search.http import ProviderNotConfiguredError
from app.services.review_store import ReviewStore
from app.stage9 import cli
from app.stage9.budget import (
    AttemptOutcome,
    BudgetLedger,
    BudgetRefused,
    LedgerError,
    RunStatus,
    UnitPrices,
    estimate_run,
)
from app.stage9.context import CURRENT_METER, active_meter
from app.stage9.manager import BudgetedRunManager, pipeline_costs
from app.stage9.meter import BudgetExceededError, RunMeter
from tests.search_fakes import make_settings

# TEST prices only: chosen to make arithmetic easy, not taken from any provider.
TEST_PRICES = dict(stage9_exa_usd_per_search_max=Decimal("0.01"), stage9_exa_usd_per_page_max=Decimal("0.002"),
                   stage9_llm_usd_per_input_token_max=Decimal("0.000001"),
                   stage9_llm_usd_per_output_token_max=Decimal("0.000004"))  # fmt: skip
D = Decimal


def settings(tmp_path, **kw):
    values = {"search_provider": "exa", "exa_api_key": "exa-test-key", "stage9_ledger_path": tmp_path / "ledger.sqlite3",
              "review_db_path": tmp_path / "review.sqlite3", **TEST_PRICES}  # fmt: skip
    values.update(kw)
    return make_settings(**values)


def ledger(tmp_path, ceiling="3.00") -> BudgetLedger:
    return BudgetLedger.create(tmp_path / "ledger.sqlite3", D(ceiling), "test")


# --------------------------------------------------------------------------- ledger


def test_a_ledger_is_never_overwritten_and_survives_reopening(tmp_path):
    led = ledger(tmp_path)
    led.reserve("r1", D("1.25"))
    with pytest.raises(LedgerError):
        BudgetLedger.create(tmp_path / "ledger.sqlite3", D("100"))
    again = BudgetLedger(tmp_path / "ledger.sqlite3")  # e.g. after an application restart
    s = again.summary()
    assert s.ceiling_usd == D("3.00") and s.committed_usd == D("1.25") and s.remaining_usd == D("1.75")
    assert s.open_reservations == ["r1"]


def test_a_missing_ledger_is_not_created_silently(tmp_path):
    with pytest.raises(LedgerError):
        BudgetLedger(tmp_path / "nope.sqlite3")
    assert not (tmp_path / "nope.sqlite3").exists()


def test_a_reservation_over_the_remaining_budget_is_refused_and_recorded(tmp_path):
    led = ledger(tmp_path, "1.00")
    assert led.reserve("a", D("0.60")) == D("0.40")
    with pytest.raises(BudgetRefused) as e:
        led.reserve("b", D("0.41"))
    assert e.value.remaining == D("0.40") and led.run("b")["status"] == RunStatus.REFUSED
    assert led.summary().committed_usd == D("0.60")  # a refusal commits nothing
    with pytest.raises(LedgerError):
        led.reserve("a", D("0.01"))  # one entry per run


def test_concurrent_reservations_never_share_the_remaining_budget(tmp_path):
    led = ledger(tmp_path, "1.00")
    ok, refused = [], []

    def go(i):
        try:
            BudgetLedger(led.path).reserve(f"run-{i}", D("0.40"))  # own connection, like another process
            ok.append(i)
        except BudgetRefused:
            refused.append(i)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 2 and len(refused) == 8
    assert led.summary().committed_usd == D("0.80") <= led.ceiling_usd


def test_reconciliation_statuses(tmp_path):
    led = ledger(tmp_path)
    # Clean: every attempt reported, matches the pipeline.
    led.reserve("clean", D("0.50"))
    led.record_attempt("clean", "exa", "/search", D("0.01"), D("0.007"), D("0.007"), 200, AttemptOutcome.REPORTED)
    led.record_attempt("clean", "openrouter", "/chat/completions", D("0.02"), D("0.001"), D("0.001"), 200,
                       AttemptOutcome.REPORTED)  # fmt: skip
    assert led.reconcile("clean", D("0.007"), D("0.001")) is RunStatus.RECONCILED
    assert led.summary().committed_usd == D("0.008")  # the reservation is replaced by the real charge
    # An unreported attempt is charged at its bound and needs review.
    led.reserve("gap", D("0.50"))
    led.record_attempt("gap", "exa", "/search", D("0.01"), None, D("0.01"), None, AttemptOutcome.NO_RESPONSE)
    assert led.reconcile("gap", D("0"), D("0")) is RunStatus.NEEDS_REVIEW
    assert any("no provider-reported cost" in n for n in json.loads(led.run("gap")["notes"]))
    # A provider cost above the configured maximum is flagged.
    led.reserve("above", D("0.50"))
    led.record_attempt("above", "exa", "/search", D("0.01"), D("0.02"), D("0.02"), 200, AttemptOutcome.REPORTED)
    assert led.reconcile("above", D("0.02"), D("0")) is RunStatus.NEEDS_REVIEW
    # A mismatch with the pipeline's own record is flagged.
    led.reserve("mismatch", D("0.50"))
    led.record_attempt("mismatch", "exa", "/search", D("0.01"), D("0.005"), D("0.005"), 200, AttemptOutcome.REPORTED)
    assert led.reconcile("mismatch", D("0.004"), D("0")) is RunStatus.NEEDS_REVIEW
    with pytest.raises(LedgerError):
        led.reconcile("clean", D("0"), D("0"))  # already closed


def test_release_resolve_and_interrupted_runs(tmp_path):
    led = ledger(tmp_path, "1.00")
    led.reserve("never-started", D("0.30"))
    led.release("never-started", "test")
    assert led.run("never-started")["status"] == RunStatus.RELEASED and led.summary().committed_usd == 0
    led.reserve("crashed", D("0.50"))
    led.record_attempt("crashed", "exa", "/search", D("0.01"), D("0.007"), D("0.007"), 200, AttemptOutcome.REPORTED)
    with pytest.raises(LedgerError):
        led.release("crashed", "a run that spent cannot be released")
    # Restart: the interrupted reservation still counts in full.
    assert BudgetLedger(led.path).summary().committed_usd == D("0.50")
    with pytest.raises(LedgerError):
        led.resolve("crashed", D("0.001"), D("0"), "below what the provider reported")
    with pytest.raises(LedgerError):
        led.resolve("crashed", D("0.007"), D("0"), "  ")
    led.resolve("crashed", D("0.007"), D("0"), "Exa dashboard, 23 Nov")
    assert led.run("crashed")["status"] == RunStatus.RESOLVED and led.summary().committed_usd == D("0.007")


# --------------------------------------------------------------------------- estimate


def test_prices_have_no_defaults(tmp_path):
    s = make_settings()
    assert UnitPrices.missing(s) == ["STAGE9_EXA_USD_PER_SEARCH_MAX", "STAGE9_EXA_USD_PER_PAGE_MAX",
                                     "STAGE9_LLM_USD_PER_INPUT_TOKEN_MAX", "STAGE9_LLM_USD_PER_OUTPUT_TOKEN_MAX"]
    with pytest.raises(ValueError):
        UnitPrices.from_settings(s)


def test_worst_case_counts_every_call_and_every_attempt(tmp_path):
    s = settings(tmp_path)
    p = UnitPrices.from_settings(s)
    est = estimate_run(s, p, products=2, run_discovery=True, max_targets=15, llm1=True, llm2=True)
    disc = 2 * (s.discovery_max_global_queries_per_product + 1) + s.discovery_max_candidates
    intel = 2 * 15 * s.intelligence_queries_per_domain
    pages = 2 * min(s.intelligence_max_pages_per_product, 15 * s.intelligence_max_pages_per_domain)
    assert est.exa_searches == disc + intel and est.exa_pages == pages
    assert est.llm1_calls == 1 + -(-s.discovery_max_candidates // s.discovery_classification_batch_size)
    assert est.llm2_calls == pages * 2  # extraction + match proposal per page
    assert est.exa_attempts_per_call == s.search_max_retries + 1
    assert est.llm_attempts_per_call == (s.llm_max_retries + 1) * 2  # transport retries x repair attempt
    exa = (D("0.01") * est.exa_searches + D("0.002") * pages) * est.exa_attempts_per_call
    assert est.exa_usd == exa
    llm1 = p.llm_call_max(s.stage9_llm_max_request_bytes, s.llm1_max_completion_tokens)
    llm2 = p.llm_call_max(s.stage9_llm_max_request_bytes, s.llm2_max_completion_tokens)
    assert est.openrouter_usd == (llm1 * est.llm1_calls + llm2 * est.llm2_calls) * est.llm_attempts_per_call
    no_llm = estimate_run(s, p, products=2, run_discovery=False, max_targets=15, llm1=False, llm2=False)
    assert no_llm.openrouter_usd == 0 and no_llm.llm1_calls == no_llm.llm2_calls == 0


# --------------------------------------------------------------------------- meter over mocked HTTP


class Exa:
    """Mock Exa API. `plan` maps a call number (1-based) to a response or an exception."""

    def __init__(self, cost="0.005", plan=None):
        self.cost, self.plan, self.calls = cost, plan or {}, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        step = self.plan.get(len(self.calls))
        if isinstance(step, Exception):
            raise step
        if isinstance(step, httpx.Response):
            return step
        body = {"requestId": "req", "results": []}
        if self.cost is not None:
            body["costDollars"] = {"total": float(self.cost)}
        if request.url.path == "/contents":
            body["statuses"] = []
        return httpx.Response(200, json=body)


def metered_exa(tmp_path, handler, reservation="0.50", **kw):
    s = settings(tmp_path, search_max_retries=kw.pop("retries", 2), **kw)
    led = ledger(tmp_path)
    led.reserve("r", D(reservation))
    meter = RunMeter("r", D(reservation), UnitPrices.from_settings(s), led)
    token = CURRENT_METER.set(meter)
    try:
        provider = build_provider("exa", s, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    finally:
        CURRENT_METER.reset(token)
    provider.http._sleep = lambda _: asyncio.sleep(0)  # no real back-off waits
    return provider, meter, led


def search(provider, n=5):
    from app.schemas.search import SearchQuery

    return provider.search(SearchQuery(text="boss rc-5", max_results=n))


def test_reported_costs_are_charged_and_recorded(tmp_path):
    exa = Exa(cost="0.005")
    provider, meter, led = metered_exa(tmp_path, exa)
    asyncio.run(search(provider))
    asyncio.run(provider.retrieve(["https://shop.example/a", "https://shop.example/b"]))
    meter.close()
    rows = led.attempts("r")
    assert [(r["endpoint"], r["bound_usd"], r["reported_usd"], r["outcome"]) for r in rows] == [
        ("/search", "0.01", "0.005", "REPORTED"), ("/contents", "0.004", "0.005", "REPORTED")]
    assert meter.charged == D("0.010")


def test_retries_are_metered_attempt_by_attempt(tmp_path):
    exa = Exa(plan={1: httpx.Response(503, json={"error": "busy"}), 2: httpx.ConnectTimeout("slow")})
    provider, meter, led = metered_exa(tmp_path, exa)
    asyncio.run(search(provider))  # 503, timeout, then success
    meter.close()
    outcomes = [r["outcome"] for r in led.attempts("r")]
    assert sorted(outcomes) == ["NO_RESPONSE", "REPORTED", "UNREPORTED"] and len(exa.calls) == 3
    assert meter.charged == D("0.01") + D("0.01") + D("0.005")  # failed attempts at their bound


def test_a_response_without_a_cost_is_charged_at_its_bound(tmp_path):
    provider, meter, led = metered_exa(tmp_path, Exa(cost=None))
    asyncio.run(search(provider))
    assert led.attempts("r")[0]["outcome"] == "UNREPORTED" and meter.charged == D("0.01")


def test_the_reservation_is_a_hard_cap_enforced_before_sending(tmp_path):
    exa = Exa()
    provider, meter, led = metered_exa(tmp_path, exa, reservation="0.024")
    # Each call: bound 0.01 checked before sending, then the reported 0.005 is charged.
    asyncio.run(search(provider))  # 0 + 0.01 <= 0.024
    asyncio.run(search(provider))  # 0.005 + 0.01 <= 0.024
    asyncio.run(search(provider))  # 0.010 + 0.01 <= 0.024; then 0.015 + 0.01 > 0.024: refused
    with pytest.raises(BudgetExceededError) as e:
        asyncio.run(search(provider))
    assert e.value.status_code == 402 and len(exa.calls) == 3  # the refused call was never sent
    assert led.run("r")["refused_attempts"] == 1 and meter.charged <= D("0.025")


def test_in_flight_attempts_count_against_the_cap(tmp_path):
    exa = Exa()

    async def slow(request):  # both requests are in flight at the same time
        await asyncio.sleep(0.05)
        return exa(request)

    provider, meter, _ = metered_exa(tmp_path, slow, reservation="0.015")

    async def two_at_once():
        return await asyncio.gather(search(provider), search(provider), return_exceptions=True)

    results = asyncio.run(two_at_once())
    assert sum(isinstance(r, BudgetExceededError) for r in results) == 1 and len(exa.calls) == 1


def test_searches_beyond_the_priced_result_count_are_refused(tmp_path):
    exa = Exa()
    provider, _, _ = metered_exa(tmp_path, exa)  # prices cover at most 10 results (default)
    with pytest.raises(BudgetExceededError):
        asyncio.run(search(provider, n=11))
    with pytest.raises(BudgetExceededError):
        asyncio.run(_raw(provider, {"numResults": "x"}))
    asyncio.run(search(provider, n=10))
    assert len(exa.calls) == 1


async def _raw(provider, body, path="/search"):
    return await provider.http.request_json("POST", f"https://api.exa.ai{path}", headers={}, json=body, timeout_s=5)


def test_unpriced_endpoints_and_hosts_are_refused(tmp_path):
    exa = Exa()
    provider, _, led = metered_exa(tmp_path, exa)
    with pytest.raises(BudgetExceededError):
        asyncio.run(_raw(provider, {"query": "x"}, path="/answer"))
    with pytest.raises(BudgetExceededError):
        asyncio.run(provider.http.request_json("GET", "https://example.com/", headers={}, timeout_s=5))
    assert exa.calls == [] and led.run("r")["refused_attempts"] == 2


class OpenRouter:
    def __init__(self, cost=0.0004):
        self.cost, self.calls = cost, []

    def __call__(self, request):
        self.calls.append(request)
        content = json.dumps({"page_type": "OTHER"})
        usage = {"prompt_tokens": 100, "completion_tokens": 10}
        if self.cost is not None:
            usage["cost"] = self.cost
        return httpx.Response(200, json={"id": "gen-1", "model": "m", "choices": [
            {"message": {"content": content}, "finish_reason": "stop"}], "usage": usage})


def metered_llm(tmp_path, handler, reservation="0.50", **kw):
    s = settings(tmp_path, openrouter_api_key="or-test-key", llm2_model="test/model", **kw)
    led = ledger(tmp_path)
    led.reserve("r", D(reservation))
    meter = RunMeter("r", D(reservation), UnitPrices.from_settings(s), led)
    token = CURRENT_METER.set(meter)
    try:
        llm = build_llm2(s, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    finally:
        CURRENT_METER.reset(token)
    return llm, meter, led, s


def test_llm_calls_are_bounded_by_request_size_and_max_completion_tokens(tmp_path):
    from pydantic import BaseModel

    class Out(BaseModel):
        page_type: str

    router = OpenRouter()
    llm, meter, led, s = metered_llm(tmp_path, router)
    asyncio.run(llm.complete_structured(system="s", user="u", output_model=Out, schema_name="out"))
    row = led.attempts("r")[0]
    size = len(router.calls[0].content)
    expected = D("0.000001") * (size + s.stage9_llm_token_overhead) + D("0.000004") * s.llm2_max_completion_tokens
    assert D(row["bound_usd"]) == expected and row["reported_usd"] == "0.0004" and row["outcome"] == "REPORTED"


def test_oversized_llm_requests_are_refused(tmp_path):
    from pydantic import BaseModel

    class Out(BaseModel):
        page_type: str

    router = OpenRouter()
    llm, _, _, _ = metered_llm(tmp_path, router, stage9_llm_max_request_bytes=1_000)
    with pytest.raises(BudgetExceededError):
        asyncio.run(llm.complete_structured(system="s", user="x" * 5_000, output_model=Out, schema_name="out"))
    assert router.calls == []


def test_openrouter_without_max_completion_tokens_is_refused(tmp_path):
    router = OpenRouter()
    llm, _, _, _ = metered_llm(tmp_path, router)
    with pytest.raises(BudgetExceededError):
        asyncio.run(llm.http.request_json("POST", "https://openrouter.ai/api/v1/chat/completions", headers={},
                                          json={"model": "m", "messages": []}, timeout_s=5))  # fmt: skip
    assert router.calls == []


# --------------------------------------------------------------------------- the guard on paid clients


def test_budget_mode_refuses_paid_clients_outside_a_budgeted_run(tmp_path):
    s = settings(tmp_path, openrouter_api_key="k", llm2_model="m")
    with pytest.raises(ProviderNotConfiguredError):
        build_provider("exa", s, httpx.AsyncClient(transport=httpx.MockTransport(Exa())))
    with pytest.raises(ProviderNotConfiguredError):
        build_llm2(s, httpx.AsyncClient(transport=httpx.MockTransport(OpenRouter())))
    off = make_settings(search_provider="exa", exa_api_key="k")
    assert active_meter(off, "exa") is None  # normal mode: unchanged behaviour
    build_provider("exa", off, httpx.AsyncClient(transport=httpx.MockTransport(Exa())))


def test_only_priced_providers_can_be_metered(tmp_path):
    s = settings(tmp_path, tavily_api_key="t")
    led = ledger(tmp_path)
    led.reserve("r", D("0.1"))
    token = CURRENT_METER.set(RunMeter("r", D("0.1"), UnitPrices.from_settings(s), led))
    try:
        with pytest.raises(BudgetExceededError):
            build_provider("tavily", s, httpx.AsyncClient(transport=httpx.MockTransport(Exa())))
    finally:
        CURRENT_METER.reset(token)


def test_standalone_paid_endpoints_are_refused_in_budget_mode(tmp_path):
    ledger(tmp_path)
    s = settings(tmp_path)
    with TestClient(create_app(s)) as client:
        r = client.post("/api/discovery/runs", json={"product_ids": ["SB-001"]})
        assert r.status_code >= 400 and "budget" in r.text.lower()
        r = client.post("/api/intelligence/runs", json={"product_ids": ["SB-001"]})
        assert r.status_code >= 400 and "budget" in r.text.lower()


# --------------------------------------------------------------------------- budgeted pipeline runs


def budgeted(catalogue, tmp_path, exa: Exa, *, ceiling="3.00", create_ledger=True, **kw):
    s = settings(tmp_path, **kw)
    if create_ledger:
        BudgetLedger.create(s.stage9_ledger_path, D(ceiling), "test")
    review = ReviewStore(s.review_db_path, s.discovery_excluded_competitor_domains)
    built = []

    def client():
        return httpx.AsyncClient(transport=httpx.MockTransport(exa))

    async def disc_factory(decisions):  # what build_discovery_agent does, with a mocked transport
        search = build_provider("exa", s, client())
        built.append(search)
        return CompetitorDiscoveryAgent(search, None, s, catalogue.competitors,
                                        [p.category for p in catalogue.products.values()],
                                        llm_disabled_reason="test: no LLM", review_decisions=decisions)  # fmt: skip

    async def intel_factory(targets, skipped):
        search = build_provider("exa", s, client())
        built.append(search)
        return ProductIntelligenceAgent(search, None, s, targets, skipped, llm_disabled_reason="test: no LLM")

    deps = PipelineDeps(s, catalogue, review, disc_factory, intel_factory)
    manager = BudgetedRunManager(InMemoryRunStore(s.pipeline_max_runs_retained, s.pipeline_run_ttl_minutes), deps)
    return manager, s, built


async def run_once(manager, **req):
    run = manager.start(RunRequest(**req))
    await manager.wait(run.run_id)
    await asyncio.sleep(0)  # let the done-callbacks (reconciliation) run
    return manager.store.get(run.run_id)


def test_a_budgeted_run_reserves_meters_and_reconciles(catalogue, tmp_path):
    exa = Exa(cost="0.005")
    manager, s, built = budgeted(catalogue, tmp_path, exa)
    run = asyncio.run(run_once(manager, product_ids=["SB-001"]))
    assert run.status in (PipelineRunStatus.COMPLETED, PipelineRunStatus.PARTIALLY_COMPLETED)
    led = BudgetLedger(s.stage9_ledger_path)
    entry = led.run(run.run_id)
    est = json.loads(entry["estimate_json"])
    assert D(entry["reserved_usd"]) == D(est["worst_case_usd"])  # reserved before the run, full worst case
    assert entry["status"] == RunStatus.RECONCILED, entry["notes"]
    attempts = led.attempts(run.run_id)
    assert len(attempts) == len(exa.calls) > 0 and all(a["outcome"] == "REPORTED" for a in attempts)
    assert D(entry["charged_exa_usd"]) == D("0.005") * len(exa.calls) == D(entry["pipeline_exa_usd"])
    assert led.summary().committed_usd == D(entry["charged_exa_usd"])


def test_the_per_run_cap_stops_paid_calls_and_the_run_still_ends(catalogue, tmp_path):
    exa = Exa(cost="0.005")
    manager, s, _ = budgeted(catalogue, tmp_path, exa, stage9_max_reservation_per_run_usd=D("0.05"))
    run = asyncio.run(run_once(manager, product_ids=["SB-001"]))
    assert run.status in (PipelineRunStatus.COMPLETED, PipelineRunStatus.PARTIALLY_COMPLETED)
    entry = BudgetLedger(s.stage9_ledger_path).run(run.run_id)
    assert D(entry["reserved_usd"]) == D("0.05") and entry["refused_attempts"] > 0
    assert D(entry["charged_exa_usd"]) == D("0.005") * len(exa.calls) <= D("0.05")
    # It stopped because one more worst-case search (0.01) would no longer fit.
    assert D("0.005") * len(exa.calls) + D("0.01") > D("0.05")


def test_a_run_that_does_not_fit_is_refused_before_anything_starts(catalogue, tmp_path):
    exa = Exa()
    manager, s, built = budgeted(catalogue, tmp_path, exa, ceiling="0.10")
    with pytest.raises(RunRequestError) as e:
        manager.start(RunRequest(product_ids=["SB-001"]))
    assert e.value.status_code == 402 and manager.store.list_runs() == [] and built == [] and exa.calls == []
    assert BudgetLedger(s.stage9_ledger_path).summary().committed_usd == 0


@pytest.mark.parametrize("kw, fragment", [
    ({"stage9_exa_usd_per_page_max": None}, "STAGE9_EXA_USD_PER_PAGE_MAX"),
    ({"create_ledger": False}, "ledger not found"),
])  # fmt: skip
def test_runs_are_refused_without_prices_or_a_ledger(catalogue, tmp_path, kw, fragment):
    manager, _, _ = budgeted(catalogue, tmp_path, Exa(), **kw)
    with pytest.raises(RunRequestError) as e:
        manager.start(RunRequest(product_ids=["SB-001"]))
    assert e.value.status_code == 503 and fragment in json.dumps(e.value.detail)
    assert manager.store.list_runs() == []


def test_pipeline_costs_reads_both_agents():
    from app.agents.discovery.schemas import DiscoveryUsage
    from app.agents.intelligence.schemas import IntelligenceUsage
    from app.orchestration.schemas import PipelineResult

    assert pipeline_costs(None) == (None, None)
    r = PipelineResult()
    assert pipeline_costs(r) == (None, None)
    r.intelligence_usage = IntelligenceUsage(search_cost_usd=D("0.02"), retrieval_cost_usd=D("0.003"),
                                             llm_cost_usd=D("0.001"))  # fmt: skip
    assert pipeline_costs(r) == (D("0.023"), D("0.001"))
    assert DiscoveryUsage  # imported for symmetry; discovery is covered by the budgeted-run test


# --------------------------------------------------------------------------- CLI


def test_cli_init_status_show_and_resolve(tmp_path, capsys):
    path = tmp_path / "l.sqlite3"
    assert cli.main(["init", "--ledger", str(path), "--ceiling", "3.00", "--note", "Stage 9"]) == 0
    assert cli.main(["init", "--ledger", str(path), "--ceiling", "9"]) == 1  # never overwritten
    BudgetLedger(path).reserve("r1", D("0.40"))
    assert cli.main(["status", "--ledger", str(path)]) == 0
    out = capsys.readouterr().out
    assert "remaining 2.60 USD" in out and "Open reservations: r1" in out
    assert cli.main(["show", "--ledger", str(path), "r1"]) == 0
    assert cli.main(["resolve", "--ledger", str(path), "r1", "--exa", "0.1", "--openrouter", "0", "--note", "x"]) == 0
    assert BudgetLedger(path).run("r1")["status"] == RunStatus.RESOLVED
