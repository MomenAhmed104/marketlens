from fastapi.testclient import TestClient

from app.api.discovery import get_agent_factory
from app.main import create_app
from tests.discovery_fakes import FakeLLM, FakeSearch
from tests.search_fakes import make_settings
from tests.test_discovery_agent import CLASSIFICATIONS, GLOBAL_HITS, PLAN, PROFILE_HITS

URL = "/api/discovery/runs"


def test_missing_search_provider_returns_503_without_leaking_anything():
    app = create_app(make_settings(search_provider=None, openrouter_api_key="secret-or-key", llm1_model="m"))
    with TestClient(app) as client:
        r = client.post(URL, json={"product_ids": ["SB-001"]})
    assert r.status_code == 503
    assert "SEARCH_PROVIDER" in r.json()["detail"] and "secret-or-key" not in r.text


def test_validation_errors():
    app = create_app(make_settings())
    with TestClient(app) as client:
        assert client.post(URL, json={"product_ids": []}).status_code == 422
        assert client.post(URL, json={"product_ids": ["SB-001"] * 11}).status_code == 422
        r = client.post(URL, json={"product_ids": ["SB-001", "SB-999"]})
    assert r.status_code == 404 and r.json()["detail"]["missing_ids"] == ["SB-999"]


def test_run_with_injected_agent_and_clients_are_closed():
    from app.agents.discovery.agent import CompetitorDiscoveryAgent

    app = create_app(make_settings())
    created = {}

    def factory_dep():
        async def factory():
            search, llm = FakeSearch(GLOBAL_HITS, PROFILE_HITS), FakeLLM(PLAN, CLASSIFICATIONS)
            created.update(search=search, llm=llm)
            cat = app.state.catalogue
            return CompetitorDiscoveryAgent(search, llm, make_settings(), cat.competitors,
                                            [p.category for p in cat.products.values()])  # fmt: skip

        return factory

    app.dependency_overrides[get_agent_factory] = factory_dep
    with TestClient(app) as client:
        r = client.post(URL, json={"product_ids": ["SB-001"]})
    assert r.status_code == 200
    body = r.json()
    assert body["product_ids"] == ["SB-001"] and body["search_provider"] == "fake-exa"
    statuses = {c["domain"]: c["approval_status"] for c in body["candidates"]}
    assert statuses["beckettsmusic.co.uk"] == "AUTO_APPROVED" and statuses["ebay.co.uk"] == "APPROVAL_REQUIRED"
    assert body["candidates"][0]["evidence"][0]["url"].startswith("https://")
    assert created["search"].closed and created["llm"].closed
