"""Stage 9.1 smoke-test preparation: fixed scope, $0.25 cap before every paid request, fail-closed
accounting, unresolved-run blocking. Offline: mocked Exa / OpenRouter transports, temporary ledger
and database, fake keys. Prices are the verified maxima or deliberately higher TEST values."""

import json
from decimal import Decimal

import httpx
import pytest

from app.agents.intelligence import ProductIntelligenceAgent
from app.orchestration.pipeline import RunRequestError
from app.orchestration.schemas import RunRequest
from app.services.llm import build_llm2
from app.services.search import build_provider
from app.stage9 import smoke
from app.stage9.budget import AttemptOutcome, BudgetLedger, UnitPrices
from app.stage9.context import CURRENT_METER
from app.stage9.isolated_db import PROTECTED_DIRS
from app.stage9.meter import BudgetExceededError, RunMeter
from tests.test_stage9_budget import Exa, budgeted, metered_exa, search
from tests.test_stage9_budget import ledger as make_ledger

D = Decimal
PRICES = ["--exa-search-usd", "0.007", "--exa-page-usd", "0.001", "--llm-input-usd-per-token", "0.00000025",
          "--llm-output-usd-per-token", "0.000001"]  # the verified maxima (23 Nov 2026)
FAKE = {"exa_api_key": "exa-test-key", "openrouter_api_key": "or-test-key", "llm2_model": "test/model",
        "search_provider": "exa"}


class Web:
    """Mock Exa (search hits on the requested domain, page text) and OpenRouter (unparseable output)."""

    def __init__(self):
        self.exa, self.llm = [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.host == "api.exa.ai":
            self.exa.append(body)
            if request.url.path == "/search":
                dom = body["includeDomains"][0]
                return httpx.Response(200, json={"requestId": "s", "costDollars": {"total": 0.007}, "results": [
                    {"url": f"https://www.{dom}/boss-rc-5-loop-station", "title": "Boss RC-5 Loop Station"}]})
            urls = body["urls"]
            return httpx.Response(200, json={"requestId": "c", "costDollars": {"total": 0.001 * len(urls)},
                                             "statuses": [{"id": u, "status": "success"} for u in urls],
                                             "results": [{"url": u, "id": u, "title": "Boss RC-5 Loop Station",
                                                          "text": "Boss RC-5 Loop Station. Price £219.00. In stock. "
                                                                  * 20} for u in urls]})  # fmt: skip
        self.llm.append(body)
        return httpx.Response(200, json={"id": "gen", "model": "m", "usage": {"prompt_tokens": 900,
                                         "completion_tokens": 50, "cost": 0.0002},
                                         "choices": [{"message": {"content": "not json"}, "finish_reason": "stop"}]})  # fmt: skip


def fake_providers(web):
    def prepare(app):
        deps = app.state.run_manager.d
        s = deps.settings

        def client():
            return httpx.AsyncClient(transport=httpx.MockTransport(web))

        async def intel(targets, skipped):
            return ProductIntelligenceAgent(build_provider("exa", s, client()), build_llm2(s, client()), s, targets,
                                            skipped)  # fmt: skip

        async def no_discovery(decisions):  # the smoke scope never runs discovery
            raise AssertionError("discovery must not run in the smoke test")

        deps.intelligence_factory, deps.discovery_factory = intel, no_discovery

    return prepare


def setup_dir(tmp_path):
    assert smoke.main(["setup", "--dir", str(tmp_path / "stage9")]) == 0
    return tmp_path / "stage9"


def test_setup_creates_a_3_dollar_ledger_and_an_empty_isolated_database(tmp_path):
    d = setup_dir(tmp_path)
    led = BudgetLedger(d / "ledger.sqlite3")
    assert led.ceiling_usd == D("3.00") and led.summary().committed_usd == 0
    assert (d / "review.sqlite3").is_file()
    assert smoke.main(["setup", "--dir", str(d)]) == 1  # never re-created or overwritten
    assert smoke.main(["setup", "--dir", str(PROTECTED_DIRS[0])]) == 1


def test_preflight_lists_every_blocker(tmp_path, capsys):
    d = tmp_path / "missing"
    code = smoke.main(["check", "--dir", str(d), "--exa-search-usd", "0.005", "--exa-page-usd", "0.001",
                       "--llm-input-usd-per-token", "0.0000001", "--llm-output-usd-per-token", "0.000001"],
                      settings_overrides=FAKE)  # fmt: skip
    out = json.loads(capsys.readouterr().out.split("\nNOT READY")[0])
    assert code == 2
    text = " ".join(out["problems"])
    assert "exa_search" in text and "llm_input" in text  # below the verified maxima
    assert "does not exist" in text and "ledger not found" in text.lower()


def test_check_passes_and_reserves_exactly_the_cap(tmp_path, capsys):
    d = setup_dir(tmp_path)
    capsys.readouterr()
    assert smoke.main(["check", "--dir", str(d), *PRICES], settings_overrides=FAKE) == 0
    report = json.loads(capsys.readouterr().out.split("\nPreflight passed")[0])
    assert report["problems"] == [] and report["scope"]["domains"] == ["andertons.co.uk", "gear4music.com"]
    wc = report["worst_case"]
    assert (wc["exa_searches"], wc["exa_pages"], wc["llm2_calls"]) == (2, 4, 8)
    assert D(wc["worst_case_usd"]) > D("0.25") and wc["reservation_usd"] == "0.25"


def test_check_refuses_a_wrong_ceiling_or_an_unresolved_run(tmp_path):
    d = tmp_path / "s9"
    BudgetLedger.create(d / "ledger.sqlite3", D("5.00"))
    smoke.create_empty(d / "review.sqlite3")
    assert smoke.main(["check", "--dir", str(d), *PRICES], settings_overrides=FAKE) == 2  # ceiling must be 3.00
    d2 = setup_dir(tmp_path)
    BudgetLedger(d2 / "ledger.sqlite3").reserve("crashed-run", D("0.10"))
    assert smoke.main(["check", "--dir", str(d2), *PRICES], settings_overrides=FAKE) == 2


def test_the_smoke_run_has_the_fixed_scope_and_stays_within_the_cap(tmp_path, capsys):
    d = setup_dir(tmp_path)
    web = Web()
    assert smoke.main(["run", "--dir", str(d), *PRICES], settings_overrides=FAKE, prepare_app=fake_providers(web)) == 0
    led = BudgetLedger(d / "ledger.sqlite3")
    (run_id,) = [p.name.split("-smoke-")[1] for p in (d / "runs").iterdir()]
    entry = led.run(run_id)
    assert entry["status"] == "RECONCILED" and D(entry["reserved_usd"]) == D("0.25")
    charged = D(entry["charged_exa_usd"]) + D(entry["charged_openrouter_usd"])
    assert charged <= D("0.25")
    # Scope: only the two domains were searched, one query each, no discovery.
    searched = [b["includeDomains"] for b in web.exa if "includeDomains" in b]
    assert sorted(x[0] for x in searched) == ["andertons.co.uk", "gear4music.com"]
    # Every Exa and OpenRouter HTTP attempt is in the ledger.
    attempts = led.attempts(run_id)
    assert len([a for a in attempts if a["outcome"] != "REFUSED"]) == len(web.exa) + len(web.llm) > 2
    manifest = json.loads(next((d / "runs").iterdir()).joinpath("manifest.json").read_text("utf-8"))
    assert manifest["request"] == {"product_ids": ["SB-001"], "run_discovery": False,
                                   "domains": ["andertons.co.uk", "gear4music.com"]}
    assert manifest["settings"]["stage9_max_reservation_per_run_usd"] == "0.25"


def test_the_cap_is_checked_before_every_paid_request(tmp_path):
    """With an LLM price so high that one attempt's worst case exceeds $0.25, no LLM request is sent."""
    d = setup_dir(tmp_path)
    web = Web()
    prices = PRICES[:-1] + ["0.0001"]  # output price x 8,000 tokens = $0.80 per attempt
    code = smoke.main(["run", "--dir", str(d), *prices], settings_overrides=FAKE, prepare_app=fake_providers(web))
    led = BudgetLedger(d / "ledger.sqlite3")
    (run_id,) = [p.name.split("-smoke-")[1] for p in (d / "runs").iterdir()]
    entry = led.run(run_id)
    assert web.llm == [] and entry["refused_attempts"] >= 1  # refused before sending, nothing spent
    assert D(entry["charged_openrouter_usd"] or 0) == 0
    assert D(entry["charged_exa_usd"]) + D(entry["charged_openrouter_usd"] or 0) <= D("0.25")
    assert code in (0, 3)


def test_an_accounting_failure_stops_all_further_paid_requests(tmp_path, monkeypatch):
    exa = Exa(cost="0.005")
    provider, meter, led = metered_exa(tmp_path, exa)
    asyncio_run = __import__("asyncio").run
    asyncio_run(search(provider))  # recorded normally

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(led, "record_attempt", broken)
    asyncio_run(search(provider))  # sent; its record fails -> accounting error
    assert meter.accounting_error and "disk full" in meter.accounting_error
    with pytest.raises(BudgetExceededError):
        asyncio_run(search(provider))
    assert len(exa.calls) == 2  # nothing sent after the failure


def test_unresolved_runs_block_new_runs(catalogue, tmp_path):
    manager, s, _ = budgeted(catalogue, tmp_path, Exa())
    BudgetLedger(s.stage9_ledger_path).reserve("interrupted", D("0.10"))
    with pytest.raises(RunRequestError) as e:
        manager.start(RunRequest(product_ids=["SB-001"]))
    assert e.value.status_code == 409 and "interrupted" in json.dumps(e.value.detail)


def test_a_run_with_an_accounting_error_needs_review(tmp_path):
    led = make_ledger(tmp_path)
    led.reserve("r", D("0.25"))
    led.record_attempt("r", "exa", "/search", D("0.007"), D("0.007"), D("0.007"), 200, AttemptOutcome.REPORTED)
    assert led.reconcile("r", D("0.007"), D("0"), ["accounting error during the run: OSError"]).value == "NEEDS_REVIEW"
    assert UnitPrices  # imported for completeness
    assert CURRENT_METER.get() is None and RunMeter
