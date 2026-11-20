"""Every operational path into product searches and price benchmarks: who is searched, and
who can be a benchmark, now that the separate UK gate is retired (Option B, 19 Nov 2026).

Rewritten from test_uk_gate_paths.py. It covers the same paths: eligible_search_targets
(configured + reviewer-approved), the pipeline (including run-scoped auto-approved domains
and `domains` subsets), the intelligence API and CLI, direct ProductIntelligenceAgent
construction, compare(), discovery's reported targets, and the matching API with tampered
payloads. The invariants protected:
- the 13 configured competitors follow their configured path with workbook types and flags;
- pending and rejected domains are never searched; approved directories are skipped;
- auto-approval needs verified UK evidence and never grants benchmark permission;
- with LLM 1 disabled, new domains stay pending and configured competitors are still searched;
- benchmark permission is only the workbook flag or the reviewer's explicit flag. No network.
"""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.agents.discovery.schemas import ClassificationBatch, SearchTarget
from app.agents.intelligence import ProductIntelligenceAgent, eligible_search_targets
from app.agents.intelligence import cli as intel_cli
from app.agents.intelligence.schemas import (
    IntelligenceMode,
    IntelligenceRunResult,
    IntelligenceUsage,
    ProductIntelligence,
    ProductSummary,
)
from app.main import create_app
from app.orchestration.pipeline import RunRequestError
from app.orchestration.schemas import RunRequest
from app.schemas.common import DomainApprovalStatus as DS
from app.schemas.common import MatchStatus as M
from app.schemas.competitor import CompanyType as T
from app.schemas.review import ReviewDecision
from app.services.matching.matcher import match_candidate
from app.services.review_store import ReviewStore
from tests.discovery_fakes import FakeLLM
from tests.intelligence_fakes import FakeIntelSearch
from tests.matching_fakes import NOW, candidate, target
from tests.pipeline_fakes import harness
from tests.search_fakes import make_settings
from tests.test_discovery_agent import CLASSIFICATIONS, PLAN
from tests.test_intelligence_agent import HITS, PAGES, dec
from tests.test_pipeline import IDS, searched_domains
from tests.uk_fakes import CONFIGURED_13


def by_domain(items):
    return {i.domain: i for i in items}


def without_uk_evidence(batch: ClassificationBatch) -> ClassificationBatch:
    return ClassificationBatch(companies=[c.model_copy(update={"uk_evidence": []}) for c in batch.companies])


# --------------------------------------------------------------------------- eligible_search_targets


def test_configured_competitors_follow_their_configured_path(catalogue):
    targets, skipped = eligible_search_targets(catalogue.competitors, [], make_settings())
    t = by_domain(targets)
    assert set(t) == set(CONFIGURED_13) and skipped == []
    for c in catalogue.competitors:  # workbook type and benchmark flag, unchanged
        assert t[c.domain].approval_status is DS.PRECONFIGURED
        assert (t[c.domain].company_type, t[c.domain].price_benchmark) == (c.company_type, c.price_benchmark)
    g, y = t["gibson.com"], t["yamahamusiclondon.com"]
    assert g.company_type is T.MANUFACTURER and g.price_benchmark is False
    assert y.company_type is T.MANUFACTURER and y.price_benchmark is True
    assert t["thomann.co.uk"].price_benchmark is True  # workbook flag (decision D5)


def test_decisions_on_configured_competitors_are_ignored(catalogue):
    targets, _ = eligible_search_targets(catalogue.competitors, [dec("gibson.com", ctype=T.RETAILER, bench=True),
                                                                 dec("andertons.co.uk", ReviewDecision.REJECTED)],
                                         make_settings())  # fmt: skip
    t = by_domain(targets)
    assert t["gibson.com"].price_benchmark is False and t["gibson.com"].company_type is T.MANUFACTURER
    assert t["andertons.co.uk"].approval_status is DS.PRECONFIGURED


def test_discovered_domains_need_explicit_approval(catalogue):
    decisions = [dec("approved.co.uk"), dec("rejected.co.uk", ReviewDecision.REJECTED),
                 dec("dir.co.uk", ctype=T.DIRECTORY)]  # fmt: skip
    targets, skipped = eligible_search_targets(catalogue.competitors, decisions, make_settings())
    t, k = by_domain(targets), by_domain(skipped)
    assert t["approved.co.uk"].approval_status is DS.APPROVED
    assert k["rejected.co.uk"].reason == "Rejected in review"
    assert "directory" in k["dir.co.uk"].reason
    assert "pending.co.uk" not in t and "pending.co.uk" not in k  # never decided: never searched


def test_target_selection_never_widens_benchmark_flags(catalogue):
    decisions = [dec("flagged.co.uk", bench=True), dec("unflagged.co.uk", bench=False),
                 dec("mkt.co.uk", ctype=T.MARKETPLACE)]  # fmt: skip
    t = by_domain(eligible_search_targets(catalogue.competitors, decisions, make_settings())[0])
    assert t["flagged.co.uk"].price_benchmark is True  # the reviewer's explicit flag
    assert t["unflagged.co.uk"].price_benchmark is False
    assert t["mkt.co.uk"].price_benchmark is False  # approved marketplace: searched, never a benchmark


# --------------------------------------------------------------------------- direct agent construction


def unchecked(domain="andertons.co.uk") -> SearchTarget:
    return SearchTarget(domain=domain, company_name=None, company_type=T.RETAILER, approval_status=DS.PRECONFIGURED,
                        price_benchmark=True)  # fmt: skip


def test_agent_searches_exactly_the_targets_it_is_given(catalogue):
    agent = ProductIntelligenceAgent(FakeIntelSearch(HITS, PAGES), None, make_settings(), [unchecked()])
    result = asyncio.run(agent.run([catalogue.products["SB-001"]]))
    assert [t.domain for t in result.search_targets] == ["andertons.co.uk"] and result.usage.search_requests > 0
    assert result.search_targets[0].price_benchmark is True  # carried unchanged


# --------------------------------------------------------------------------- compare()

AND_RC5 = "https://www.andertons.co.uk/boss-rc-5-loop-station-pedal"


def priced(url=AND_RC5, competitor=None, **kw):
    return candidate(url, "Boss RC-5 Loop Station", llm=True, product_title="Boss RC-5 Loop Station", brand="BOSS",
                     model="RC-5", price="189.00", quote="£189.00 In stock", competitor=competitor, **kw)  # fmt: skip


def compared(catalogue, competitor, url=AND_RC5):
    return match_candidate(catalogue.products["SB-001"], priced(url, competitor=competitor), NOW, 24)


def test_compare_needs_the_benchmark_flag(catalogue):
    ok = compared(catalogue, target())
    assert ok.match_status is M.EXACT_MATCH and ok.comparison.eligible
    unflagged = compared(catalogue, target(benchmark=False))
    assert not unflagged.comparison.eligible
    assert any("not marked as a price benchmark" in r for r in unflagged.comparison.reasons)
    assert not any("UK" in r for r in ok.comparison.reasons + unflagged.comparison.reasons)  # no UK gate reason


def test_gibson_is_never_a_benchmark(catalogue):
    g = target("gibson.com", ctype=T.MANUFACTURER, benchmark=False)
    m = compared(catalogue, g, "https://www.gibson.com/boss-rc-5")
    assert not m.comparison.eligible and any("not marked as a price benchmark" in r for r in m.comparison.reasons)


# --------------------------------------------------------------------------- discovery's reported targets


def test_discovery_reports_configured_approved_and_auto_approved_targets(catalogue):
    from tests.test_discovery_agent import make_agent

    agent, _, _ = make_agent(catalogue)
    result = asyncio.run(agent.run([catalogue.products["SB-001"]]))
    t = by_domain(result.search_targets)
    assert set(t) == {*CONFIGURED_13, "beckettsmusic.co.uk"}
    assert t["beckettsmusic.co.uk"].approval_status is DS.AUTO_APPROVED
    assert t["beckettsmusic.co.uk"].price_benchmark is False  # auto-approval never grants benchmark permission
    assert "ebay.co.uk" not in {q.domain for q in result.targeted_queries}  # pending: no targeted queries


# --------------------------------------------------------------------------- pipeline


def test_pipeline_searches_configured_competitors_by_default(catalogue, tmp_path):
    h = harness(catalogue, tmp_path)  # LLM 1 disabled
    run, result, events = asyncio.run(h.run(product_ids=IDS))
    assert set(CONFIGURED_13) <= {t.domain for t in result.search_targets}
    assert result.skipped_targets == []
    loaded = next(e for e in events if e.type == "known_domains_loaded")
    assert loaded.payload["configured"] == 13
    assert "uk_blocked" not in loaded.payload and "uk_exceptions" not in loaded.payload


def test_llm_disabled_new_domains_stay_pending_and_configured_are_still_searched(catalogue, tmp_path):
    h = harness(catalogue, tmp_path)
    _, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
    cands = result.discovery.candidates
    assert cands and all(c.approval_status is DS.APPROVAL_REQUIRED for c in cands if c.review_decision is None)
    assert all(c.uk_assessment is None for c in cands)  # nothing assessed, nothing invented
    searched = searched_domains(h)
    assert not searched & {c.domain for c in cands}
    assert {"andertons.co.uk", "gibson.com"} <= searched


def test_auto_approved_domain_is_searched_but_never_a_benchmark(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS))
    _, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
    beck = by_domain(result.discovery.candidates)["beckettsmusic.co.uk"]
    assert beck.approval_status is DS.AUTO_APPROVED and beck.uk_assessment.independent_sources >= 2
    assert "beckettsmusic.co.uk" in searched_domains(h)
    assert by_domain(result.search_targets)["beckettsmusic.co.uk"].price_benchmark is False


def test_without_verified_uk_evidence_a_domain_is_not_auto_approved(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, without_uk_evidence(CLASSIFICATIONS)))
    _, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
    beck = by_domain(result.discovery.candidates)["beckettsmusic.co.uk"]
    assert beck.approval_status is DS.APPROVAL_REQUIRED
    assert any("insufficient verified UK evidence" in r for r in beck.decision_reasons)
    assert "beckettsmusic.co.uk" in result.new_pending_domains
    assert "beckettsmusic.co.uk" not in searched_domains(h)


def test_pipeline_domains_subset_naming_an_unapproved_domain_is_422(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, decisions=[dec("rejected.co.uk", ReviewDecision.REJECTED)])
    with pytest.raises(RunRequestError) as e:
        h.manager.validate(RunRequest(product_ids=["SB-001"],
                                      domains=["andertons.co.uk", "pending.co.uk", "rejected.co.uk"]))  # fmt: skip
    assert e.value.status_code == 422 and e.value.detail["not_allowed"] == ["pending.co.uk", "rejected.co.uk"]
    assert "uk_blocked" not in e.value.detail
    h.manager.validate(RunRequest(product_ids=["SB-001"], domains=["andertons.co.uk", "thomann.co.uk"]))  # allowed


# --------------------------------------------------------------------------- intelligence API and CLI


def test_intelligence_api_refuses_pending_and_rejected_domains(tmp_path):
    s = make_settings(search_provider="exa", exa_api_key="k", review_db_path=tmp_path / "r.sqlite3")
    with TestClient(create_app(s)) as client:
        client.app.state.review_store.create_decision(dec("rejected.co.uk", ReviewDecision.REJECTED))
        r = client.post("/api/intelligence/runs", json={"product_ids": ["SB-001"],
                                                        "domains": ["rejected.co.uk", "pending.co.uk"]})  # fmt: skip
    assert r.status_code == 422 and r.json()["detail"]["not_allowed"] == ["pending.co.uk", "rejected.co.uk"]
    assert "uk_blocked" not in r.json()["detail"]


def test_intelligence_cli_uses_configured_and_approved_targets(catalogue, tmp_path, monkeypatch, capsys):
    s = make_settings(search_provider="exa", exa_api_key="k", review_db_path=tmp_path / "r.sqlite3")
    store = ReviewStore(s.review_db_path)
    store.create_decision(dec("approved.co.uk"))
    store.create_decision(dec("rejected.co.uk", ReviewDecision.REJECTED))
    seen = {}

    async def fake_build(settings, targets, skipped):
        seen.update(targets={t.domain for t in targets}, skipped={k.domain for k in skipped})
        raise intel_cli.ProviderError("exa", "stop here")

    monkeypatch.setattr(intel_cli, "Settings", lambda: s)
    monkeypatch.setattr(intel_cli, "build_intelligence_agent", fake_build)
    assert asyncio.run(intel_cli.main_async(["SB-001"], ["rejected.co.uk"])) == 2
    assert "rejected.co.uk" in capsys.readouterr().err
    assert asyncio.run(intel_cli.main_async(["SB-001"], None)) == 3
    assert seen["targets"] == {*CONFIGURED_13, "approved.co.uk"} and seen["skipped"] == {"rejected.co.uk"}


# --------------------------------------------------------------------------- matching API: tampered payloads


def payload(*cands) -> str:
    now = datetime.now(UTC)
    run = IntelligenceRunResult(
        run_id="intel-test", mode=IntelligenceMode.LLM_ENABLED, started_at=now, finished_at=now, search_provider="exa",
        search_targets=[], skipped_targets=[], usage=IntelligenceUsage(), warnings=[],
        products=[ProductIntelligence(product=ProductSummary(product_id="SB-001", product_name="RC-5", brand="BOSS",
                                                             manufacturer_model="RC-5"),
                                      queries=[], domains=[], candidates=list(cands), not_selected=[])],
    )  # fmt: skip
    return run.model_dump_json()


def fresh(url, competitor):
    # The API validates against the real clock; freshness is checked on the page's retrieval time.
    return priced(url, competitor, retrieved_at=datetime.now(UTC))


def evaluate(client, *cands):
    r = client.post("/api/matching/evaluate", content=payload(*cands), headers={"content-type": "application/json"})
    assert r.status_code == 200
    return r.json()


def test_matching_api_recomputes_benchmark_from_server_state(tmp_path):
    tampered_gibson = target("gibson.com", ctype=T.RETAILER, benchmark=True)
    tampered_pending = target("pending.co.uk", benchmark=True, status=DS.APPROVED)
    with TestClient(create_app(make_settings(review_db_path=tmp_path / "r.sqlite3"))) as client:
        body = evaluate(client, fresh("https://www.gibson.com/boss-rc-5", tampered_gibson),
                        fresh("https://pending.co.uk/boss-rc-5-loop-station-pedal", tampered_pending))  # fmt: skip
        cands = {c["competitor"]["domain"]: c for c in body["products"][0]["candidates"]}
        g = cands["gibson.com"]
        assert not g["comparison"]["eligible"] and g["competitor"]["price_benchmark"] is False
        assert g["competitor"]["company_type"] == "manufacturer"  # from the workbook, not the payload
        p = cands["pending.co.uk"]  # not approved on the server: never a benchmark
        assert not p["comparison"]["eligible"] and p["competitor"]["price_benchmark"] is False
        assert any("replaced" in w for w in body["warnings"])
        assert "uk_gate" not in json.dumps(body)

        ok = evaluate(client, fresh(AND_RC5, target("andertons.co.uk", benchmark=False)))
        c = ok["products"][0]["candidates"][0]
        assert c["comparison"]["eligible"] and c["competitor"]["price_benchmark"] is True  # server flag, not payload


def test_matching_api_refuses_a_page_on_another_domain(tmp_path):
    with TestClient(create_app(make_settings(review_db_path=tmp_path / "r.sqlite3"))) as client:
        body = evaluate(client, fresh("https://www.evil.example/boss-rc-5-loop-station-pedal",
                                      target("andertons.co.uk")))  # fmt: skip
    c = body["products"][0]["candidates"][0]
    assert not c["comparison"]["eligible"] and c["competitor"]["price_benchmark"] is False
    assert json.dumps(c).count("evil.example") >= 1
