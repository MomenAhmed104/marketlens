"""Phase 6 on a real Phase 5 result (no-LLM, live Exa run of 12 Nov 2026; snippets removed) and the API."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents.intelligence.schemas import IntelligenceRunResult
from app.main import create_app
from app.schemas.common import MatchStatus as M
from app.services.matching import evaluate_run
from app.services.matching.schemas import Condition, MatchingRunResult, PriceStatus
from tests.matching_fakes import NOW
from tests.search_fakes import make_settings

FIXTURE = Path(__file__).parent / "fixtures" / "phase5_no_llm_run.json"


@pytest.fixture(scope="module")
def run():
    return IntelligenceRunResult.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def result(run, catalogue):
    return evaluate_run(run, catalogue.products, 24, now=NOW)


def statuses(result, pid):
    pr = next(p for p in result.products if p.product_id == pid)
    return {m.product_url: m for m in pr.candidates}, pr


def test_real_no_llm_run_rc5(result):
    by_url, pr = statuses(result, "SB-001")
    exact = {u for u, m in by_url.items() if m.match_status is M.EXACT_MATCH}
    assert exact == {"https://www.andertons.co.uk/boss-rc-5-loop-station-pedal", "https://www.bopdj.com/boss-rc-5.html",
                     "https://www.promenademusic.co.uk/boss-rc-5-loop-pedal"}  # fmt: skip
    for u, m in by_url.items():
        if "Secondhand" in u:
            assert m.match_status is M.SIMILAR_MATCH and m.condition.condition is Condition.USED
        if "forum.gibson.com" in u or u.endswith("looper-pedal-guide"):
            assert m.match_status is M.NO_MATCH
    assert by_url["https://www.bopdj.com/music-production/beatboxing/loopers"].match_status is M.NO_MATCH
    assert pr.counts[M.EXACT_MATCH] == 3 and pr.comparable_prices == 0


def test_real_no_llm_run_fz5(result):
    by_url, _ = statuses(result, "SB-060")
    g4m = by_url["https://www.gear4music.com/Guitar-and-Bass/Boss-FZ-5-Fuzz-Effects-Pedal/7X8"]
    assert g4m.match_status is M.EXACT_MATCH  # "7X8" product code is not a speaker cabinet
    assert by_url["https://www.promenademusic.co.uk/boss-me-90-multi-effects-pedal"].match_status is M.NO_MATCH


def test_no_llm_run_never_compares_prices_or_invents_values(result):
    assert any("no LLM extraction" in w.lower() or "no llm extraction" in w.lower() for w in result.warnings)
    for pr in result.products:
        for m in pr.candidates:
            assert m.price.status is PriceStatus.NOT_EXTRACTED and m.price.price is None
            assert not m.comparison.eligible and m.comparison.difference_gbp is None
            # Every check cites evidence actually present in the Phase 5 record.
            for chk in [m.brand, m.model, *m.variants]:
                for ref in chk.evidence:
                    assert ref.text


def test_exact_matches_cite_two_evidence_groups(result):
    for pr in result.products:
        for m in pr.candidates:
            if m.match_status is M.EXACT_MATCH:
                assert {e.source.value for e in m.model.evidence} & {"page_title", "search_title"}
                assert any(e.source.value == "url" for e in m.model.evidence)


def test_result_round_trips(result):
    assert MatchingRunResult.model_validate_json(result.model_dump_json()) == result


# --------------------------------------------------------------------------- API


def test_api_evaluate(run):
    with TestClient(create_app(make_settings())) as client:
        r = client.post("/api/matching/evaluate", content=run.model_dump_json(),
                        headers={"content-type": "application/json"})  # fmt: skip
        assert r.status_code == 200
        body = r.json()
        assert body["source_run_id"] == run.run_id and body["source_mode"] == "LLM_DISABLED"
        sb1 = next(p for p in body["products"] if p["product_id"] == "SB-001")
        assert sb1["counts"]["EXACT_MATCH"] == 3 and sb1["comparable_prices"] == 0
        assert sb1["candidates"][0]["match_status"] == "EXACT_MATCH"  # EXACT first

        bad = json.loads(run.model_dump_json())
        bad["products"][0]["product"]["product_id"] = "SB-999"
        r = client.post("/api/matching/evaluate", json=bad)
        assert r.status_code == 404 and r.json()["detail"]["missing_ids"] == ["SB-999"]
        assert client.post("/api/matching/evaluate", json={"run_id": "x"}).status_code == 422
