"""Stage 9.0 evaluation preparation: answer key, isolated databases, harness, scoring.

Offline only: no network (conftest blocks it), no provider keys beyond fake test values, a
temporary ledger with made-up TEST prices, temporary review databases.
"""

import copy
import hashlib
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.agents.discovery.agent import CompetitorDiscoveryAgent
from app.agents.intelligence import ProductIntelligenceAgent
from app.schemas.competitor import CompanyType
from app.schemas.review import ReviewDecision
from app.services.review_store import ReviewStore
from app.services.search import build_provider
from app.stage9 import isolated_db
from app.stage9.budget import BudgetLedger
from app.stage9.evaluation import (
    DEFAULT_ANSWER_KEY,
    AnswerKey,
    Label,
    check_against_catalogue,
    has_term,
    load_answer_key,
)
from app.stage9.harness import HarnessError, ScenarioRun, load_artifacts, run_scenario
from app.stage9.isolated_db import Approval, IsolationError
from app.stage9.scoring import (
    CandidateVerification,
    IdentityVerdict,
    Relevance,
    Verifications,
    render_markdown,
    rules_verdict,
    score_run,
)
from tests.search_fakes import make_settings
from tests.test_stage9_budget import TEST_PRICES, Exa

D = Decimal
FIXTURE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "test" / "fixtures" / "llm-run.json"


@pytest.fixture(scope="module")
def key() -> AnswerKey:
    return load_answer_key()


# --------------------------------------------------------------------------- scenario and answer key


def test_the_scenario_matches_the_workbook(key, catalogue):
    assert check_against_catalogue(key, catalogue) == []
    ids = [p.product_id for p in key.scenario]
    assert ids[:5] == ["SB-001", "SB-028", "SB-017", "SB-030", "SB-090"] and len(ids) == 6
    # Approved 23 Nov 2026 as a hypothesis-based specification, not verified market truth.
    assert key.status.startswith("APPROVED") and "hypothesis-based" in key.status


def test_price_scoring_exclusions(key):
    # SB-028: unconfirmed baseline. SB-001: baseline kept as historical reference only (user, 23 Nov 2026).
    assert key.entry("SB-028").price_scoring_eligible is False
    assert key.entry("SB-001").price_scoring_eligible is False
    assert "historical reference only" in key.entry("SB-001").price_scoring_note
    assert all(key.entry(p).price_scoring_eligible for p in ("SB-017", "SB-030", "SB-090", "SB-055"))


def test_retailers_are_never_promised_as_verified_current_listings(key):
    for e in key.entries:
        for r in e.retailers:
            assert r.label is not Label.VERIFIED, (e.product_id, r.domain)
    # Historical evidence is named as such.
    sb001 = {r.domain: r for r in key.entry("SB-001").retailers}
    assert "Historical" in sb001["andertons.co.uk"].basis and "20 Nov 2026" in sb001["andertons.co.uk"].basis


def test_planned_runs_respect_the_product_limit(key):
    assert all(len(r.product_ids) <= 5 for r in key.planned_runs)
    covered = {pid for r in key.planned_runs for pid in r.product_ids}
    assert covered == {p.product_id for p in key.scenario}


def test_verified_claims_need_a_source():
    with pytest.raises(ValueError):
        AnswerKey.model_validate({**json.loads(DEFAULT_ANSWER_KEY.read_text("utf-8")),
                                  "entries": [{"product_id": "SB-001", "identity": {"text": "x", "label": "VERIFIED"},
                                               "exact_requires": [], "price_scoring_eligible": True,
                                               "price_scoring_note": "", "evidence_required": []}]})


@pytest.mark.parametrize("text, term, hit", [
    ("https://www.andertons.co.uk/boss-rc-5-loop-station-pedal", "rc 5", True),
    ("Boss RC-500 Loop Station", "rc 5", False),
    ("Cherub WST-905 Clip-on Tuner", "wst 905li", False),
    ("Cherub WST-905Li Flow Tune", "wst 905li", True),
    ("Les Paul Standard '60s", "les paul standard 60s", True),
])  # fmt: skip
def test_terms_match_whole_words(text, term, hit):
    assert has_term(text, term) is hit


@pytest.mark.parametrize("text, condition, verdict", [
    ("Epiphone Les Paul Standard 60s Figured Left-Handed Iced Tea Burst", "UNKNOWN", IdentityVerdict.EXACT),
    ("Epiphone Les Paul Standard 60s Figured Right Handed Iced Tea", "UNKNOWN", IdentityVerdict.VARIANT_MISMATCH),
    ("Epiphone Les Paul Standard 60s Ice Tea Secondhand", "UNKNOWN", IdentityVerdict.VARIANT_MISMATCH),
    ("Epiphone Les Paul Standard 60s Left-Handed Iced Tea", "UNKNOWN", IdentityVerdict.UNKNOWN),  # no 'Figured'
    ("Epiphone Les Paul Standard 60s Figured LH Iced Tea", "USED", IdentityVerdict.VARIANT_MISMATCH),
])  # fmt: skip
def test_identity_rules_for_the_hardest_variant_case(key, text, condition, verdict):
    assert rules_verdict(key.entry("SB-028"), text, condition)[0] is verdict


# --------------------------------------------------------------------------- isolated databases


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture
def source_db(tmp_path):
    """A stand-in for a real review database, built in a temporary folder."""
    path = tmp_path / "source" / "review.sqlite3"
    path.parent.mkdir()
    isolated_db.create_empty(path, protected=())
    isolated_db.apply_decisions(path, [Approval(domain="approved-source.example", company_type=CompanyType.RETAILER)],
                                protected=())  # fmt: skip
    return path


def test_a_copy_preserves_schema_and_rows_and_never_writes_the_source(source_db, tmp_path):
    before = sha(source_db)
    copy_ = isolated_db.create_copy(source_db, tmp_path / "eval" / "review.sqlite3", protected=())
    assert sha(source_db) == before
    q = "SELECT name, sql FROM sqlite_master ORDER BY name"
    with sqlite3.connect(source_db) as a, sqlite3.connect(copy_) as b:
        assert a.execute(q).fetchall() == b.execute(q).fetchall()
        assert a.execute("PRAGMA user_version").fetchone() == b.execute("PRAGMA user_version").fetchone()
    assert [d.domain for d in ReviewStore(copy_).list_decisions()] == ["approved-source.example"]
    # Decisions go to the copy only.
    isolated_db.apply_decisions(copy_, [Approval(domain="eval-only.example", decision=ReviewDecision.REJECTED)],
                                protected=())  # fmt: skip
    assert sha(source_db) == before
    assert {d.domain for d in ReviewStore(copy_).list_decisions()} == {"approved-source.example", "eval-only.example"}


def test_reset_recreates_from_the_source_or_empty(source_db, tmp_path):
    dest = isolated_db.create_copy(source_db, tmp_path / "eval.sqlite3", protected=())
    isolated_db.apply_decisions(dest, [Approval(domain="temp.example")], protected=())
    isolated_db.reset(dest, source_db, protected=())
    assert [d.domain for d in ReviewStore(dest).list_decisions()] == ["approved-source.example"]
    isolated_db.reset(dest, protected=())
    assert ReviewStore(dest).list_decisions() == []


def test_reapplying_decisions_is_idempotent_and_conflicts_are_refused(tmp_path):
    db = isolated_db.create_empty(tmp_path / "e.sqlite3", protected=())
    a = Approval(domain="x.example", company_type=CompanyType.RETAILER)
    isolated_db.apply_decisions(db, [a], protected=())
    isolated_db.apply_decisions(db, [a], protected=())
    assert len(ReviewStore(db).list_decisions()) == 1
    with pytest.raises(IsolationError):
        isolated_db.apply_decisions(db, [Approval(domain="x.example", decision=ReviewDecision.REJECTED)], protected=())


def test_isolation_guards(source_db, tmp_path):
    with pytest.raises(IsolationError):
        isolated_db.create_copy(source_db, source_db, protected=())  # the source itself
    protected = tmp_path / "protected"
    with pytest.raises(IsolationError):
        isolated_db.create_copy(source_db, protected / "x.sqlite3", protected=(protected,))
    dest = isolated_db.create_copy(source_db, tmp_path / "e.sqlite3", protected=())
    with pytest.raises(IsolationError):
        isolated_db.create_copy(source_db, dest, protected=())  # never overwritten silently
    # The real data/review folder is protected by default.
    with pytest.raises(IsolationError):
        isolated_db.check_destination(isolated_db.PROTECTED_DIRS[0] / "anything.sqlite3")


# --------------------------------------------------------------------------- harness


def harness_settings(tmp_path, review_db, ledger, **kw):
    return make_settings(**{"search_provider": "exa", "exa_api_key": "exa-test-key", "review_db_path": review_db,
                            "stage9_ledger_path": ledger, "product_images_enabled": False, **TEST_PRICES, **kw})


def use_mock_exa(exa, catalogue):
    """prepare_app hook: the app's agents use the real Exa adapter over a mocked transport."""

    def prepare(app):
        deps = app.state.run_manager.d
        s = deps.settings

        async def disc(decisions):
            search = build_provider("exa", s, httpx.AsyncClient(transport=httpx.MockTransport(exa)))
            return CompetitorDiscoveryAgent(search, None, s, catalogue.competitors,
                                            [p.category for p in catalogue.products.values()],
                                            llm_disabled_reason="test: no LLM", review_decisions=decisions)  # fmt: skip

        async def intel(targets, skipped):
            search = build_provider("exa", s, httpx.AsyncClient(transport=httpx.MockTransport(exa)))
            return ProductIntelligenceAgent(search, None, s, targets, skipped, llm_disabled_reason="test: no LLM")

        deps.discovery_factory, deps.intelligence_factory = disc, intel

    return prepare


@pytest.fixture
def scenario(tmp_path):
    db = isolated_db.create_empty(tmp_path / "eval" / "review.sqlite3", protected=())
    ledger = BudgetLedger.create(tmp_path / "eval" / "ledger.sqlite3", D("3.00"), "test")
    return ScenarioRun(label="A", product_ids=["SB-001"], review_db=db, ledger=ledger.path,
                       output_dir=tmp_path / "out", poll_s=0.01,
                       approvals=[Approval(domain="approved-eval.example", company_type=CompanyType.RETAILER)])


def test_the_harness_runs_through_post_api_runs_and_saves_linked_artifacts(scenario, tmp_path, catalogue):
    exa = Exa(cost="0.005")
    s = harness_settings(tmp_path, scenario.review_db, scenario.ledger)
    res = run_scenario(scenario, settings=s, prepare_app=use_mock_exa(exa, catalogue), sleep=lambda _: None)
    assert res.http_status == 202 and res.status in ("COMPLETED", "PARTIALLY_COMPLETED")
    a = load_artifacts(res.artifact_dir)
    assert a["manifest"]["run_id"] == res.run_id == a["run"]["run_id"] == a["ledger"]["run"]["run_id"]
    assert a["manifest"]["request"] == {"product_ids": ["SB-001"], "run_discovery": True}
    assert a["manifest"]["review_db"] == str(Path(scenario.review_db).resolve())
    assert len(a["manifest"]["answer_key_sha256"]) == 64 and a["manifest"]["duration_s"] >= 0
    assert a["events"] and a["events"][-1]["type"] in ("run_completed", "run_failed")
    # The pipeline used the isolated database: the decision applied there made the domain a target.
    assert "approved-eval.example" in {t["domain"] for t in a["result"]["search_targets"]}
    # Spending linkage: the run reserved, every Exa call was metered, the ledger reconciled.
    entry = a["ledger"]["run"]
    assert entry["status"] == "RECONCILED" and len(a["ledger"]["attempts"]) == len(exa.calls) > 0
    assert D(entry["charged_exa_usd"]) == D("0.005") * len(exa.calls)
    # No secret reaches the artifacts.
    blob = "".join(p.read_text("utf-8") for p in Path(res.artifact_dir).iterdir())
    assert "exa-test-key" not in blob
    # Artifacts are never overwritten: a second run writes a new folder.
    res2 = run_scenario(scenario, settings=s, prepare_app=use_mock_exa(exa, catalogue), sleep=lambda _: None)
    assert res2.artifact_dir != res.artifact_dir and len(list((tmp_path / "out").iterdir())) == 2


def test_the_harness_is_blocked_while_prices_are_unset(scenario, tmp_path, catalogue):
    exa = Exa()
    s = harness_settings(tmp_path, scenario.review_db, scenario.ledger,
                         stage9_exa_usd_per_search_max=None)  # an unconfigured price  # fmt: skip
    res = run_scenario(scenario, settings=s, prepare_app=use_mock_exa(exa, catalogue), sleep=lambda _: None)
    assert res.http_status == 503 and res.status == "REFUSED" and res.run_id is None and exa.calls == []
    assert "STAGE9_EXA_USD_PER_SEARCH_MAX" in json.dumps(res.detail)


def test_the_harness_refuses_without_budget_mode_a_ledger_or_an_isolated_db(scenario, tmp_path):
    with pytest.raises(HarnessError):
        run_scenario(scenario, settings=harness_settings(tmp_path, scenario.review_db, None))
    missing = ScenarioRun(**{**scenario.__dict__, "ledger": tmp_path / "none.sqlite3"})
    with pytest.raises(HarnessError):
        run_scenario(missing, settings=harness_settings(tmp_path, scenario.review_db, missing.ledger))
    other_db = ScenarioRun(**{**scenario.__dict__, "review_db": tmp_path / "absent.sqlite3"})
    with pytest.raises(HarnessError):
        run_scenario(other_db, settings=harness_settings(tmp_path, other_db.review_db, scenario.ledger))
    with pytest.raises(IsolationError):
        run_scenario(ScenarioRun(**{**scenario.__dict__,
                                    "review_db": isolated_db.PROTECTED_DIRS[0] / "competitor_review.sqlite3"}))


# --------------------------------------------------------------------------- scoring


@pytest.fixture
def fixture_run():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))  # fake data from the mocked pipeline
    return data["run"], data["result"]


def test_fixture_scoring_counts(key, fixture_run, catalogue):
    run, result = fixture_run
    card = score_run(key, run, result, catalogue_prices={"SB-001": catalogue.products["SB-001"].displayed_price})
    m = card.metrics
    assert card.product_states == {"SB-001": "SEARCHED"}
    # Three EXACT_MATCH: Andertons and Beckett's confirmed by the rules; Gear4music too (title/URL).
    assert m["exact_match_precision"]["denominator"] == 3 and m["exact_match_precision"]["numerator"] == 3
    # The bundle listing is a variant mismatch handled as SIMILAR.
    assert m["similar_match_handling"]["numerator"] == m["similar_match_handling"]["denominator"] == 1
    # SB-001's comparison is left out: its baseline is excluded from price scoring (historical reference).
    assert m["price_comparison_correctness"]["denominator"] == 0
    assert m["price_comparison_correctness"]["excluded"] == {"SB-001: price scoring excluded by the answer key": 1}
    # Domain eligibility: every searched domain was a target.
    assert m["domain_eligibility"]["failures"] == [] and m["domain_eligibility"]["denominator"] == 14
    # Without human verification: extraction not scored (left out, not wrong); relevance not scored.
    assert m["extraction_accuracy"]["denominator"] == 0 and m["extraction_accuracy"]["excluded"]
    assert m["discovery_relevance"]["denominator"] == 0
    assert "| exact_match_precision | 100.0% | 3/3 |" in render_markdown(card)


def test_a_variant_mismatch_labelled_exact_is_a_failure(key, fixture_run):
    run, result = fixture_run
    r = copy.deepcopy(result)
    c = r["products"][0]["matching"]["candidates"][0]
    c["product_url"] = "https://www.andertons.co.uk/boss-rc-5-loop-station-nearly-new"
    m = score_run(key, run, r).metrics
    assert m["exact_match_precision"]["numerator"] == 2 and m["exact_match_precision"]["denominator"] == 3
    assert any("VARIANT_MISMATCH" in f for f in m["exact_match_precision"]["failures"])
    assert m["similar_match_handling"]["failures"]


def test_unsearched_products_are_never_no_match_and_no_match_is_not_a_failure(key, fixture_run):
    run, result = fixture_run
    r = copy.deepcopy(result)
    unsearched = copy.deepcopy(r["products"][0])
    unsearched.update(product_id="SB-055", status="FAILED", intelligence=None, matching=None)
    r["products"].append(unsearched)
    card = score_run(key, run, r)
    assert card.product_states["SB-055"] == "NOT_SEARCHED"
    assert card.metrics["exact_match_precision"]["excluded"]["product NOT_SEARCHED"] == 1
    assert card.metrics["run_completion"]["numerator"] == 1 and card.metrics["run_completion"]["denominator"] == 2
    # NO_MATCH candidates never appear as failures.
    r2 = copy.deepcopy(result)
    for c in r2["products"][0]["matching"]["candidates"]:
        if c["match_status"] != "EXACT_MATCH":
            c["match_status"] = "NO_MATCH"
    assert not any("NO_MATCH" in f for mt in score_run(key, run, r2).metrics.values() for f in mt["failures"])


def test_every_price_rule_is_checked_independently(key, fixture_run):
    run, result = fixture_run
    for mutate, fragment in (
        (lambda c: c["competitor"].update(price_benchmark=False), "not benchmark-eligible"),
        (lambda c: c["price"].update(evidence_status="STALE"), "evidence STALE"),
        (lambda c: c["price"].update(currency="EUR"), "currency EUR"),
        (lambda c: c["condition"].update(comparable_to_new=False), "condition not comparable"),
        (lambda c: c["comparison"].update(difference_gbp="5.00"), "difference"),
        (lambda c: c["price"].update(status="AMBIGUOUS"), "price status AMBIGUOUS"),
    ):  # fmt: skip
        r = copy.deepcopy(result)
        r["products"][0]["product_id"] = "SB-017"  # a price-scored product (SB-001 is excluded)
        c = next(x for x in r["products"][0]["matching"]["candidates"] if x["comparison"]["eligible"])
        mutate(c)
        pc = score_run(key, run, r).metrics["price_comparison_correctness"]
        assert pc["numerator"] == 0 and pc["denominator"] == 1 and fragment in pc["failures"][0], fragment


def test_sb028_comparisons_are_left_out_of_price_scoring(key, fixture_run):
    run, result = fixture_run
    r = copy.deepcopy(result)
    r["products"][0]["product_id"] = "SB-028"
    pc = score_run(key, run, r).metrics["price_comparison_correctness"]
    assert pc["denominator"] == 0 and pc["excluded"] == {"SB-028: price scoring excluded by the answer key": 1}


def test_human_verification_drives_extraction_and_relevance(key, fixture_run):
    run, result = fixture_run
    ver = Verifications(
        domain_relevance={"lookalike-music.co.uk": Relevance.NOT_RELEVANT, "ebay.co.uk": Relevance.RELEVANT},
        candidates=[CandidateVerification(product_url="https://www.andertons.co.uk/boss-rc-5-loop-station",
                                          identity=IdentityVerdict.EXACT, price=D("189.00"), currency="GBP"),
                    CandidateVerification(product_url="https://www.gibson.com/en-gb/boss-rc-5", price=D("1"))])
    m = score_run(key, run, result, verifications=ver).metrics
    assert (m["discovery_relevance"]["numerator"], m["discovery_relevance"]["denominator"]) == (1, 2)
    assert (m["extraction_accuracy"]["numerator"], m["extraction_accuracy"]["denominator"]) == (2, 2)
    assert m["extraction_accuracy"]["excluded"]["page not retrieved"] >= 1  # Gibson: not a wrong extraction
    wrong = Verifications(candidates=[CandidateVerification(
        product_url="https://www.andertons.co.uk/boss-rc-5-loop-station", price=D("199.00"))])
    priced = copy.deepcopy(result)
    priced["products"][0]["product_id"] = "SB-017"  # price-scored, so the wrong price must fail it
    m = score_run(key, run, priced, verifications=wrong).metrics
    assert m["price_comparison_correctness"]["denominator"] == 1
    assert m["extraction_accuracy"]["numerator"] == 0 and m["price_comparison_correctness"]["numerator"] == 0


def test_domain_eligibility_catches_searching_a_rejected_or_untargeted_domain(key, fixture_run):
    run, result = fixture_run
    r = copy.deepcopy(result)
    r["search_targets"] = [t for t in r["search_targets"] if t["domain"] != "beckettsmusic.co.uk"]
    m = score_run(key, run, r, review_decisions=[{"domain": "andertons.co.uk", "decision": "REJECTED"}]).metrics
    fails = m["domain_eligibility"]["failures"]
    assert any("beckettsmusic.co.uk: not a search target" in f for f in fails)
    assert any("andertons.co.uk: rejected by a reviewer" in f for f in fails)


def test_cost_and_latency_come_from_the_ledger_and_the_run(key, fixture_run):
    run, result = fixture_run
    ledger = {"run": {"status": "RECONCILED", "reserved_usd": "0.90", "charged_exa_usd": "0.120",
                      "charged_openrouter_usd": "0.010", "unreported_attempts": 0, "refused_attempts": 0}}
    cl = score_run(key, run, result, ledger=ledger, duration_s=90.0).cost_latency
    assert cl["charged_total_usd"] == "0.130" and cl["duration_per_product_s"] == 90.0
    assert cl["ledger_status"] == "RECONCILED"
