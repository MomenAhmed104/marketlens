"""Every operational path into product searches and price benchmarks applies the UK gate.

Covers: eligible_search_targets (configured + reviewer-approved), the pipeline (incl.
run-scoped auto-approved domains and `domains` subsets), the intelligence API and CLI,
direct ProductIntelligenceAgent construction, compare(), discovery's reported targets,
and the matching API with tampered payloads. No network.
"""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.agents.discovery.schemas import SearchTarget
from app.agents.intelligence import ProductIntelligenceAgent, apply_uk_gate, eligible_search_targets
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
from app.services.uk_gate import UkGateState, gate_decision
from tests.intelligence_fakes import FakeIntelSearch
from tests.matching_fakes import candidate, target
from tests.pipeline_fakes import harness
from tests.search_fakes import make_settings
from tests.test_discovery_agent import CLASSIFICATIONS, PLAN
from tests.test_intelligence_agent import HITS, PAGES, dec
from tests.test_pipeline import IDS, searched_domains
from tests.discovery_fakes import FakeLLM
from tests.uk_fakes import CONFIGURED_13, S, all_eligible, seed_store, uk_exception, uk_record, uk_state

# The configured-competitor assessment (17 Nov 2026) with the user-approved exceptions, as test state.
ASSESSED_ELIGIBLE = ("andertons.co.uk", "langleyguitarcentre.co.uk", "themusicdept.com", "gear4music.com",
                     "the58sound.com", "perfectpitch.co.uk", "southlondonmusicshop.co.uk",
                     "yamahamusiclondon.com")  # fmt: skip
EXCEPTIONS = (uk_exception("guitarguitar.co.uk", search=True, benchmark=True),
              uk_exception("bax-shop.co.uk", search=True, benchmark=True),
              uk_exception("gibson.com", search=True, benchmark=False))  # fmt: skip
INSUFFICIENT = ("guitarguitar.co.uk", "bax-shop.co.uk", "gibson.com", "projectmusic.co.uk")


def assessed_state(extra_records=()) -> UkGateState:
    return uk_state(ASSESSED_ELIGIBLE, exceptions=EXCEPTIONS, records=[
        uk_record("thomann.co.uk", S.NOT_UK_ELIGIBLE),
        *(uk_record(d, S.INSUFFICIENT_EVIDENCE) for d in INSUFFICIENT), *extra_records])  # fmt: skip


def by_domain(items):
    return {i.domain: i for i in items}


# --------------------------------------------------------------------------- eligible_search_targets


def test_unassessed_configured_competitors_are_all_blocked(catalogue):
    targets, skipped = eligible_search_targets(catalogue.competitors, [], UkGateState.empty(), make_settings())
    assert targets == []
    assert set(by_domain(skipped)) == set(CONFIGURED_13)
    assert all(k.uk_gate.evidence_status is S.INSUFFICIENT_EVIDENCE and not k.uk_gate.assessed for k in skipped)


def test_configured_assessment_with_exceptions(catalogue):
    targets, skipped = eligible_search_targets(catalogue.competitors, [], assessed_state(), make_settings())
    t, k = by_domain(targets), by_domain(skipped)
    assert set(t) == {*ASSESSED_ELIGIBLE, "guitarguitar.co.uk", "bax-shop.co.uk", "gibson.com"}
    assert set(k) == {"thomann.co.uk", "projectmusic.co.uk"}
    assert "NOT_UK_ELIGIBLE" in k["thomann.co.uk"].reason
    assert "INSUFFICIENT_EVIDENCE" in k["projectmusic.co.uk"].reason  # unresolved domain identity: blocked
    # Exceptions never relabel the evidence status.
    for d in ("guitarguitar.co.uk", "bax-shop.co.uk", "gibson.com"):
        assert t[d].uk_gate.evidence_status is S.INSUFFICIENT_EVIDENCE and t[d].uk_gate.search_basis == "EXCEPTION"
    assert t["guitarguitar.co.uk"].uk_gate.benchmark_allowed and t["bax-shop.co.uk"].uk_gate.benchmark_allowed
    # Gibson: searched by exception, never a benchmark; its reference-only flag is untouched.
    g = t["gibson.com"]
    assert g.price_benchmark is False and g.uk_gate.benchmark_allowed is False and g.company_type is T.MANUFACTURER
    # Yamaha keeps its configured type and benchmark flag (an open decision; nothing reclassified).
    y = t["yamahamusiclondon.com"]
    assert y.company_type is T.MANUFACTURER and y.price_benchmark is True


def test_discovered_domains_need_approval_and_uk_eligibility(catalogue):
    decisions = [dec("approved-uk.co.uk"), dec("approved-unassessed.co.uk"), dec("approved-insufficient.co.uk"),
                 dec("rejected-uk.co.uk", ReviewDecision.REJECTED), dec("dir-uk.co.uk", ctype=T.DIRECTORY)]
    state = uk_state(["approved-uk.co.uk", "rejected-uk.co.uk", "dir-uk.co.uk", "pending-uk.co.uk"],
                     records=[uk_record("approved-insufficient.co.uk", S.INSUFFICIENT_EVIDENCE)])  # fmt: skip
    targets, skipped = eligible_search_targets(catalogue.competitors, decisions, state, make_settings())
    t, k = by_domain(targets), by_domain(skipped)
    assert "approved-uk.co.uk" in t and t["approved-uk.co.uk"].uk_gate.search_allowed
    assert "not assessed" in k["approved-unassessed.co.uk"].reason
    assert "INSUFFICIENT_EVIDENCE" in k["approved-insufficient.co.uk"].reason
    assert k["rejected-uk.co.uk"].reason == "Rejected in review" and k["rejected-uk.co.uk"].uk_gate is None
    assert "directory" in k["dir-uk.co.uk"].reason
    assert "pending-uk.co.uk" not in t and "pending-uk.co.uk" not in k  # UK eligible but never approved


def test_apply_uk_gate_never_changes_benchmark_flags():
    ts = [target("a.co.uk", benchmark=True, uk_eligible=False), target("b.co.uk", benchmark=False, uk_eligible=False)]
    allowed, _ = apply_uk_gate(ts, uk_state(["a.co.uk", "b.co.uk"]))
    assert [(t.domain, t.price_benchmark) for t in allowed] == [("a.co.uk", True), ("b.co.uk", False)]


# --------------------------------------------------------------------------- direct agent construction


def unchecked(domain="andertons.co.uk") -> SearchTarget:
    return SearchTarget(domain=domain, company_name=None, company_type=T.RETAILER, approval_status=DS.PRECONFIGURED,
                        price_benchmark=True)  # fmt: skip


def test_agent_drops_targets_that_never_passed_the_gate(catalogue):
    search = FakeIntelSearch(HITS, PAGES)
    agent = ProductIntelligenceAgent(search, None, make_settings(), [unchecked()])
    assert agent.targets == [] and "not checked" in agent.skipped_targets[0].reason
    result = asyncio.run(agent.run([catalogue.products["SB-001"]]))
    assert result.usage.search_requests == 0 and result.search_targets == []
    assert any("dropped before searching" in w for w in result.warnings)


def test_agent_recomputes_the_gate_and_ignores_a_faked_decision(catalogue):
    faked = target("andertons.co.uk", uk_eligible=True)  # claims UK_ELIGIBLE
    agent = ProductIntelligenceAgent(FakeIntelSearch(HITS, PAGES), None, make_settings(), [faked],
                                     uk_state=UkGateState.empty())  # fmt: skip
    assert agent.targets == [] and agent.skipped_targets[0].uk_gate.evidence_status is S.INSUFFICIENT_EVIDENCE
    asyncio.run(agent.aclose())


def test_agent_rejects_a_gate_decision_for_another_domain():
    other = unchecked("evil.example").model_copy(update={"uk_gate": gate_decision("andertons.co.uk",
                                                                                   uk_state(["andertons.co.uk"]))})
    agent = ProductIntelligenceAgent(FakeIntelSearch(HITS, PAGES), None, make_settings(), [other])
    assert agent.targets == []


def test_agent_keeps_allowed_targets_with_state(catalogue):
    agent = ProductIntelligenceAgent(FakeIntelSearch(HITS, PAGES), None, make_settings(), [unchecked()],
                                     uk_state=uk_state(["andertons.co.uk"]))  # fmt: skip
    result = asyncio.run(agent.run([catalogue.products["SB-001"]]))
    assert [t.domain for t in result.search_targets] == ["andertons.co.uk"] and result.usage.search_requests > 0


# --------------------------------------------------------------------------- compare()

AND_RC5 = "https://www.andertons.co.uk/boss-rc-5-loop-station-pedal"


def priced(url=AND_RC5, competitor=None, **kw):
    return candidate(url, "Boss RC-5 Loop Station", llm=True, product_title="Boss RC-5 Loop Station", brand="BOSS",
                     model="RC-5", price="189.00", quote="£189.00 In stock", competitor=competitor, **kw)  # fmt: skip


def compared(catalogue, competitor):
    from tests.matching_fakes import NOW

    return match_candidate(catalogue.products["SB-001"], priced(competitor=competitor), NOW, 24)


def test_compare_needs_uk_benchmark_clearance(catalogue):
    ok = compared(catalogue, target())
    assert ok.match_status is M.EXACT_MATCH and ok.comparison.eligible
    blocked = compared(catalogue, target(uk_eligible=False))
    assert not blocked.comparison.eligible and any("not assessed" in r for r in blocked.comparison.reasons)
    missing = compared(catalogue, target().model_copy(update={"uk_gate": None}))
    assert not missing.comparison.eligible and any("UK eligibility unknown" in r for r in missing.comparison.reasons)


def test_compare_respects_exception_scopes(catalogue):
    search_only = target().model_copy(update={"uk_gate": gate_decision("andertons.co.uk", uk_state(
        exceptions=[uk_exception("andertons.co.uk", search=True, benchmark=False)]))})  # fmt: skip
    assert not compared(catalogue, search_only).comparison.eligible
    both = target().model_copy(update={"uk_gate": gate_decision("andertons.co.uk", uk_state(
        exceptions=[uk_exception("andertons.co.uk", search=True, benchmark=True)]))})  # fmt: skip
    assert compared(catalogue, both).comparison.eligible


def test_gibson_is_never_a_benchmark_even_with_a_benchmark_exception(catalogue):
    g = target("gibson.com", ctype=T.MANUFACTURER, benchmark=False, uk_eligible=False).model_copy(update={
        "uk_gate": gate_decision("gibson.com", uk_state(exceptions=[uk_exception("gibson.com", True, True)]))})
    m = match_candidate(catalogue.products["SB-001"], priced("https://www.gibson.com/boss-rc-5", g),
                        __import__("tests.matching_fakes", fromlist=["NOW"]).NOW, 24)  # fmt: skip
    assert not m.comparison.eligible and any("not a retailer price benchmark" in r for r in m.comparison.reasons)


# --------------------------------------------------------------------------- discovery's reported targets


def test_discovery_reports_only_uk_allowed_targets(catalogue):
    from tests.test_discovery_agent import make_agent

    agent, _, _ = make_agent(catalogue)
    agent.uk_state = uk_state(["andertons.co.uk"], exceptions=[uk_exception("gibson.com")])
    result = asyncio.run(agent.run([catalogue.products["SB-001"]]))
    assert {t.domain for t in result.search_targets} == {"andertons.co.uk", "gibson.com"}
    assert {q.domain for q in result.targeted_queries} == {"andertons.co.uk", "gibson.com"}
    assert result.usage.search_requests > 0  # evidence-gathering searches still ran
    agent.uk_state = UkGateState.empty()
    assert agent.search_targets([]) == []


# --------------------------------------------------------------------------- pipeline


def test_pipeline_strict_default_searches_nothing(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, uk_eligible=())
    run, result, events = asyncio.run(h.run(product_ids=IDS))
    assert result.search_targets == [] and searched_domains(h) == set()
    assert {k.domain for k in result.skipped_targets} == set(CONFIGURED_13)
    loaded = next(e for e in events if e.type == "known_domains_loaded")
    assert loaded.payload["uk_blocked"] == 13 and loaded.payload["domains"] == []
    assert any("No eligible search targets" in w for w in run.warnings)


def test_pipeline_applies_assessment_and_exceptions(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, uk_eligible=ASSESSED_ELIGIBLE, uk_exceptions=EXCEPTIONS,
                uk_records=[uk_record("thomann.co.uk", S.NOT_UK_ELIGIBLE)])  # fmt: skip
    _, result, events = asyncio.run(h.run(product_ids=["SB-001"]))
    searched = searched_domains(h)
    assert {"guitarguitar.co.uk", "bax-shop.co.uk", "gibson.com", "andertons.co.uk"} <= searched
    assert not searched & {"thomann.co.uk", "projectmusic.co.uk"}
    loaded = next(e for e in events if e.type == "known_domains_loaded")
    assert set(loaded.payload["uk_exceptions"]) == {"guitarguitar.co.uk", "bax-shop.co.uk", "gibson.com"}
    for pr in result.products:
        for m in pr.matching.candidates:
            if m.competitor.domain == "gibson.com":
                assert not m.comparison.eligible


def test_auto_approved_domain_is_not_searched_until_uk_eligible(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS))
    _, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
    assert result.discovery.candidates and any(c.domain == "beckettsmusic.co.uk"
                                               and c.approval_status is DS.AUTO_APPROVED
                                               for c in result.discovery.candidates)  # fmt: skip
    assert "beckettsmusic.co.uk" not in searched_domains(h)
    k = by_domain(result.skipped_targets)["beckettsmusic.co.uk"]
    assert "not assessed" in k.reason and k.uk_gate is not None


def test_pipeline_domains_subset_naming_a_blocked_domain_is_422(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, uk_eligible=["andertons.co.uk"])
    with pytest.raises(RunRequestError) as e:
        h.manager.validate(RunRequest(product_ids=["SB-001"], domains=["andertons.co.uk", "thomann.co.uk"]))
    assert e.value.status_code == 422 and e.value.detail["not_allowed"] == ["thomann.co.uk"]
    assert "not assessed" in e.value.detail["uk_blocked"]["thomann.co.uk"]
    h.manager.validate(RunRequest(product_ids=["SB-001"], domains=["andertons.co.uk"]))  # allowed


# --------------------------------------------------------------------------- intelligence API and CLI


def test_intelligence_api_refuses_blocked_domains(tmp_path):
    with TestClient(create_app(make_settings(search_provider="exa", exa_api_key="k"))) as client:
        seed_store(client.app.state.review_store, ["andertons.co.uk"])
        r = client.post("/api/intelligence/runs", json={"product_ids": ["SB-001"], "domains": ["gear4music.com"]})
    assert r.status_code == 422 and r.json()["detail"]["not_allowed"] == ["gear4music.com"]
    assert "gear4music.com" in r.json()["detail"]["uk_blocked"]


def test_intelligence_cli_uses_the_gate(catalogue, tmp_path, monkeypatch, capsys):
    s = make_settings(search_provider="exa", exa_api_key="k", review_db_path=tmp_path / "r.sqlite3")
    seed_store(ReviewStore(s.review_db_path), ["andertons.co.uk"], [uk_exception("gibson.com")])
    seen = {}

    async def fake_build(settings, targets, skipped, uk):
        seen.update(targets=[t.domain for t in targets], skipped={k.domain for k in skipped}, uk=uk)
        raise intel_cli.ProviderError("exa", "stop here")

    monkeypatch.setattr(intel_cli, "Settings", lambda: s)
    monkeypatch.setattr(intel_cli, "build_intelligence_agent", fake_build)
    assert asyncio.run(intel_cli.main_async(["SB-001"], ["thomann.co.uk"])) == 2
    assert "thomann.co.uk" in capsys.readouterr().err
    assert asyncio.run(intel_cli.main_async(["SB-001"], None)) == 3
    assert sorted(seen["targets"]) == ["andertons.co.uk", "gibson.com"] and "thomann.co.uk" in seen["skipped"]
    assert set(seen["uk"].eligibility) == {"andertons.co.uk"}


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


def test_matching_api_recomputes_benchmark_and_uk_eligibility(tmp_path):
    tampered_uk = target("andertons.co.uk", uk_eligible=True)  # claims UK_ELIGIBLE; server has no record
    tampered_gibson = target("gibson.com", ctype=T.RETAILER, benchmark=True, uk_eligible=True)
    with TestClient(create_app(make_settings())) as client:
        store = client.app.state.review_store
        store.put_uk_exception(uk_exception("gibson.com", search=True, benchmark=True))  # even a benchmark exception
        body = evaluate(client, fresh(AND_RC5, tampered_uk), fresh("https://www.gibson.com/boss-rc-5", tampered_gibson))
        cands = {c["competitor"]["domain"]: c for c in body["products"][0]["candidates"]}
        assert not cands["andertons.co.uk"]["comparison"]["eligible"]
        assert cands["andertons.co.uk"]["competitor"]["uk_gate"]["evidence_status"] == "INSUFFICIENT_EVIDENCE"
        g = cands["gibson.com"]
        assert not g["comparison"]["eligible"] and g["competitor"]["price_benchmark"] is False
        assert g["competitor"]["company_type"] == "manufacturer"  # from the workbook, not the payload
        assert any("replaced" in w for w in body["warnings"])

        seed_store(store, ["andertons.co.uk"])
        ok = evaluate(client, fresh(AND_RC5, target("andertons.co.uk", benchmark=False, uk_eligible=False)))
        c = ok["products"][0]["candidates"][0]
        assert c["comparison"]["eligible"] and c["competitor"]["price_benchmark"] is True  # server flag, not payload


def test_matching_api_refuses_a_page_on_another_domain(tmp_path):
    with TestClient(create_app(make_settings())) as client:
        seed_store(client.app.state.review_store, ["andertons.co.uk"])
        body = evaluate(client, fresh("https://www.evil.example/boss-rc-5-loop-station-pedal",
                                      target("andertons.co.uk", uk_eligible=True)))  # fmt: skip
    c = body["products"][0]["candidates"][0]
    assert not c["comparison"]["eligible"] and c["competitor"]["price_benchmark"] is False
    assert json.dumps(c).count("evil.example") >= 1
