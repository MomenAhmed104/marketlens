"""Review API (Phase 4) end to end: discovery in LLM_DISABLED mode -> review -> next run.

Uses the default discovery factory wiring (persisted decisions passed to the
agent, candidates recorded after the run) with a fake search provider.
"""

import pytest
from fastapi.testclient import TestClient

import app.api.discovery as discovery_api
from app.agents.discovery.agent import CompetitorDiscoveryAgent
from app.main import create_app
from tests.discovery_fakes import FakeSearch
from tests.search_fakes import make_settings
from tests.test_discovery_agent import GLOBAL_HITS, PROFILE_HITS
from tests.uk_fakes import all_eligible

RUN = "/api/discovery/runs"
REVIEW = "/api/review"


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def searches(monkeypatch):
    """Replace the real provider; keep the default factory so decision wiring is exercised."""
    created = []

    async def fake_build(settings, catalogue, review_decisions=None, *, uk_state):
        search = FakeSearch(GLOBAL_HITS, PROFILE_HITS)
        created.append(search)
        return CompetitorDiscoveryAgent(search, None, settings, catalogue.competitors,
                                        [p.category for p in catalogue.products.values()],
                                        llm_disabled_reason="OPENROUTER_API_KEY and LLM1_MODEL not set",
                                        review_decisions=review_decisions, uk_state=all_eligible())  # fmt: skip

    monkeypatch.setattr(discovery_api, "build_discovery_agent", fake_build)
    return created


@pytest.fixture
def client(settings, searches):
    with TestClient(create_app(settings)) as c:
        assert c.post(RUN, json={"product_ids": ["SB-001"]}).status_code == 200
        yield c


def approve(client, domain, **body):
    return client.post(f"{REVIEW}/candidates/{domain}/approve", json={"reviewer": "Alex", **body})


def statuses(client, **params):
    return {c["domain"]: c["review_status"] for c in client.get(f"{REVIEW}/candidates", params=params).json()}


# --------------------------------------------------------------------------- listing and evidence


def test_no_llm_candidates_are_pending_and_explicitly_unclassified(client):
    cands = client.get(f"{REVIEW}/candidates").json()
    assert {c["domain"] for c in cands} == {"beckettsmusic.co.uk", "ebay.co.uk", "guitarworld.com",
                                            "lookalike-music.co.uk"}  # fmt: skip
    for c in cands:
        assert c["review_status"] == "PENDING" and c["discovery_mode"] == "LLM_DISABLED"
        assert c["classification_status"] == "SKIPPED_LLM_DISABLED" and c["llm_classified"] is False
        # Nothing invented
        assert c["proposed_company_type"] is None and c["proposed_company_name"] is None
        assert c["confidence"] is None and c["relevance"] is None
        assert c["search_eligible"] is False and c["benchmark_eligible"] is False and c["decision"] is None
    ebay = next(c for c in cands if c["domain"] == "ebay.co.uk")
    assert ebay["known_marketplace"] is True


def test_candidate_detail_has_traceable_evidence(client):
    r = client.get(f"{REVIEW}/candidates/www.beckettsmusic.co.uk")  # hostname is normalised
    assert r.status_code == 200
    d = r.json()
    urls = {e["url"] for e in d["evidence"]}
    assert "https://beckettsmusic.co.uk/products/boss-rc-5" in urls
    assert "https://beckettsmusic.co.uk/pages/about" in urls  # profile evidence
    e = next(e for e in d["evidence"] if e["url"].endswith("boss-rc-5"))
    assert e["snippet"] and e["retrieved_at"] and e["provider"] == "fake-exa" and e["query_text"]
    assert d["history"] == [] and any("LLM classification skipped" in r for r in d["automatic_reasons"])


def test_unknown_domain_404(client):
    assert client.get(f"{REVIEW}/candidates/nowhere.co.uk").status_code == 404
    assert approve(client, "nowhere.co.uk", company_type="retailer").status_code == 404


# --------------------------------------------------------------------------- approve / reject rules


def test_approve_requires_an_explicit_company_type(client):
    r = approve(client, "beckettsmusic.co.uk")  # company_type omitted
    assert r.status_code == 422
    r = approve(client, "beckettsmusic.co.uk", company_type="retail-ish")
    assert r.status_code == 422


def test_unknown_type_can_be_approved_but_never_benchmarked(client):
    r = approve(client, "lookalike-music.co.uk", company_type=None, benchmark_eligible=True)
    assert r.status_code == 422 and "unknown" in r.json()["detail"]
    r = approve(client, "lookalike-music.co.uk", company_type=None, notes="Could not confirm type")
    assert r.status_code == 201
    d = r.json()
    assert d["decision"] == "APPROVED" and d["company_type"] is None and d["benchmark_eligible"] is False
    assert d["reviewer"] == "Alex" and d["decided_at"] and d["version"] == 1
    assert "https://lookalike-music.co.uk/boss-rc-5" in d["evidence_urls"]


def test_retailer_approval_with_benchmark(client):
    r = approve(client, "beckettsmusic.co.uk", company_type="specialist_retailer",
                company_name="Beckett's Music", benchmark_eligible=True)  # fmt: skip
    assert r.status_code == 201 and r.json()["benchmark_eligible"] is True
    c = client.get(f"{REVIEW}/candidates/beckettsmusic.co.uk").json()
    assert c["review_status"] == "APPROVED" and c["search_eligible"] and c["benchmark_eligible"]
    assert c["decision"]["company_name"] == "Beckett's Music"
    assert c["proposed_company_name"] is None  # the discovery proposal is not rewritten


@pytest.mark.parametrize("ctype", ["marketplace", "directory", "other"])
def test_non_benchmark_types_cannot_be_benchmarks(client, ctype):
    r = approve(client, "guitarworld.com", company_type=ctype, benchmark_eligible=True)
    assert r.status_code == 422 and "not a direct retailer" in r.json()["detail"]


def test_known_marketplace_must_be_approved_as_marketplace(client):
    for ctype in ("retailer", "specialist_retailer", None):
        r = approve(client, "ebay.co.uk", company_type=ctype)
        assert r.status_code == 422 and "known marketplace" in r.json()["detail"]
    r = approve(client, "ebay.co.uk", company_type="marketplace", benchmark_eligible=True)
    assert r.status_code == 422
    r = approve(client, "ebay.co.uk", company_type="marketplace")
    assert r.status_code == 201 and r.json()["benchmark_eligible"] is False


def test_reject(client):
    r = client.post(f"{REVIEW}/candidates/guitarworld.com/reject",
                    json={"reviewer": "Alex", "notes": "Review magazine", "company_type": "other"})  # fmt: skip
    assert r.status_code == 201 and r.json()["decision"] == "REJECTED"
    # A reject body cannot carry benchmark eligibility at all
    r = client.post(f"{REVIEW}/candidates/ebay.co.uk/reject", json={"reviewer": "A", "benchmark_eligible": True})
    assert r.status_code == 422


def test_blank_reviewer_rejected(client):
    assert approve(client, "beckettsmusic.co.uk", company_type="retailer", reviewer="   ").status_code == 422


def test_preconfigured_competitors_are_not_reviewable(client):
    for domain in ("andertons.co.uk", "www.gibson.com"):
        assert approve(client, domain, company_type="retailer").status_code == 409
        assert client.post(f"{REVIEW}/candidates/{domain}/reject", json={"reviewer": "A"}).status_code == 409


# --------------------------------------------------------------------------- conflicts and updates


def test_second_decision_conflicts_and_update_must_be_explicit(client):
    assert approve(client, "beckettsmusic.co.uk", company_type="retailer").status_code == 201
    r = client.post(f"{REVIEW}/candidates/beckettsmusic.co.uk/reject", json={"reviewer": "Sam"})
    assert r.status_code == 409 and r.json()["detail"]["current_decision"] == "APPROVED"

    r = client.put(f"{REVIEW}/decisions/beckettsmusic.co.uk",
                   json={"decision": "REJECTED", "reviewer": "Sam", "company_type": "retailer",
                         "notes": "Closed down"})  # fmt: skip
    assert r.status_code == 200 and r.json()["version"] == 2
    hist = client.get(f"{REVIEW}/candidates/beckettsmusic.co.uk").json()["history"]
    assert [(h["version"], h["decision"], h["reviewer"]) for h in hist] == [(1, "APPROVED", "Alex"),
                                                                           (2, "REJECTED", "Sam")]  # fmt: skip


def test_update_of_pending_domain_is_404(client):
    r = client.put(f"{REVIEW}/decisions/beckettsmusic.co.uk",
                   json={"decision": "APPROVED", "reviewer": "Sam", "company_type": "retailer"})  # fmt: skip
    assert r.status_code == 404


def test_update_applies_the_same_rules(client):
    assert approve(client, "ebay.co.uk", company_type="marketplace").status_code == 201
    r = client.put(f"{REVIEW}/decisions/ebay.co.uk",
                   json={"decision": "APPROVED", "reviewer": "Sam", "company_type": "retailer",
                         "benchmark_eligible": True})  # fmt: skip
    assert r.status_code == 422


def test_filters_and_decision_lists(client):
    approve(client, "beckettsmusic.co.uk", company_type="retailer")
    client.post(f"{REVIEW}/candidates/guitarworld.com/reject", json={"reviewer": "Alex"})
    assert statuses(client, status="APPROVED") == {"beckettsmusic.co.uk": "APPROVED"}
    assert statuses(client, status="REJECTED") == {"guitarworld.com": "REJECTED"}
    assert set(statuses(client, status="PENDING")) == {"ebay.co.uk", "lookalike-music.co.uk"}
    assert client.get(f"{REVIEW}/candidates", params={"status": "nope"}).status_code == 422
    assert [d["domain"] for d in client.get(f"{REVIEW}/decisions", params={"decision": "APPROVED"}).json()] == [
        "beckettsmusic.co.uk"]
    assert [d["domain"] for d in client.get(f"{REVIEW}/decisions", params={"decision": "REJECTED"}).json()] == [
        "guitarworld.com"]
    assert len(client.get(f"{REVIEW}/decisions").json()) == 2


# --------------------------------------------------------------------------- persistence and later runs


def test_decisions_persist_across_backend_restart(settings, searches):
    with TestClient(create_app(settings)) as c:
        c.post(RUN, json={"product_ids": ["SB-001"]})
        approve(c, "beckettsmusic.co.uk", company_type="specialist_retailer", benchmark_eligible=True)
    with TestClient(create_app(settings)) as c:  # new app instance, same database file
        assert statuses(c) == {"beckettsmusic.co.uk": "APPROVED", "ebay.co.uk": "PENDING",
                               "guitarworld.com": "PENDING", "lookalike-music.co.uk": "PENDING"}  # fmt: skip
        assert c.get(f"{REVIEW}/decisions").json()[0]["reviewer"] == "Alex"


def test_next_discovery_run_respects_decisions(client):
    approve(client, "beckettsmusic.co.uk", company_type="specialist_retailer", benchmark_eligible=True)
    approve(client, "ebay.co.uk", company_type="marketplace")
    client.post(f"{REVIEW}/candidates/guitarworld.com/reject", json={"reviewer": "Alex"})

    body = client.post(RUN, json={"product_ids": ["SB-001"]}).json()
    cands = {c["domain"]: c for c in body["candidates"]}
    assert cands["beckettsmusic.co.uk"]["approval_status"] == "APPROVED"
    assert cands["beckettsmusic.co.uk"]["review_decision"]["reviewer"] == "Alex"
    assert cands["beckettsmusic.co.uk"]["automatic_approval_status"] == "APPROVAL_REQUIRED"
    assert cands["guitarworld.com"]["approval_status"] == "REJECTED"
    assert cands["lookalike-music.co.uk"]["approval_status"] == "APPROVAL_REQUIRED"

    targeted = {q["domain"]: q for q in body["targeted_queries"]}
    assert targeted["beckettsmusic.co.uk"]["price_benchmark"] is True
    assert targeted["beckettsmusic.co.uk"]["approval_status"] == "APPROVED"
    assert targeted["ebay.co.uk"]["price_benchmark"] is False  # searched, never a retailer benchmark
    assert "guitarworld.com" not in targeted and "lookalike-music.co.uk" not in targeted
    assert targeted["gibson.com"]["price_benchmark"] is False  # unchanged reference
    assert len(body["known_competitors"]) == 13
    # The review queue still shows the human decision, not the run's automatic one.
    assert statuses(client)["beckettsmusic.co.uk"] == "APPROVED"
