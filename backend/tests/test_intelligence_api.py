"""Product intelligence API (Phase 5): config, validation, domain filtering, modes."""

import asyncio

import httpx
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.agents.intelligence import ProductIntelligenceAgent, build_intelligence_agent, llm2_disabled_reason
from app.api.intelligence import get_agent_factory
from app.main import create_app
from app.schemas.competitor import CompanyType as T
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.llm import LLMNotConfiguredError, build_llm2
from app.services.search.http import ProviderNotConfiguredError
from tests.intelligence_fakes import FakeExtractLLM, FakeIntelSearch
from tests.search_fakes import make_settings
from tests.test_intelligence_agent import EXTRACTIONS, HITS, PAGES
from tests.store_fakes import CONFIGURED_13

RUN = "/api/intelligence/runs"
SECRET = "sk-or-never-shown"
NOW = datetime(2026, 11, 12, tzinfo=UTC)


def dec(domain, decision=ReviewDecision.APPROVED, ctype=T.RETAILER, bench=False):
    return DomainDecisionRecord(domain=domain, decision=decision, company_type=ctype, benchmark_eligible=bench,
                                reviewer="Alex", decided_at=NOW)  # fmt: skip


def app_with(settings=None, llm=True, decisions=()):
    app = create_app(settings or make_settings())
    created = {}

    def factory_dep():
        async def factory(targets, skipped):
            search = FakeIntelSearch(HITS, PAGES)
            model = FakeExtractLLM(EXTRACTIONS) if llm else None
            created.update(search=search, llm=model, targets=targets)
            return ProductIntelligenceAgent(search, model, app.state.settings, targets, skipped,
                                            llm_disabled_reason=None if llm else "OPENROUTER_API_KEY and LLM2_MODEL not set")  # fmt: skip

        return factory

    app.dependency_overrides[get_agent_factory] = factory_dep
    return app, created, list(decisions)


@pytest.fixture
def client_and_state():
    app, created, decisions = app_with(decisions=[
        dec("beckettsmusic.co.uk", ctype=T.SPECIALIST_RETAILER, bench=True),
        dec("guitarworld.com", ReviewDecision.REJECTED, T.OTHER),
    ])
    with TestClient(app) as c:
        for d in decisions:
            app.state.review_store.create_decision(d)
        yield c, created


def test_run_returns_structured_candidates(client_and_state):
    client, created = client_and_state
    r = client.post(RUN, json={"product_ids": ["SB-001"]})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "LLM_ENABLED" and body["search_provider"] == "fake-exa"
    targets = {t["domain"] for t in body["search_targets"]}
    assert "beckettsmusic.co.uk" in targets and "guitarworld.com" not in targets and len(targets) == 14
    assert {s["domain"] for s in body["skipped_targets"]} == {"guitarworld.com"}
    cands = {c["product_url"]: c for c in body["products"][0]["candidates"]}
    a = cands["https://www.andertons.co.uk/boss-rc-5-loop-station"]
    assert a["competitor"]["domain"] == "andertons.co.uk" and a["competitor"]["price_benchmark"] is True
    assert a["price"] == "189.00" and a["currency"] == "GBP" and a["extraction_status"] == "COMPLETED"
    assert a["search"]["url"] and a["page"]["status"] == "OK" and a["field_evidence"]
    assert "content" not in a["page"]  # no raw page text in the API response
    assert body["usage"]["llm_calls"] >= 1 and created["search"].closed and created["llm"].closed


def test_domain_subset_must_be_approved(client_and_state):
    client, created = client_and_state
    r = client.post(RUN, json={"product_ids": ["SB-001"], "domains": ["guitarworld.com", "pending-shop.co.uk"]})
    assert r.status_code == 422 and r.json()["detail"]["not_allowed"] == ["guitarworld.com", "pending-shop.co.uk"]
    r = client.post(RUN, json={"product_ids": ["SB-001"], "domains": ["www.andertons.co.uk", "beckettsmusic.co.uk"]})
    assert r.status_code == 200
    assert {t.domain for t in created["targets"]} == {"andertons.co.uk", "beckettsmusic.co.uk"}
    assert {q.domains[0] for q in created["search"].queries} == {"andertons.co.uk", "beckettsmusic.co.uk"}


def test_request_validation(client_and_state):
    client, _ = client_and_state
    assert client.post(RUN, json={"product_ids": []}).status_code == 422
    assert client.post(RUN, json={"product_ids": ["SB-001"] * 6}).status_code == 422
    r = client.post(RUN, json={"product_ids": ["SB-001", "SB-999"]})
    assert r.status_code == 404 and r.json()["detail"]["missing_ids"] == ["SB-999"]
    assert client.post(RUN, json={"product_ids": ["SB-001"], "domains": ["not a domain!"]}).status_code == 422


def test_llm_disabled_run_through_api():
    app, _, _ = app_with(llm=False)
    with TestClient(app) as client:
        body = client.post(RUN, json={"product_ids": ["SB-001"]}).json()
    assert body["mode"] == "LLM_DISABLED"
    for c in body["products"][0]["candidates"]:
        assert c["price"] is None and c["product_title"] is None
        assert c["extraction_status"] in ("SKIPPED_NO_LLM", "SKIPPED_NOT_RETRIEVED")


# --------------------------------------------------------------------------- configuration


def test_config_without_llm_and_without_secrets():
    s = make_settings(search_provider="exa", exa_api_key="exa-k", openrouter_api_key=SECRET, llm1_model="a/b")
    with TestClient(create_app(s)) as client:
        r = client.get("/api/intelligence/config")
    body = r.json()
    assert body["mode"] == "LLM_DISABLED" and body["llm_disabled_reason"] == "LLM2_MODEL not set"
    assert body["llm2_model"] is None and SECRET not in r.text and "exa-k" not in r.text
    # The 13 configured competitors follow their configured path: no UK gate, nothing skipped.
    assert {t["domain"] for t in body["search_targets"]} == set(CONFIGURED_13) and body["skipped_targets"] == []
    assert "uk_gate" not in r.text


def test_config_keeps_configured_types_and_workbook_benchmark_flags(tmp_path):
    s = make_settings(search_provider="exa", exa_api_key="exa-k", review_db_path=tmp_path / "r.sqlite3")
    with TestClient(create_app(s)) as client:
        store = client.app.state.review_store
        for d in (dec("beckettsmusic.co.uk"), dec("ebay.co.uk", ReviewDecision.REJECTED, T.MARKETPLACE)):
            store.create_decision(d)
        body = client.get("/api/intelligence/config").json()
    targets = {t["domain"]: t for t in body["search_targets"]}
    assert set(targets) == {*CONFIGURED_13, "beckettsmusic.co.uk"}  # rejected never searched
    assert all(targets[d]["approval_status"] == "PRECONFIGURED" for d in CONFIGURED_13)
    assert targets["gibson.com"]["company_type"] == "manufacturer" and targets["gibson.com"]["price_benchmark"] is False
    yamaha = targets["yamahamusiclondon.com"]
    assert yamaha["company_type"] == "manufacturer" and yamaha["price_benchmark"] is True
    assert targets["thomann.co.uk"]["price_benchmark"] is True  # workbook flag (decision D5)
    # Approved without the reviewer's explicit benchmark flag: searched, never a benchmark.
    assert targets["beckettsmusic.co.uk"]["approval_status"] == "APPROVED"
    assert targets["beckettsmusic.co.uk"]["price_benchmark"] is False
    assert {k["domain"]: k["reason"] for k in body["skipped_targets"]} == {"ebay.co.uk": "Rejected in review"}


def test_config_with_llm2():
    s = make_settings(search_provider="exa", exa_api_key="exa-k", openrouter_api_key=SECRET, llm2_model="v/m2")
    with TestClient(create_app(s)) as client:
        r = client.get("/api/intelligence/config")
    assert r.json()["mode"] == "LLM_ENABLED" and r.json()["llm2_model"] == "v/m2" and SECRET not in r.text


@pytest.mark.parametrize("overrides,reason", [
    ({}, "OPENROUTER_API_KEY and LLM2_MODEL not set"),
    ({"openrouter_api_key": "k"}, "LLM2_MODEL not set"),
    ({"llm2_model": "m"}, "OPENROUTER_API_KEY not set"),
    ({"openrouter_api_key": "k", "llm2_model": "m"}, None),
    ({"openrouter_api_key": "k", "llm2_model": "m", "intelligence_use_llm": False}, "INTELLIGENCE_USE_LLM is false"),
    ({"openrouter_api_key": "k", "llm1_model": "m"}, "LLM2_MODEL not set"),  # LLM 1 config does not enable LLM 2
    ({"openrouter_api_key": "k", "llm2_model": "   "}, "LLM2_MODEL not set"),  # blank value in .env
    ({"openrouter_api_key": "  ", "llm2_model": "m"}, "OPENROUTER_API_KEY not set"),
])  # fmt: skip
def test_llm2_disabled_reason(overrides, reason):
    assert llm2_disabled_reason(make_settings(**overrides)) == reason


def test_build_llm2_errors_never_leak_keys():
    with pytest.raises(LLMNotConfiguredError, match="OPENROUTER_API_KEY"):
        build_llm2(make_settings(llm2_model="m"))
    with pytest.raises(LLMNotConfiguredError, match="LLM2_MODEL") as e:
        build_llm2(make_settings(openrouter_api_key=SECRET, llm1_model="m1"))
    assert SECRET not in str(e.value)
    llm = build_llm2(make_settings(openrouter_api_key=SECRET, llm2_model="v/m2", llm2_max_completion_tokens=1234))
    assert llm.model == "v/m2" and llm.max_completion_tokens == 1234
    asyncio.run(llm.aclose())


def test_llm2_is_ready_for_activation_over_openrouter(catalogue):
    """With OPENROUTER_API_KEY + LLM2_MODEL set, the real client sends a strict-schema request
    for LLM2_MODEL (not LLM1_MODEL) and the agent turns the reply into checked fields."""
    from app.agents.intelligence import ProductIntelligenceAgent, eligible_search_targets
    from tests.search_fakes import Recorder, ok
    from tests.test_intelligence_agent import AND, EXTRACTIONS, run

    reply = {"id": "gen-1", "model": "v/m2", "usage": {"prompt_tokens": 900, "completion_tokens": 150, "cost": 0.0021},
             "choices": [{"finish_reason": "stop", "message": {"content": EXTRACTIONS[AND].model_dump_json()}}]}  # fmt: skip
    from tests.intelligence_fakes import proposal

    match = proposal(reasons=[("Same model", "Model: RC-5")]).model_dump_json()
    match_reply = {"id": "gen-2", "model": "v/m2", "usage": {"prompt_tokens": 700, "completion_tokens": 90, "cost": 0.001},
                   "choices": [{"finish_reason": "stop", "message": {"content": match}}]}  # fmt: skip
    rec = Recorder(ok(reply), ok(match_reply))
    s = make_settings(openrouter_api_key=SECRET, llm1_model="v/m1", llm2_model="v/m2", llm2_temperature=0.0)
    llm = build_llm2(s, client=httpx.AsyncClient(transport=httpx.MockTransport(rec)))
    targets, _ = eligible_search_targets(catalogue.competitors, [], s)
    targets = [t for t in targets if t.domain == "andertons.co.uk"]
    hits = {"andertons.co.uk": HITS["andertons.co.uk"][:1]}
    result = run(ProductIntelligenceAgent(FakeIntelSearch(hits, PAGES), llm, s, targets), catalogue)
    body = rec.body(0)
    assert body["model"] == "v/m2" and body["response_format"]["json_schema"]["name"] == "page_extraction"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert rec.requests[0].headers["authorization"] == f"Bearer {SECRET}"  # sent only to the provider
    (c,) = result.products[0].candidates
    assert c.extraction_status.value == "COMPLETED" and str(c.price) == "189.00" and c.currency == "GBP"
    assert result.usage.llm_cost_usd is not None and result.usage.llm_prompt_tokens == 900 + 700
    # Second call: Task A match proposal with its own strict schema, same model (P-1).
    second = rec.body(1)
    assert second["model"] == "v/m2" and second["response_format"]["json_schema"]["name"] == "match_proposal"
    assert second["response_format"]["json_schema"]["strict"] is True
    assert c.match_proposal_status.value == "COMPLETED" and c.match_proposal.classification.value == "EXACT_MATCH"
    assert result.usage.llm_match_calls == 1 and len(rec.requests) == 2
    assert SECRET not in result.model_dump_json()


def test_factory_modes(catalogue):
    async def build(**kw):
        agent = await build_intelligence_agent(make_settings(search_provider="exa", exa_api_key="e", **kw), [], None)
        await agent.aclose()
        return agent

    assert asyncio.run(build()).mode.value == "LLM_DISABLED"
    assert asyncio.run(build(openrouter_api_key="k", llm2_model="m")).mode.value == "LLM_ENABLED"


def test_missing_search_provider_is_503_without_leaking():
    s = make_settings(search_provider=None, openrouter_api_key=SECRET, llm2_model="m")
    with TestClient(create_app(s)) as client:
        r = client.post(RUN, json={"product_ids": ["SB-001"]})
    assert r.status_code == 503 and "SEARCH_PROVIDER" in r.json()["detail"] and SECRET not in r.text

    async def build():
        await build_intelligence_agent(make_settings(search_provider="exa"), [], None)

    with pytest.raises(ProviderNotConfiguredError, match="EXA_API_KEY"):
        asyncio.run(build())
