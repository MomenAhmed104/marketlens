"""Stage 8.1: automatic search eligibility across runs, the per-run cap, rejection precedence,
pre-Stage 8.1 records, the review API view, SSE payloads and benchmark independence.

Fakes only: no network, no keys, temporary databases.
"""

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.agents.discovery.rules import apply_classification
from app.agents.discovery.schemas import DiscoveryMode, EvidenceKind, UkSeller
from app.agents.intelligence import auto_target_rank, eligible_search_targets
from app.main import create_app
from app.schemas.common import DomainApprovalStatus as DS
from app.schemas.competitor import CompanyType as T
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.review_store import ReviewStore
from tests.discovery_fakes import FakeLLM
from tests.pipeline_fakes import harness
from tests.search_fakes import make_settings
from tests.store_fakes import CONFIGURED_13
from tests.test_discovery_agent import CLASSIFICATIONS, PLAN
from tests.test_discovery_rules import CATS, candidate, cls, ev
from tests.test_pipeline import searched_domains
from tests.test_review_store import run as run_result
from tests.test_search_targets import evaluate, fresh
from tests.matching_fakes import target

NOW = datetime(2026, 11, 22, tzinfo=UTC)
BECK = "beckettsmusic.co.uk"


def eligible(domain, name=None, products=("SB-001",), pages=1, product_ids=("SB-001",)):
    """A stored-style candidate that passes `is_search_eligible` (current classification)."""
    name = name or domain.split(".")[0].title()
    evidence = [ev(f"https://{domain}/p{i}", f"Boss RC-5 - {name}", "£189", product=product_ids[i % len(product_ids)])
                for i in range(pages)]  # fmt: skip
    c = candidate(domain, name, evidence=evidence, products=products)
    apply_classification(c, cls(domain=domain, company_name=name, company_type=T.RETAILER,
                                evidence_urls=[f"https://{domain}/p0"]), CATS)  # fmt: skip
    return c


def decision(domain, kind=ReviewDecision.APPROVED, **kw):
    return DomainDecisionRecord(domain=domain, decision=kind, company_type=kw.pop("company_type", T.RETAILER),
                                reviewer="sam", decided_at=NOW, **kw)  # fmt: skip


def auto(targets):
    return [t.domain for t in targets if t.approval_status is DS.AUTO_APPROVED]


# --------------------------------------------------------------------------- target selection and cap


def test_automatic_targets_come_after_configured_and_approved_and_never_benchmark(catalogue):
    s = make_settings()
    targets, skipped = eligible_search_targets(catalogue.competitors, [decision("approved.co.uk", benchmark_eligible=True)],
                                               s, candidates=[eligible("autoshop.co.uk")])  # fmt: skip
    assert {t.domain for t in targets[:13]} == set(CONFIGURED_13)
    assert [t.domain for t in targets][-2:] == ["approved.co.uk", "autoshop.co.uk"]
    a = targets[-1]
    assert a.approval_status is DS.AUTO_APPROVED and a.price_benchmark is False and a.company_type is T.RETAILER
    assert skipped == []


def test_without_candidates_there_are_no_automatic_targets(catalogue):
    """The matching trust check calls it without candidates: automatic domains are never trusted targets."""
    targets, _ = eligible_search_targets(catalogue.competitors, [], make_settings())
    assert auto(targets) == []


def test_cap_keeps_the_best_grounded_domains_and_reports_the_rest(catalogue):
    cands = [eligible("one.co.uk", products=("SB-001", "SB-028"), pages=2, product_ids=("SB-001", "SB-028")),
             eligible("two.co.uk", products=("SB-001",), pages=3),
             eligible("three.co.uk", products=("SB-001",), pages=1),
             eligible("four.co.uk", products=(), pages=1)]  # fmt: skip
    s = make_settings(max_auto_search_targets=2)
    targets, skipped = eligible_search_targets(catalogue.competitors, [], s, candidates=list(reversed(cands)),
                                               product_ids=["SB-001", "SB-028"])  # fmt: skip
    assert auto(targets) == ["one.co.uk", "two.co.uk"]
    over = {k.domain: k.reason for k in skipped}
    assert set(over) == {"three.co.uk", "four.co.uk"}
    assert "over the per-run cap of 2" in over["three.co.uk"] and "ranked 3 of 4" in over["three.co.uk"]
    assert "ranked 4 of 4" in over["four.co.uk"]
    # Deterministic: the same inputs in another order give the same answer.
    again, _ = eligible_search_targets(catalogue.competitors, [], s, candidates=cands, product_ids=["SB-001", "SB-028"])
    assert auto(again) == auto(targets)


def test_rank_prefers_the_selected_products_and_never_uses_scores():
    a = eligible("a.co.uk", products=("SB-028",))
    b = eligible("b.co.uk", products=("SB-001",))
    b.relevance, b.confidence, a.relevance, a.confidence = 0.01, 0.01, 0.99, 0.99
    assert sorted([a, b], key=lambda c: auto_target_rank(c, ["SB-001"]))[0].domain == "b.co.uk"
    assert sorted([a, b], key=lambda c: auto_target_rank(c, ["SB-028"]))[0].domain == "a.co.uk"
    assert sorted([b, a], key=lambda c: auto_target_rank(c))[0].domain == "a.co.uk"  # tie broken by name


def test_cap_never_limits_configured_or_approved_domains(catalogue):
    s = make_settings(max_auto_search_targets=0)
    targets, skipped = eligible_search_targets(catalogue.competitors, [decision("approved.co.uk")], s,
                                               candidates=[eligible("autoshop.co.uk")])  # fmt: skip
    assert {"approved.co.uk", *CONFIGURED_13} == {t.domain for t in targets}
    assert [k.domain for k in skipped] == ["autoshop.co.uk"]


def test_rejected_pending_ineligible_excluded_and_known_domains_never_become_automatic(catalogue):
    manufacturer = eligible("brand.com")
    manufacturer.company_type = T.MANUFACTURER
    old = eligible("old.co.uk")
    old.classification_version = None  # classified before Stage 8.1
    unclassified = candidate("pending.co.uk")
    pmt = eligible("pmtonline.co.uk")
    andertons = eligible("andertons.co.uk")
    cands = [eligible("rejected.co.uk"), manufacturer, old, unclassified, pmt, andertons, eligible("okshop.co.uk")]
    targets, skipped = eligible_search_targets(catalogue.competitors, [decision("rejected.co.uk", ReviewDecision.REJECTED)],
                                               make_settings(), candidates=cands)  # fmt: skip
    assert auto(targets) == ["okshop.co.uk"]
    assert next(t for t in targets if t.domain == "andertons.co.uk").approval_status is DS.PRECONFIGURED
    assert {k.domain: k.reason for k in skipped} == {"rejected.co.uk": "Rejected in review"}


def test_automatic_search_can_be_switched_off(catalogue):
    targets, skipped = eligible_search_targets(catalogue.competitors, [], make_settings(pipeline_search_auto_approved=False),
                                               candidates=[eligible("autoshop.co.uk")])  # fmt: skip
    assert auto(targets) == [] and skipped == []


def test_only_domains_restricts_automatic_domains_before_the_cap(catalogue):
    s = make_settings(max_auto_search_targets=1)
    cands = [eligible("best.co.uk", pages=3), eligible("wanted.co.uk")]
    targets, skipped = eligible_search_targets(catalogue.competitors, [], s, candidates=cands,
                                               only_domains={"wanted.co.uk"})  # fmt: skip
    assert auto(targets) == ["wanted.co.uk"] and skipped == []


# --------------------------------------------------------------------------- pipeline across runs


def test_automatic_eligibility_persists_across_runs_and_a_rejection_always_wins(catalogue, tmp_path):
    h1 = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS))
    _, r1, _ = asyncio.run(h1.run(product_ids=["SB-001"]))
    assert BECK in searched_domains(h1)
    assert h1.review.list_decisions() == []  # no artificial human approval is written

    # Run 2 without LLM 1: BECK is not rediscovered as classified, but its stored record still qualifies.
    h2 = harness(catalogue, tmp_path)
    _, r2, events = asyncio.run(h2.run(product_ids=["SB-001"]))
    t = {x.domain: x for x in r2.search_targets}
    assert t[BECK].approval_status is DS.AUTO_APPROVED and t[BECK].price_benchmark is False
    assert BECK in searched_domains(h2)
    loaded = next(e for e in events if e.type == "known_domains_loaded")
    assert loaded.payload["auto_approved"] >= 1 and BECK in loaded.payload["domains"]

    # A reviewer rejects it: never searched again, even when LLM 1 rediscovers it as eligible.
    h2.review.create_decision(decision(BECK, ReviewDecision.REJECTED))
    h3 = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS))
    _, r3, _ = asyncio.run(h3.run(product_ids=["SB-001"]))
    assert BECK not in {x.domain for x in r3.search_targets} and BECK not in searched_domains(h3)
    assert next(c for c in r3.discovery.candidates if c.domain == BECK).approval_status is DS.REJECTED


def _strip_version(path, domain):
    with closing(sqlite3.connect(path)) as conn:
        rec = json.loads(conn.execute("SELECT record FROM review_candidates WHERE domain = ?", (domain,)).fetchone()[0])
        rec["candidate"].pop("classification_version", None)  # as stored before Stage 8.1
        conn.execute("UPDATE review_candidates SET record = ? WHERE domain = ?", (json.dumps(rec), domain))
        conn.commit()


def test_old_pending_records_need_rediscovery_under_the_new_prompt(catalogue, tmp_path):
    h1 = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS))
    asyncio.run(h1.run(product_ids=["SB-001"]))
    _strip_version(h1.settings.review_db_path, BECK)

    h2 = harness(catalogue, tmp_path)  # no LLM: the old classification is kept, but never activated
    _, r2, _ = asyncio.run(h2.run(product_ids=["SB-001"]))
    assert BECK not in {x.domain for x in r2.search_targets}
    stored = h2.review.get_candidate(BECK).candidate
    assert stored.company_type is T.SPECIALIST_RETAILER and stored.classification_version is None

    h3 = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS))  # rediscovered and classified
    _, r3, _ = asyncio.run(h3.run(product_ids=["SB-001"]))
    assert BECK in {x.domain for x in r3.search_targets} and BECK in searched_domains(h3)


def test_pending_and_ineligible_domains_are_never_searched(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS))
    _, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
    searched = searched_domains(h)
    assert not searched & {"ebay.co.uk", "lookalike-music.co.uk", "guitarworld.com", "stompbox.co.uk", "youtube.com"}
    assert set(result.new_pending_domains) >= {"ebay.co.uk", "lookalike-music.co.uk"}


def test_competitor_discovered_events_carry_no_uk_verification_flags(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS))
    _, _, events = asyncio.run(h.run(product_ids=["SB-001"]))
    found = {e.payload["domain"]: e.payload for e in events if e.type == "competitor_discovered"}
    assert found[BECK]["auto_search_eligible"] is True and found["ebay.co.uk"]["auto_search_eligible"] is False
    assert found[BECK]["serves_uk_customers"] == "yes"
    assert not [k for p in found.values() for k in p if k.startswith("uk_")]
    auto_evt = next(e for e in events if e.type == "domain_auto_approved")
    assert auto_evt.message == f"{BECK} is automatically searchable"


# --------------------------------------------------------------------------- review API view


def stored_db(tmp_path, cands, decisions=()):
    store = ReviewStore(tmp_path / "r.sqlite3")
    store.record_run(run_result(cands, mode=DiscoveryMode.LLM_ENABLED))
    for d in decisions:
        store.create_decision(d)
    return store


def test_review_api_explains_why_a_domain_is_or_is_not_searched(tmp_path):
    maker = eligible("brand.com")
    maker.company_type = T.MANUFACTURER
    store = stored_db(tmp_path, [eligible("autoshop.co.uk"), maker, eligible("approved.co.uk"),
                                 eligible("rejected.co.uk"), candidate("pending.co.uk")],
                      [decision("approved.co.uk", benchmark_eligible=True),
                       decision("rejected.co.uk", ReviewDecision.REJECTED)])  # fmt: skip
    with TestClient(create_app(make_settings(review_db_path=store.path))) as client:
        rows = {r["domain"]: r for r in client.get("/api/review/candidates").json()}
        detail = client.get("/api/review/candidates/autoshop.co.uk").json()
    a = rows["autoshop.co.uk"]
    assert (a["review_status"], a["search_basis"], a["search_eligible"], a["benchmark_eligible"]) == \
        ("PENDING", "AUTOMATIC", True, False)
    assert a["search_eligibility_reasons"][0].startswith("Automatically searchable")
    assert any("per run" in r for r in a["search_eligibility_reasons"])
    m = rows["brand.com"]
    assert (m["search_basis"], m["search_eligible"]) == ("NOT_SEARCHED", False)
    assert any("type is manufacturer" in r for r in m["search_eligibility_reasons"])
    ap = rows["approved.co.uk"]
    assert (ap["search_basis"], ap["benchmark_eligible"]) == ("MANUAL_APPROVAL", True)
    rj = rows["rejected.co.uk"]
    assert (rj["search_basis"], rj["search_eligible"], rj["auto_search_eligible"]) == ("NOT_SEARCHED", False, False)
    p = rows["pending.co.uk"]
    assert p["search_basis"] == "NOT_SEARCHED" and p["classification_version"] is None
    for r in rows.values():
        assert not [k for k in r if k.startswith("uk_")]
        assert r["serves_uk_customers"] in ("yes", "no", "unknown")
    assert detail["search_basis"] == "AUTOMATIC" and "uk_review" not in detail


def test_review_api_reports_automatic_search_switched_off(tmp_path):
    store = stored_db(tmp_path, [eligible("autoshop.co.uk")])
    s = make_settings(review_db_path=store.path, pipeline_search_auto_approved=False)
    with TestClient(create_app(s)) as client:
        (row,) = client.get("/api/review/candidates").json()
    assert (row["auto_search_eligible"], row["search_eligible"], row["search_basis"]) == (True, False, "NOT_SEARCHED")
    assert any("switched off" in r for r in row["search_eligibility_reasons"])


def test_serves_uk_customers_no_is_shown_but_never_blocks(tmp_path):
    c = eligible("abroad.de", name="Abroad Shop")
    c.serves_uk_customers, c.serves_uk_customers_note = UkSeller.NO, "Says it ships within Germany only"
    store = stored_db(tmp_path, [c])
    with TestClient(create_app(make_settings(review_db_path=store.path))) as client:
        (row,) = client.get("/api/review/candidates").json()
        cfg = client.get("/api/intelligence/config").json()
    assert row["serves_uk_customers"] == "no" and row["search_basis"] == "AUTOMATIC"
    assert row["serves_uk_customers_note"] == "Says it ships within Germany only"
    assert "abroad.de" in {t["domain"] for t in cfg["search_targets"]}


# --------------------------------------------------------------------------- benchmark independence


def test_automatic_domains_are_never_trusted_as_benchmarks(tmp_path):
    store = stored_db(tmp_path, [eligible("autoshop.co.uk")])
    tampered = target("autoshop.co.uk", benchmark=True, status=DS.AUTO_APPROVED)
    with TestClient(create_app(make_settings(review_db_path=store.path))) as client:
        body = evaluate(client, fresh("https://autoshop.co.uk/boss-rc-5-loop-station-pedal", tampered))
    (c,) = body["products"][0]["candidates"]
    assert c["competitor"]["price_benchmark"] is False and not c["comparison"]["eligible"]
    assert c.get("price_kind") != "DIRECT"


def test_profile_only_evidence_cannot_make_a_domain_eligible():
    c = candidate(BECK, "Beckett's Music", evidence=[ev(f"https://{BECK}/about", "About Beckett's Music",
                                                        kind=EvidenceKind.DOMAIN_PROFILE)])  # fmt: skip
    apply_classification(c, cls(evidence_urls=[f"https://{BECK}/about"]), CATS)
    from app.agents.discovery.rules import decide, policy_from_settings

    decide(c, policy_from_settings(make_settings()))
    assert c.approval_status is DS.REJECTED  # no discovery result: never searched automatically
