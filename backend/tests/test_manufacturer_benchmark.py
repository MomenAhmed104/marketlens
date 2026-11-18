"""R1: manufacturers may join the main price benchmark alongside retailers (business decision,
18 Nov 2026). Type no longer excludes them; every other safeguard still applies: the benchmark
flag, the UK gate (NOT_UK_ELIGIBLE always blocked, INSUFFICIENT_EVIDENCE only with a benchmark
exception), approval, page domain, match status and price validity. Temporary databases only."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.agents.discovery.rules import apply_review_decision
from app.main import create_app
from app.schemas.competitor import BENCHMARK_ELIGIBLE_TYPES, CompanyType as T
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from tests.matching_fakes import candidate, target
from tests.search_fakes import make_settings
from tests.test_review_store import candidate as discovered
from tests.test_uk_gate_paths import AND_RC5, evaluate, fresh, priced
from tests.uk_fakes import S, seed_store, uk_exception, uk_record

YAMAHA = "yamahamusiclondon.com"
YAMAHA_RC5 = "https://www.yamahamusiclondon.com/boss-rc-5-loop-station-pedal"
GIBSON_RC5 = "https://www.gibson.com/boss-rc-5-loop-station-pedal"
MAKER = "maker-example.co.uk"  # a discovered, reviewer-approved manufacturer (not in the workbook)
MAKER_RC5 = f"https://www.{MAKER}/boss-rc-5-loop-station-pedal"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(review_db_path=tmp_path / "review.sqlite3"))) as c:
        yield c


def first(body):
    return body["products"][0]["candidates"][0]


def maker_decision(benchmark=True, ctype=T.MANUFACTURER):
    return DomainDecisionRecord(domain=MAKER, decision=ReviewDecision.APPROVED, company_type=ctype,
                                benchmark_eligible=benchmark, reviewer="sam", decided_at=datetime.now(UTC))  # fmt: skip


# --------------------------------------------------------------------------- the type restriction


def test_manufacturer_is_a_benchmark_type_and_others_are_not():
    assert T.MANUFACTURER in BENCHMARK_ELIGIBLE_TYPES
    assert {T.RETAILER, T.SPECIALIST_RETAILER} <= BENCHMARK_ELIGIBLE_TYPES
    assert not {T.MARKETPLACE, T.DIRECTORY, T.OTHER} & BENCHMARK_ELIGIBLE_TYPES


def test_catalogue_values_for_yamaha_and_gibson_are_unchanged(client):
    comps = {c.domain: c for c in client.app.state.catalogue.competitors}
    y, g = comps[YAMAHA], comps["gibson.com"]
    assert (y.company_type, y.price_benchmark) == (T.MANUFACTURER, True)
    assert (g.company_type, g.price_benchmark) == (T.MANUFACTURER, False)


# --------------------------------------------------------------------------- Yamaha (configured manufacturer)


def test_uk_eligible_yamaha_is_benchmarked(client):
    seed_store(client.app.state.review_store, [YAMAHA])
    # The payload under-claims; the server rebuilds Yamaha from the workbook and the UK gate.
    c = first(evaluate(client, fresh(YAMAHA_RC5, target(YAMAHA, ctype=T.RETAILER, benchmark=False, uk_eligible=False))))
    assert c["match_status"] == "EXACT_MATCH"
    assert c["competitor"]["company_type"] == "manufacturer" and c["competitor"]["price_benchmark"] is True
    assert c["comparison"]["eligible"] is True and c["comparison"]["difference_gbp"] is not None
    assert not any("manufacturer" in r for r in c["comparison"]["reasons"])


def test_yamaha_in_the_pipeline_is_compared(catalogue, tmp_path):
    import asyncio

    from tests.pipeline_fakes import harness

    h = harness(catalogue, tmp_path, uk_eligible=[YAMAHA, "andertons.co.uk"])
    _, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
    yamaha = [m for pr in result.products for m in pr.matching.candidates if m.competitor.domain == YAMAHA]
    assert {t.domain for t in result.search_targets} == {YAMAHA, "andertons.co.uk"}
    for m in yamaha:  # whatever the fake pages yield, type is never the reason for exclusion
        assert m.competitor.price_benchmark is True and m.competitor.company_type is T.MANUFACTURER
        assert not any("not marked as a price benchmark" in r for r in m.comparison.reasons)


@pytest.mark.parametrize("setup,reason", [
    ("unassessed", "not assessed"),
    ("not_uk_eligible", "NOT_UK_ELIGIBLE"),
    ("insufficient", "INSUFFICIENT_EVIDENCE"),
    ("search_only_exception", "INSUFFICIENT_EVIDENCE"),
])  # fmt: skip
def test_uk_gate_still_applies_to_yamaha(client, setup, reason):
    store = client.app.state.review_store
    if setup == "not_uk_eligible":
        store.put_uk_eligibility(uk_record(YAMAHA, S.NOT_UK_ELIGIBLE))
    elif setup == "insufficient":
        store.put_uk_eligibility(uk_record(YAMAHA, S.INSUFFICIENT_EVIDENCE))
    elif setup == "search_only_exception":
        store.put_uk_exception(uk_exception(YAMAHA, search=True, benchmark=False))
    c = first(evaluate(client, fresh(YAMAHA_RC5, target(YAMAHA, ctype=T.MANUFACTURER, uk_eligible=True))))
    # Blocked from search too (except search-only), so the trusted rebuild also drops the flag.
    assert not c["comparison"]["eligible"] and c["competitor"]["uk_gate"]["benchmark_allowed"] is False
    assert any(reason in r for r in c["comparison"]["reasons"])


def test_yamaha_with_a_benchmark_exception_is_benchmarked(client):
    seed_store(client.app.state.review_store, exceptions=[uk_exception(YAMAHA, search=True, benchmark=True)])
    c = first(evaluate(client, fresh(YAMAHA_RC5, target(YAMAHA))))
    assert c["comparison"]["eligible"] and c["competitor"]["uk_gate"]["benchmark_basis"] == "EXCEPTION"


def test_an_exception_cannot_rescue_a_not_uk_eligible_manufacturer(client):
    store = client.app.state.review_store
    store.put_uk_exception(uk_exception(YAMAHA, search=True, benchmark=True))
    store.put_uk_eligibility(uk_record(YAMAHA, S.NOT_UK_ELIGIBLE))  # revokes the exception (D3)
    c = first(evaluate(client, fresh(YAMAHA_RC5, target(YAMAHA))))
    assert not c["comparison"]["eligible"] and any("NOT_UK_ELIGIBLE" in r for r in c["comparison"]["reasons"])


# --------------------------------------------------------------------------- manufacturers with benchmark=false


def test_gibson_stays_excluded_even_when_uk_eligible(client):
    seed_store(client.app.state.review_store, ["gibson.com"])
    tampered = target("gibson.com", ctype=T.MANUFACTURER, benchmark=True, uk_eligible=True)
    c = first(evaluate(client, fresh(GIBSON_RC5, tampered)))
    assert c["competitor"]["price_benchmark"] is False and c["competitor"]["uk_gate"]["benchmark_allowed"] is True
    assert not c["comparison"]["eligible"]
    assert any("gibson.com is not marked as a price benchmark" in r for r in c["comparison"]["reasons"])


def test_reviewed_manufacturer_without_the_benchmark_flag_is_excluded(client):
    store = client.app.state.review_store
    store.create_decision(maker_decision(benchmark=False))
    seed_store(store, [MAKER])
    c = first(evaluate(client, fresh(MAKER_RC5, target(MAKER, ctype=T.MANUFACTURER, benchmark=True))))
    assert c["competitor"]["price_benchmark"] is False and not c["comparison"]["eligible"]


# --------------------------------------------------------------------------- reviewed (discovered) manufacturers


def test_reviewer_can_mark_a_manufacturer_benchmark_eligible(client):
    store = client.app.state.review_store
    store.record_run(__import__("tests.test_review_store", fromlist=["run"]).run([discovered(MAKER)]))
    r = client.post(f"/api/review/candidates/{MAKER}/approve",
                    json={"reviewer": "sam", "company_type": "manufacturer", "benchmark_eligible": True})
    assert r.status_code == 201 and r.json()["benchmark_eligible"] is True
    assert maker_decision().benchmark_eligible is True  # the record validator accepts it too


def test_reviewed_manufacturer_is_benchmarked_only_with_approval_and_uk_clearance(client):
    store = client.app.state.review_store
    page = fresh(MAKER_RC5, target(MAKER, ctype=T.MANUFACTURER))
    # Not approved yet: not a search target, so never a benchmark (even if UK eligible).
    seed_store(store, [MAKER])
    assert not first(evaluate(client, page))["comparison"]["eligible"]
    store.create_decision(maker_decision())
    c = first(evaluate(client, page))
    assert c["comparison"]["eligible"] and c["competitor"]["company_type"] == "manufacturer"


def test_apply_review_decision_keeps_the_manufacturer_flag():
    cand = discovered(MAKER)
    apply_review_decision(cand, maker_decision())
    assert cand.price_benchmark is True
    cand = discovered(MAKER)
    apply_review_decision(cand, maker_decision(benchmark=False))
    assert cand.price_benchmark is False


@pytest.mark.parametrize("ctype", ["marketplace", "directory", "other"])
def test_other_types_are_still_refused(client, ctype):
    store = client.app.state.review_store
    store.record_run(__import__("tests.test_review_store", fromlist=["run"]).run([discovered(MAKER)]))
    r = client.post(f"/api/review/candidates/{MAKER}/approve",
                    json={"reviewer": "sam", "company_type": ctype, "benchmark_eligible": True})
    assert r.status_code == 422 and "can never be a price benchmark" in r.json()["detail"]


# --------------------------------------------------------------------------- other safeguards, for a manufacturer


def test_page_on_another_domain_is_not_a_yamaha_benchmark(client):
    seed_store(client.app.state.review_store, [YAMAHA])
    c = first(evaluate(client, fresh("https://www.evil.example/boss-rc-5-loop-station-pedal", target(YAMAHA))))
    assert not c["comparison"]["eligible"] and c["competitor"]["price_benchmark"] is False


def test_non_exact_match_and_non_gbp_price_are_not_benchmarks_for_yamaha(client):
    seed_store(client.app.state.review_store, [YAMAHA])
    other_model = priced("https://www.yamahamusiclondon.com/boss-rc-1-loop-station", target(YAMAHA),
                         retrieved_at=datetime.now(UTC)).model_copy(
        update={"product_title": "Boss RC-1 Loop Station", "model": "RC-1"})  # fmt: skip
    usd = candidate(YAMAHA_RC5, "Boss RC-5 Loop Station", llm=True, product_title="Boss RC-5 Loop Station",
                    brand="BOSS", model="RC-5", price="189.00", currency="USD", quote="US$189.00 In stock",
                    competitor=target(YAMAHA), retrieved_at=datetime.now(UTC))  # fmt: skip
    body = evaluate(client, other_model, usd)
    cands = body["products"][0]["candidates"]
    assert len(cands) == 2 and not any(c["comparison"]["eligible"] for c in cands)


# --------------------------------------------------------------------------- retailers unchanged


def test_retailer_benchmarking_is_unchanged(client):
    seed_store(client.app.state.review_store, ["andertons.co.uk"])
    c = first(evaluate(client, fresh(AND_RC5, target("andertons.co.uk"))))
    assert c["comparison"]["eligible"] and c["competitor"]["company_type"] == "retailer"
    blocked = first(evaluate(client, fresh("https://www.thomann.co.uk/boss_rc5.htm", target("thomann.co.uk"))))
    assert not blocked["comparison"]["eligible"]  # unassessed retailer: still blocked by the UK gate
