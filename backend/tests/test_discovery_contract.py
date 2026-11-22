"""General discovery coverage that used to live in the retired UK test files (Stage 8.1 audit).

The UK-only assertions are gone with the UK verification system; these keep the behaviour that
is unrelated to it: the LLM 1 classification contract, evidence selection and prompts, the
agent's classify call, merging of LLM fields across runs, review API consistency and manual
benchmark permission after automatic eligibility. Fakes only: no network, no keys.
"""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agents.discovery import prompts
from app.agents.discovery.agent import PROFILE_QUERY, CompetitorDiscoveryAgent
from app.agents.discovery.rules import apply_classification
from app.agents.discovery.schemas import (
    CLASSIFICATION_VERSION,
    ClassificationBatch,
    ClassificationStatus,
    CompanyClassification,
    DiscoveredCompetitor,
    DiscoveryMode,
    EvidenceKind,
    UkSeller,
)
from app.main import create_app
from app.schemas.competitor import CompanyType as T
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.llm.base import strict_json_schema
from app.services.review_store import LLM_FIELDS, ReviewStore, merge_candidate
from tests.discovery_fakes import FakeLLM, FakeSearch
from tests.pipeline_fakes import harness
from tests.search_fakes import make_settings
from tests.test_discovery_agent import CLASSIFICATIONS, GLOBAL_HITS, PLAN, PROFILE_HITS
from tests.test_discovery_rules import CATS, candidate, cls, ev
from tests.test_review_store import run as run_result

NOW = datetime(2026, 11, 22, tzinfo=UTC)
DOMAIN = "perfectpitch.co.uk"
URL = "https://www.perfectpitch.co.uk/"


def core(**kw) -> dict:
    """A valid classification as LLM 1 returns it (JSON-shaped)."""
    base = dict(domain=DOMAIN, company_name="Perfect Pitch Ltd", company_type="specialist_retailer",
                relevant_categories=["Guitars"], serves_uk_customers="yes", serves_uk_customers_note=None,
                sells_new_musical_instruments=True, relevance=0.9, confidence=0.9,
                rationale="A music shop in Chesham.", evidence_urls=[URL], competitor_class="DIRECT_COMPETITOR")  # fmt: skip
    base.update(kw)
    return base


# --------------------------------------------------------------------------- classification contract


def test_strict_schema_exposes_no_private_or_internal_fields():
    company = strict_json_schema(ClassificationBatch)["$defs"]["CompanyClassification"]
    assert company["additionalProperties"] is False
    assert set(company["required"]) == set(company["properties"])
    assert not {k for k in company["properties"] if k.startswith("_") or "warning" in k}
    assert "classification_version" not in company["properties"]  # set by code, never by LLM 1


def test_batch_parsed_from_json_text_like_the_openrouter_client():
    c = ClassificationBatch.model_validate(json.loads(json.dumps({"companies": [core()]}))).companies[0]
    assert (c.company_type, c.serves_uk_customers, c.sells_new_musical_instruments) == (
        T.SPECIALIST_RETAILER, UkSeller.YES, True)


@pytest.mark.parametrize("bad", [
    {"company_type": "shop"},
    {"relevance": 1.5},
    {"confidence": "high"},
    {"serves_uk_customers": "maybe"},
    {"evidence_urls": "https://x"},
    {"competitor_class": "BEST"},
    {"sells_new_musical_instruments": "sometimes"},
])  # fmt: skip
def test_core_field_errors_fail_validation(bad):
    with pytest.raises(ValidationError):
        CompanyClassification.model_validate(core(**bad))


@pytest.mark.parametrize("missing", ["domain", "company_type", "rationale", "evidence_urls",
                                     "sells_new_musical_instruments"])
def test_missing_core_fields_fail_validation(missing):
    data = core()
    data.pop(missing)
    with pytest.raises(ValidationError):
        CompanyClassification.model_validate(data)


def test_core_failure_on_one_company_fails_the_whole_batch():
    with pytest.raises(ValidationError):
        ClassificationBatch.model_validate({"companies": [core(), core(company_type="shop")]})


def test_retired_uk_keys_from_old_answers_are_ignored():
    c = CompanyClassification.model_validate(core(uk_evidence=[{"url": URL, "quote": "x", "kind": "UK_ADDRESS"}],
                                                  uk_relevance="UK_BASED", uk_confidence="HIGH"))  # fmt: skip
    assert not {"uk_evidence", "uk_relevance", "uk_confidence"} & set(c.model_dump())


def test_records_stored_before_stage_8_1_load():
    raw = {"domain": DOMAIN, "hostnames": [DOMAIN], "homepage_url": URL, "evidence": [], "products_matched": [],
           "uk_signals": ["uk_domain"], "uk_seller": "unknown", "discovered_at": NOW.isoformat(),
           "uk_assessment": {"proposed": "UK_BASED", "sufficient": True}, "classification_status": "COMPLETED"}
    c = DiscoveredCompetitor.model_validate(raw)
    assert c.classification_version is None and c.serves_uk_customers is UkSeller.UNKNOWN


# --------------------------------------------------------------------------- evidence selection and prompts


def profile(url, title, snippet):
    return ev(url, title, snippet, kind=EvidenceKind.DOMAIN_PROFILE, product=None, model=False)


def test_profile_evidence_is_kept_when_there_are_many_product_hits():
    products = [ev(f"https://{DOMAIN}/p/{i}", f"Boss pedal {i}", "£99 in stock") for i in range(10)]
    profiles = [profile(f"https://{DOMAIN}/pages/x{i}", "Page", f"About us {i}") for i in range(4)]
    picked = prompts._pick_evidence(candidate(DOMAIN, evidence=products + profiles))
    kinds = [e.kind for e in picked]
    assert len(picked) == 8
    assert kinds.count(EvidenceKind.DOMAIN_PROFILE) == 3 and kinds.count(EvidenceKind.DISCOVERY_RESULT) == 5
    # In the order found: no UK-cue scoring any more.
    assert [e.url for e in picked if e.kind is EvidenceKind.DOMAIN_PROFILE] == [p.url for p in profiles[:3]]


def test_unused_slots_go_to_the_other_kind_and_empty_items_are_never_sent():
    few_products = [ev(f"https://{DOMAIN}/p/{i}", f"Boss pedal {i}", "£99") for i in range(2)]
    many_profiles = [profile(f"https://{DOMAIN}/pages/{i}", "About", "Our shop") for i in range(7)]
    blank = [profile(f"https://{DOMAIN}/pages/blank", None, "   ")]
    picked = prompts._pick_evidence(candidate(DOMAIN, evidence=few_products + many_profiles + blank))
    assert len(picked) == 8 and sum(e.kind is EvidenceKind.DOMAIN_PROFILE for e in picked) == 6
    assert all(e.url != f"https://{DOMAIN}/pages/blank" for e in picked)
    only_products = [ev(f"https://{DOMAIN}/p/{i}", f"Boss pedal {i}", "£99") for i in range(12)]
    assert len(prompts._pick_evidence(candidate(DOMAIN, evidence=only_products))) == 8


def test_snippet_lengths_sent_to_llm1_depend_on_the_evidence_kind():
    long = "x" * 600
    c = candidate(DOMAIN, evidence=[ev(f"https://{DOMAIN}/rc5", "Boss RC-5", long),
                                    profile(f"https://{DOMAIN}/about", "About", long)])  # fmt: skip
    data = json.loads(prompts.classify_user([c], [], ["Effects"]).split("<untrusted_evidence>\n", 1)[1]
                      .rsplit("\n</untrusted_evidence>", 1)[0])  # fmt: skip
    lengths = {e["found_by"]: len(e["snippet"]) for e in data["domains"][0]["evidence"]}
    assert lengths == {"discovery_result": prompts.SNIPPET_CHARS, "domain_profile": prompts.PROFILE_SNIPPET_CHARS}
    assert (prompts.SNIPPET_CHARS, prompts.PROFILE_SNIPPET_CHARS, prompts.MAX_EVIDENCE_PER_CANDIDATE,
            prompts.MAX_PROFILE_EVIDENCE) == (280, 400, 8, 3)
    assert "uk_signals" not in data["domains"][0]["facts_computed_by_code"]


def test_plan_prompt_targets_retailers_serving_uk_customers_from_anywhere():
    p = prompts.PLAN_SYSTEM.format(max_global=3, max_targeted=2)
    for phrase in ("UK musical instrument retailers", "UK guitar shops", "UK music equipment retailers",
                   "delivering to the UK", "UK stockists", "does not have to be based in the UK"):
        assert phrase in p, phrase
    assert "Do not name specific shops" in p and "site:" in p  # still forbids operators


def test_classify_prompt_has_no_uk_quote_system_and_explains_the_new_fields():
    p = prompts.CLASSIFY_SYSTEM
    for gone in ("uk_evidence", "UK_PHYSICAL_STORE", "UK_ADDRESS", "NO_UK_SERVICE", "uk_seller", "verdict"):
        assert gone not in p, gone
    assert "serves_uk_customers" in p and "for information only" in p and "Being based abroad is not a no" in p
    assert "exclusively used" in p and "null if the evidence does not make this clear" in p
    assert all(k in p for k in ("DIRECT_COMPETITOR", "RELATED_RETAILER", "NEEDS_REVIEW"))


def test_profile_query_has_no_operators_and_no_uk_wording():
    q = PROFILE_QUERY.lower()
    assert all(w in q for w in ("about", "contact", "delivery"))
    assert "site:" not in q and '"' not in q and " uk" not in q and len(PROFILE_QUERY) < 200


def test_classify_prompt_sent_by_the_agent_includes_profile_evidence(catalogue):
    llm = FakeLLM(PLAN, CLASSIFICATIONS)
    agent = CompetitorDiscoveryAgent(FakeSearch(GLOBAL_HITS, PROFILE_HITS), llm, make_settings(),
                                     catalogue.competitors, [p.category for p in catalogue.products.values()])
    asyncio.run(agent.run([catalogue.products["SB-001"]]))
    classify = next(call for call in llm.calls if call["schema"] == "company_classification")
    assert "Independent music shop in Bedford" in classify["user"]
    assert '"found_by": "domain_profile"' in classify["user"]


# --------------------------------------------------------------------------- merging across runs


def classified_record(**kw) -> DiscoveredCompetitor:
    c = candidate()
    apply_classification(c, cls(serves_uk_customers_note="Ships UK-wide", **kw), CATS)
    c.classified_at, c.classification_model = NOW, "openai/gpt-6-luna"
    return c


@pytest.mark.parametrize("status", [ClassificationStatus.SKIPPED_LLM_DISABLED, ClassificationStatus.FAILED,
                                    ClassificationStatus.MISSING])  # fmt: skip
def test_a_rerun_without_classification_keeps_every_llm_field_including_the_version(status):
    old = classified_record(sells_new_musical_instruments=False)
    new = candidate(evidence=[ev("https://beckettsmusic.co.uk/other", "Boss RC-1 - Beckett's Music", "£99")])
    new.classification_status = status
    merged = merge_candidate(old, new)
    for f in LLM_FIELDS:
        assert getattr(merged, f) == getattr(old, f), f
    assert merged.classification_version == CLASSIFICATION_VERSION and merged.sells_new_musical_instruments is False
    assert len(merged.evidence) == 3  # evidence still accumulates


def test_a_new_completed_classification_replaces_every_llm_field():
    old = classified_record(sells_new_musical_instruments=False)
    new = classified_record(sells_new_musical_instruments=True, serves_uk_customers=UkSeller.UNKNOWN)
    merged = merge_candidate(old, new)
    assert merged.sells_new_musical_instruments is True and merged.serves_uk_customers is UkSeller.UNKNOWN


def test_llm_fields_cover_the_stage_8_1_additions():
    assert {"classification_version", "serves_uk_customers", "serves_uk_customers_note",
            "sells_new_musical_instruments"} <= set(LLM_FIELDS)  # fmt: skip


def test_frontend_mirrors_the_classification_version():
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
    m = re.search(r"export const CLASSIFICATION_VERSION = (\d+)", src)
    assert m and int(m.group(1)) == CLASSIFICATION_VERSION


def test_the_backfill_never_writes_the_classification_version():
    from app.agents.discovery import backfill as bf

    assert "classification_version" not in bf.CLASS_FIELDS
    assert set(bf.ClassFields.model_fields) == set(bf.CLASS_FIELDS)


# --------------------------------------------------------------------------- review API consistency


@pytest.fixture
def api(tmp_path):
    store = ReviewStore(tmp_path / "r.sqlite3")
    store.record_run(run_result([classified_record(), _named("usedonly.co.uk", "Used Only", False)],
                                mode=DiscoveryMode.LLM_ENABLED))  # fmt: skip
    with TestClient(create_app(make_settings(review_db_path=store.path))) as client:
        yield client


def _named(domain, name, sells_new):
    c = candidate(domain, name)
    apply_classification(c, cls(domain=domain, company_name=name, sells_new_musical_instruments=sells_new,
                                evidence_urls=[f"https://{domain}/boss-rc-5"]), CATS)  # fmt: skip
    return c


def listing(client):
    return {c["domain"]: c for c in client.get("/api/review/candidates").json()}


def test_detail_matches_the_list(api):
    for domain, row in listing(api).items():
        detail = api.get(f"/api/review/candidates/{domain}").json()
        assert {k: detail[k] for k in row} == row, domain


def test_used_only_retailer_is_shown_pending_and_can_be_approved_without_a_benchmark(api):
    row = listing(api)["usedonly.co.uk"]
    assert (row["search_basis"], row["auto_search_eligible"], row["sells_new_musical_instruments"]) == \
        ("NOT_SEARCHED", False, False)
    assert any("sells only used instruments" in r for r in row["search_eligibility_reasons"])
    r = api.post("/api/review/candidates/usedonly.co.uk/approve",
                 json={"reviewer": "Sam", "company_type": "specialist_retailer"})
    assert r.status_code == 201 and r.json()["benchmark_eligible"] is False
    row = listing(api)["usedonly.co.uk"]
    assert (row["search_basis"], row["search_eligible"], row["benchmark_eligible"]) == ("MANUAL_APPROVAL", True, False)
    cfg = api.get("/api/intelligence/config").json()
    t = {x["domain"]: x for x in cfg["search_targets"]}
    assert t["usedonly.co.uk"]["approval_status"] == "APPROVED" and t["usedonly.co.uk"]["price_benchmark"] is False


@pytest.mark.parametrize("bench", [False, True])
def test_manual_benchmark_permission_after_automatic_eligibility_is_only_the_reviewers_flag(api, bench):
    assert listing(api)["beckettsmusic.co.uk"]["search_basis"] == "AUTOMATIC"
    assert listing(api)["beckettsmusic.co.uk"]["benchmark_eligible"] is False
    api.post("/api/review/candidates/beckettsmusic.co.uk/approve",
             json={"reviewer": "Sam", "company_type": "retailer", "benchmark_eligible": bench})
    row = listing(api)["beckettsmusic.co.uk"]
    assert (row["search_basis"], row["benchmark_eligible"]) == ("MANUAL_APPROVAL", bench)


def test_a_new_classification_never_overwrites_a_decision(api):
    api.post("/api/review/candidates/beckettsmusic.co.uk/reject", json={"reviewer": "Sam"})
    store = api.app.state.review_store
    before = store.list_decisions()
    store.record_run(run_result([classified_record()], run_id="run-2", mode=DiscoveryMode.LLM_ENABLED))
    assert store.list_decisions() == before
    assert listing(api)["beckettsmusic.co.uk"]["search_basis"] == "NOT_SEARCHED"


def test_discovery_result_and_review_api_agree(catalogue, tmp_path):
    from app.api.review import _summary

    h = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS))
    _, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
    for c in result.discovery.candidates:
        s = _summary(h.review.get_candidate(c.domain), h.review.get_decision(c.domain), h.settings)
        assert s["auto_search_eligible"] is (c.approval_status.value == "AUTO_APPROVED"), c.domain


def test_reruns_keep_reviewer_decisions(catalogue, tmp_path):
    d = DomainDecisionRecord(domain="beckettsmusic.co.uk", decision=ReviewDecision.REJECTED, reviewer="Sam",
                             decided_at=NOW, notes="Not a competitor for us")  # fmt: skip
    h = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS), decisions=[d])
    for _ in range(2):
        _, result, _ = asyncio.run(h.run(product_ids=["SB-001"]))
        beck = next(c for c in result.discovery.candidates if c.domain == "beckettsmusic.co.uk")
        assert beck.approval_status.value == "REJECTED"
    assert h.review.get_decision("beckettsmusic.co.uk") == d.model_copy(update={"version": 1})
