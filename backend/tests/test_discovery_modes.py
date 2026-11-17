"""Discovery in LLM_ENABLED and LLM_DISABLED modes (no network, no keys)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.agents.discovery import build_discovery_agent, llm1_disabled_reason
from app.services.uk_gate import UkGateState
from app.agents.discovery.agent import CompetitorDiscoveryAgent
from app.agents.discovery.schemas import (
    ClassificationStatus,
    DiscoveryMode,
    EvidenceKind,
    QuerySource,
    UkSeller,
)
from app.api.discovery import get_agent_factory
from app.main import create_app
from app.schemas.common import DomainApprovalStatus as S
from app.services.llm import OpenRouterClient
from app.services.search.exa import ExaProvider
from app.services.search.http import ProviderNotConfiguredError
from tests.discovery_fakes import FakeLLM, FakeSearch
from tests.search_fakes import make_settings
from tests.test_discovery_agent import CLASSIFICATIONS, GLOBAL_HITS, PLAN, PROFILE_HITS
from tests.uk_fakes import all_eligible

SECRET = "sk-or-should-never-appear"
IDS = ("SB-001", "SB-028")


def run_mode(catalogue, llm):
    search = FakeSearch(GLOBAL_HITS, PROFILE_HITS)
    agent = CompetitorDiscoveryAgent(
        search, llm, make_settings(), catalogue.competitors,
        [p.category for p in catalogue.products.values()],
        llm_disabled_reason=None if llm else "OPENROUTER_API_KEY and LLM1_MODEL not set",
        uk_state=all_eligible(),  # explicit UK seeding
    )  # fmt: skip
    result = asyncio.run(agent.run([catalogue.products[i] for i in IDS]))
    asyncio.run(agent.aclose())
    return result, search


@pytest.fixture(scope="module")
def disabled(catalogue):
    return run_mode(catalogue, None)


@pytest.fixture(scope="module")
def enabled(catalogue):
    llm = FakeLLM(PLAN, CLASSIFICATIONS)
    result, search = run_mode(catalogue, llm)
    return result, search, llm


# --------------------------------------------------------------------------- LLM_DISABLED


def test_disabled_mode_is_explicit_and_uses_baseline_queries_only(disabled):
    result, search = disabled
    assert result.mode is DiscoveryMode.LLM_DISABLED
    assert result.llm_disabled_reason == "OPENROUTER_API_KEY and LLM1_MODEL not set"
    assert [(q.product_id, q.source, q.query.text) for q in result.global_queries] == [
        ("SB-001", QuerySource.BASELINE, "Boss RC-5 buy UK"),
        ("SB-028", QuerySource.BASELINE, "Epiphone Les Paul Standard '60s LH buy UK"),
    ]
    u = result.usage
    assert (u.llm_calls, u.llm_model, u.llm_prompt_tokens, u.llm_cost_usd) == (0, None, 0, None)
    # Real searches still ran: 2 global + 1 profile per candidate.
    assert u.search_requests == len(search.queries) == 2 + len(result.candidates)


def test_disabled_mode_never_invents_classification(disabled):
    result, _ = disabled
    assert result.candidates
    for c in result.candidates:
        assert c.classification_status is ClassificationStatus.SKIPPED_LLM_DISABLED
        assert c.approval_status is S.APPROVAL_REQUIRED and c.price_benchmark is False
        assert c.company_name is None and c.company_type is None
        assert c.relevance is None and c.confidence is None and c.rationale is None
        assert c.uk_seller is UkSeller.UNKNOWN and c.sells_new_musical_instruments is None
        assert c.relevant_categories == [] and c.cited_evidence_urls == [] and not c.name_verified
        assert c.decision_reasons[0].startswith("LLM classification skipped (LLM disabled)")
    assert not any(c.approval_status is S.AUTO_APPROVED for c in result.candidates)


def test_disabled_mode_still_records_evidence_and_deterministic_signals(disabled):
    result, _ = disabled
    cands = {c.domain: c for c in result.candidates}
    beck = cands["beckettsmusic.co.uk"]  # would be auto-approved with the LLM; strong evidence alone is not enough
    assert set(beck.uk_signals) >= {"uk_domain", "gbp_price", "uk_postcode"}
    assert beck.products_matched == ["SB-001", "SB-028"]
    assert {e.kind for e in beck.evidence} == {EvidenceKind.DISCOVERY_RESULT, EvidenceKind.DOMAIN_PROFILE}
    first = beck.evidence[0]
    assert first.url.startswith("https://beckettsmusic.co.uk/") and first.provider_request_id and first.retrieved_at
    # Grouping, dedup and exclusions work without the LLM.
    assert "andertons.co.uk" not in cands and "gibson.com" not in cands
    assert {k.domain for k in result.known_competitor_evidence} >= {"andertons.co.uk", "gibson.com"}
    excluded = {x.domain: x.reason for x in result.excluded}
    assert excluded["stompbox.co.uk"].startswith("Own site") and "youtube.com" in excluded


def test_disabled_mode_targets_known_competitors_only(disabled):
    result, _ = disabled
    assert len(result.known_competitors) == 13
    domains = {q.domain for q in result.targeted_queries}
    assert domains == {k.domain for k in result.known_competitors}
    assert len(result.targeted_queries) == 13 * len(IDS)  # baseline variant only
    assert all(q.source is QuerySource.BASELINE for q in result.targeted_queries)
    assert all(not q.price_benchmark for q in result.targeted_queries if q.domain == "gibson.com")


def test_disabled_mode_warnings_are_clear(disabled):
    result, _ = disabled
    joined = " | ".join(result.warnings)
    assert "LLM query planning skipped (OPENROUTER_API_KEY and LLM1_MODEL not set)" in joined
    assert "LLM classification skipped" in joined and "marked APPROVAL_REQUIRED" in joined


# --------------------------------------------------------------------------- LLM_ENABLED


def test_enabled_mode_classifies_and_applies_rules(enabled):
    result, _, llm = enabled
    assert result.mode is DiscoveryMode.LLM_ENABLED and result.llm_disabled_reason is None
    assert len(llm.calls) == 2 and result.usage.llm_calls == 2
    assert any(q.source is QuerySource.LLM for q in result.global_queries)
    statuses = {c.domain: (c.classification_status, c.approval_status) for c in result.candidates}
    assert statuses["beckettsmusic.co.uk"] == (ClassificationStatus.COMPLETED, S.AUTO_APPROVED)
    assert statuses["ebay.co.uk"] == (ClassificationStatus.COMPLETED, S.APPROVAL_REQUIRED)
    assert "beckettsmusic.co.uk" in {q.domain for q in result.targeted_queries}


def test_same_evidence_in_both_modes(disabled, enabled):
    """The LLM changes plans and decisions, not the baseline evidence collected."""
    d, _ = disabled
    e, _, _ = enabled
    base = lambda r: {(c.domain, x.url) for c in r.candidates for x in c.evidence  # noqa: E731
                      if x.query_text.endswith("buy UK")}  # fmt: skip
    assert base(d) == base(e)


# --------------------------------------------------------------------------- configuration


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({}, "OPENROUTER_API_KEY and LLM1_MODEL not set"),
        ({"openrouter_api_key": SECRET}, "LLM1_MODEL not set"),
        ({"llm1_model": "vendor/model"}, "OPENROUTER_API_KEY not set"),
        ({"openrouter_api_key": "   ", "llm1_model": "vendor/model"}, "OPENROUTER_API_KEY not set"),
        ({"openrouter_api_key": SECRET, "llm1_model": "vendor/model", "discovery_use_llm": False},
         "DISCOVERY_USE_LLM is false"),
        ({"openrouter_api_key": SECRET, "llm1_model": "vendor/model"}, None),
    ],
)  # fmt: skip
def test_llm1_disabled_reason(overrides, reason):
    assert llm1_disabled_reason(make_settings(**overrides)) == reason


def test_factory_selects_mode_and_never_needs_an_llm_key(catalogue):
    async def build(**kw):
        agent = await build_discovery_agent(make_settings(search_provider="exa", exa_api_key="exa-k", **kw), catalogue,
                                            uk_state=UkGateState.empty())  # fmt: skip
        info = (agent.mode, agent.llm_disabled_reason, type(agent.search), type(agent.llm))
        await agent.aclose()
        return info

    assert asyncio.run(build()) == (DiscoveryMode.LLM_DISABLED, "OPENROUTER_API_KEY and LLM1_MODEL not set",
                                    ExaProvider, type(None))  # fmt: skip
    assert asyncio.run(build(openrouter_api_key=SECRET, llm1_model="vendor/model")) == (
        DiscoveryMode.LLM_ENABLED, None, ExaProvider, OpenRouterClient)  # fmt: skip
    with pytest.raises(ProviderNotConfiguredError, match="SEARCH_PROVIDER"):
        asyncio.run(build_discovery_agent(make_settings(), catalogue, uk_state=UkGateState.empty()))


def test_app_starts_without_openrouter_key_and_reports_mode():
    app = create_app(make_settings(search_provider="exa", exa_api_key="exa-k"))
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert len(client.get("/api/competitors").json()) == 13
        cfg = client.get("/api/discovery/config").json()
    assert cfg["mode"] == "LLM_DISABLED" and cfg["llm1_model"] is None
    assert cfg["llm_disabled_reason"] == "OPENROUTER_API_KEY and LLM1_MODEL not set"
    assert cfg["thresholds"]["auto_approve_min_confidence"] == 0.85

    app = create_app(make_settings(search_provider="exa", exa_api_key="exa-k",
                                   openrouter_api_key=SECRET, llm1_model="vendor/model"))  # fmt: skip
    with TestClient(app) as client:
        r = client.get("/api/discovery/config")
    assert r.json()["mode"] == "LLM_ENABLED" and r.json()["llm1_model"] == "vendor/model"
    assert SECRET not in r.text and "exa-k" not in r.text


def test_api_run_in_disabled_mode(catalogue):
    app = create_app(make_settings())

    def factory_dep():
        async def factory():
            return CompetitorDiscoveryAgent(FakeSearch(GLOBAL_HITS, PROFILE_HITS), None, make_settings(),
                                            app.state.catalogue.competitors, ["Effects"],
                                            llm_disabled_reason="OPENROUTER_API_KEY and LLM1_MODEL not set",
                                            uk_state=all_eligible())  # fmt: skip

        return factory

    app.dependency_overrides[get_agent_factory] = factory_dep
    with TestClient(app) as client:
        r = client.post("/api/discovery/runs", json={"product_ids": ["SB-001"]})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "LLM_DISABLED"
    assert {c["approval_status"] for c in body["candidates"]} == {"APPROVAL_REQUIRED"}
    assert all(c["company_type"] is None and c["confidence"] is None for c in body["candidates"])
