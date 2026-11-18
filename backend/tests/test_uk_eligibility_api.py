"""UK eligibility review API (D5): assessments, exceptions, audit history, and the rules the store
enforces (canonical domains, versions, D2, D3), exercised over HTTP. Temporary databases only."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.review_store import ReviewStore
from tests.search_fakes import make_settings
from tests.test_review_store import candidate, run
from tests.uk_fakes import CONFIGURED_13, make_v1_db

API = "/api/review/uk-eligibility"

STRONG = {"url": "https://www.andertons.co.uk/store-finder", "quote": "Andertons Music Co, 58-59 Woodbridge Road, "
          "Guildford", "kind": "PHYSICAL_STORE", "source": "REVIEWER_SUPPLIED", "note": "Read 17 Nov 2026"}
NON_UK = {"url": "https://www.thomann.co.uk/compinfo_imprint.html", "quote": "Hans-Thomann-Strasse 1, Treppendorf",
          "kind": "NON_UK_OPERATOR", "source": "REVIEWER_SUPPLIED", "note": "Imprint read 17 Nov 2026"}


@pytest.fixture
def settings(tmp_path):
    return make_settings(review_db_path=tmp_path / "review.sqlite3")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def assess(status="UK_ELIGIBLE", version=0, **kw):
    body = {"status": status, "confidence": "HIGH", "reasoning": "Physical UK store verified",
            "evidence": {"UK_ELIGIBLE": [STRONG], "NOT_UK_ELIGIBLE": [NON_UK]}.get(status, []),
            "reviewer": "alex", "expected_version": version}  # fmt: skip
    body.update(kw)
    return body


def exc(search=True, benchmark=False, version=0, **kw):
    body = {"allow_search": search, "allow_benchmark": benchmark, "reason": "Explicit user approval 17 Nov 2026",
            "reviewer": "alex", "expected_version": version}  # fmt: skip
    body.update(kw)
    return body


def put(client, domain, body, path=""):
    return client.put(f"{API}/{domain}{path}", json=body)


# --------------------------------------------------------------------------- reads


def test_list_shows_configured_competitors_unassessed_and_blocked(client):
    body = client.get(API).json()
    assert body["uk_supported"] is True and body["schema_version"] == 2
    rows = {d["domain"]: d for d in body["domains"]}
    assert set(rows) == set(CONFIGURED_13)
    for d in rows.values():
        assert d["configured_competitor"] and d["eligibility"] is None and d["exception"] is None
        assert d["gate"]["evidence_status"] == "INSUFFICIENT_EVIDENCE" and not d["gate"]["search_allowed"]


def discover(client, *domains):
    client.app.state.review_store.record_run(run([candidate(d) for d in domains]))


def test_list_includes_assessed_and_excepted_discovered_domains(client):
    discover(client, "beckettsmusic.co.uk", "someshop.co.uk")  # writes need a known domain
    assert put(client, "beckettsmusic.co.uk", assess()).status_code == 200
    assert put(client, "someshop.co.uk", exc(), "/exception").status_code == 200
    rows = {d["domain"]: d for d in client.get(API).json()["domains"]}
    assert rows["beckettsmusic.co.uk"]["gate"]["search_allowed"] and not rows["beckettsmusic.co.uk"]["configured_competitor"]
    assert rows["someshop.co.uk"]["gate"]["search_basis"] == "EXCEPTION"


def test_get_unassessed_domain_is_insufficient_evidence_not_404(client):
    r = client.get(f"{API}/andertons.co.uk")
    assert r.status_code == 200
    d = r.json()
    assert d["eligibility"] is None and d["eligibility_history"] == [] and d["exception_history"] == []
    assert d["gate"]["assessed"] is False and d["gate"]["evidence_status"] == "INSUFFICIENT_EVIDENCE"


def test_get_canonicalises_the_domain(client):
    put(client, "andertons.co.uk", assess())
    d = client.get(f"{API}/WWW.Andertons.CO.UK").json()
    assert d["domain"] == "andertons.co.uk" and d["eligibility"]["status"] == "UK_ELIGIBLE"


# --------------------------------------------------------------------------- create / update


def test_create_then_update_with_history(client):
    r = put(client, "www.Andertons.co.uk", assess())
    assert r.status_code == 200
    d = r.json()
    assert d["domain"] == "andertons.co.uk" and d["eligibility"]["version"] == 1
    assert d["eligibility"]["reviewer"] == "alex" and d["eligibility"]["decided_at"]
    assert d["gate"]["search_allowed"] and d["gate"]["benchmark_allowed"] and d["gate"]["search_basis"] == "UK_ELIGIBLE"
    r = put(client, "andertons.co.uk", assess("INSUFFICIENT_EVIDENCE", version=1, reasoning="Store closed?"))
    d = r.json()
    assert r.status_code == 200 and d["eligibility"]["version"] == 2 and not d["gate"]["search_allowed"]
    assert [h["status"] for h in d["eligibility_history"]] == ["UK_ELIGIBLE", "INSUFFICIENT_EVIDENCE"]


def test_stale_expected_version_is_409(client):
    put(client, "andertons.co.uk", assess())
    r = put(client, "andertons.co.uk", assess("NOT_UK_ELIGIBLE", version=0))
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "VERSION_CONFLICT" and r.json()["detail"]["current_version"] == 1
    discover(client, "new.co.uk")
    assert put(client, "new.co.uk", assess(version=5)).json()["detail"]["current_version"] == 0
    assert client.get(f"{API}/andertons.co.uk").json()["eligibility"]["status"] == "UK_ELIGIBLE"


@pytest.mark.parametrize("missing", ["expected_version", "reviewer", "reasoning", "status", "confidence"])
def test_required_fields(client, missing):
    body = assess()
    del body[missing]
    assert put(client, "andertons.co.uk", body).status_code == 422


@pytest.mark.parametrize("field,value", [("reviewer", "   "), ("reasoning", ""), ("expected_version", -1),
                                         ("status", "ELIGIBLE"), ("status", "uk_eligible"), ("confidence", "SURE")])
def test_invalid_values(client, field, value):
    assert put(client, "andertons.co.uk", assess(**{field: value})).status_code == 422


@pytest.mark.parametrize("domain", ["shop.andertons.co.uk", "andertons", "co.uk", "andertons.co.uk:443",
                                    "bad_domain.co.uk"])
def test_invalid_domains(client, domain):
    r = put(client, domain, assess())
    assert r.status_code == 422 and r.json()["detail"]["error"] == "INVALID_DOMAIN"
    assert client.get(f"{API}/{domain}").status_code == 422
    assert put(client, domain, exc(), "/exception").status_code == 422


def test_record_rules_are_enforced(client):
    weak = {**STRONG, "kind": "WEAK_SIGNAL", "quote": "Free UK delivery"}
    r = put(client, "andertons.co.uk", assess(evidence=[weak]))
    assert r.status_code == 422 and any("strong evidence" in p for p in r.json()["detail"]["problems"])
    assert put(client, "thomann.co.uk", assess("NOT_UK_ELIGIBLE", evidence=[])).status_code == 422
    no_note = {**STRONG, "note": None}
    assert put(client, "andertons.co.uk", assess(evidence=[no_note])).status_code == 422


# --------------------------------------------------------------------------- evidence verification


def test_saved_evidence_must_match_the_stored_discovery_evidence(client):
    store: ReviewStore = client.app.state.review_store
    store.record_run(run([candidate("beckettsmusic.co.uk", urls=("https://beckettsmusic.co.uk/visit",))]))
    saved = {"url": "https://beckettsmusic.co.uk/visit", "quote": "boss  rc-5", "kind": "PHYSICAL_STORE",
             "source": "SAVED_EVIDENCE"}  # stored title is "Boss RC-5"; whitespace and case aside
    assert put(client, "beckettsmusic.co.uk", assess(evidence=[saved])).status_code == 200
    for bad in ({**saved, "quote": "Our shop in Ely"}, {**saved, "url": "https://beckettsmusic.co.uk/other"}):
        r = put(client, "beckettsmusic.co.uk", assess(evidence=[bad], version=1))
        assert r.status_code == 422 and r.json()["detail"]["error"] == "UNVERIFIED_EVIDENCE"
    r = put(client, "andertons.co.uk", assess(evidence=[{**saved, "url": "https://www.andertons.co.uk/x"}]))
    assert r.status_code == 422 and "no" in r.json()["detail"]["problems"][0]  # nothing saved for this domain


# --------------------------------------------------------------------------- exceptions


def test_grant_and_independently_revoke_scopes(client):
    r = put(client, "guitarguitar.co.uk", exc(search=True, benchmark=True), "/exception")
    g = r.json()["gate"]
    assert r.status_code == 200 and g["search_allowed"] and g["benchmark_allowed"] and g["search_basis"] == "EXCEPTION"
    assert r.json()["eligibility"] is None  # an exception never creates or changes an assessment
    # Revoke benchmarking only.
    r = put(client, "guitarguitar.co.uk", exc(search=True, benchmark=False, version=1), "/exception")
    g = r.json()["gate"]
    assert g["search_allowed"] and not g["benchmark_allowed"] and g["benchmark_basis"] == "BLOCKED"
    # Benchmarking without search is refused by the model.
    r = put(client, "guitarguitar.co.uk", exc(search=False, benchmark=True, version=2), "/exception")
    assert r.status_code == 422 and any("needs a search exception" in p for p in r.json()["detail"]["problems"])
    # Revoke everything.
    r = put(client, "guitarguitar.co.uk", exc(search=False, benchmark=False, version=2), "/exception")
    assert r.status_code == 200 and not r.json()["gate"]["search_allowed"]
    hist = r.json()["exception_history"]
    assert [(h["version"], h["allow_search"], h["allow_benchmark"]) for h in hist] == [
        (1, True, True), (2, True, False), (3, False, False)]  # fmt: skip
    assert all(h["approval"] == "EXPLICIT_USER_APPROVAL" and h["reviewer"] == "alex" for h in hist)


def test_gibson_search_only_exception_is_never_a_benchmark(client):
    r = put(client, "gibson.com", exc(search=True, benchmark=False), "/exception")
    assert r.json()["gate"]["search_allowed"] and not r.json()["gate"]["benchmark_allowed"]
    cfg = client.get("/api/intelligence/config").json()
    g = {t["domain"]: t for t in cfg["search_targets"]}["gibson.com"]
    assert g["price_benchmark"] is False and g["uk_gate"]["benchmark_allowed"] is False


@pytest.mark.parametrize("missing", ["reviewer", "reason", "expected_version", "allow_search", "allow_benchmark"])
def test_exception_required_fields(client, missing):
    body = exc()
    del body[missing]
    assert put(client, "gibson.com", body, "/exception").status_code == 422


def test_exception_stale_version_is_409(client):
    put(client, "gibson.com", exc(), "/exception")
    r = put(client, "gibson.com", exc(benchmark=False, search=False, version=0), "/exception")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "VERSION_CONFLICT"


@pytest.mark.parametrize("status", ["UK_ELIGIBLE", "NOT_UK_ELIGIBLE"])
def test_d2_exceptions_only_for_insufficient_evidence(client, status):
    domain = "andertons.co.uk" if status == "UK_ELIGIBLE" else "thomann.co.uk"
    put(client, domain, assess(status))
    r = put(client, domain, exc(search=True, benchmark=True), "/exception")
    assert r.status_code == 409
    assert r.json()["detail"] == {**r.json()["detail"], "error": "EXCEPTION_NOT_ALLOWED", "evidence_status": status}
    d = client.get(f"{API}/{domain}").json()
    assert d["exception"] is None and d["exception_history"] == []


def test_d2_assessed_insufficient_domain_can_receive_an_exception(client):
    put(client, "bax-shop.co.uk", assess("INSUFFICIENT_EVIDENCE"))
    r = put(client, "bax-shop.co.uk", exc(search=True, benchmark=True), "/exception")
    assert r.status_code == 200 and r.json()["gate"]["benchmark_allowed"] and r.json()["gate"]["assessed"]


def test_not_uk_eligible_cannot_be_overridden(client):
    put(client, "thomann.co.uk", assess("NOT_UK_ELIGIBLE"))
    for spelling in ("thomann.co.uk", "WWW.THOMANN.co.uk"):
        assert put(client, spelling, exc(search=True), "/exception").status_code == 409
    d = client.get(f"{API}/thomann.co.uk").json()
    assert not d["gate"]["search_allowed"] and d["gate"]["search_basis"] == "BLOCKED"
    # A revocation is always accepted and grants nothing.
    r = put(client, "thomann.co.uk", exc(search=False, benchmark=False), "/exception")
    assert r.status_code == 200 and not r.json()["gate"]["search_allowed"]


def test_d3_status_changes_revoke_and_never_revive(client):
    put(client, "bax-shop.co.uk", assess("INSUFFICIENT_EVIDENCE"))
    put(client, "bax-shop.co.uk", exc(search=True, benchmark=True), "/exception")
    d = put(client, "bax-shop.co.uk", assess("UK_ELIGIBLE", version=1, reviewer="sam")).json()
    assert d["exception"]["allow_search"] is False and d["exception"]["revoked_by_status_change"] is True
    assert d["exception"]["reviewer"] == "sam" and "INSUFFICIENT_EVIDENCE to UK_ELIGIBLE" in d["exception"]["reason"]
    assert d["gate"]["search_basis"] == "UK_ELIGIBLE"
    d = put(client, "bax-shop.co.uk", assess("INSUFFICIENT_EVIDENCE", version=2)).json()
    assert not d["gate"]["search_allowed"] and not d["gate"]["benchmark_allowed"]
    assert [h["revoked_by_status_change"] for h in d["exception_history"]] == [False, True]


def test_d3_change_to_not_uk_eligible_revokes(client):
    put(client, "guitarguitar.co.uk", exc(search=True, benchmark=True), "/exception")
    d = put(client, "guitarguitar.co.uk", assess("NOT_UK_ELIGIBLE")).json()
    assert d["exception"]["revoked_by_status_change"] and not d["gate"]["search_allowed"]


# --------------------------------------------------------------------------- forged fields


@pytest.mark.parametrize("field,value", [
    ("price_benchmark", True), ("benchmark_eligible", True), ("competitor_class", "DIRECT_COMPETITOR"),
    ("company_type", "retailer"), ("approval_status", "APPROVED"), ("search_allowed", True),
    ("benchmark_allowed", True), ("gate", {"search_allowed": True}), ("version", 9), ("decided_at", "2020-01-01"),
    ("domain", "andertons.co.uk"), ("approval", "EXPLICIT_USER_APPROVAL"),
])  # fmt: skip
def test_forged_fields_are_rejected_on_both_writes(client, field, value):
    # Added after building the body, so `version` is sent as a field, not used as expected_version.
    assert put(client, "thomann.co.uk", {**assess("NOT_UK_ELIGIBLE"), field: value}).status_code == 422
    assert put(client, "gibson.com", {**exc(), field: value}, "/exception").status_code == 422


def test_forged_revocation_flag_is_rejected(client):
    assert put(client, "gibson.com", exc(revoked_by_status_change=True), "/exception").status_code == 422


def test_writes_never_touch_approval_classification_or_benchmark_flags(client):
    store: ReviewStore = client.app.state.review_store
    store.record_run(run([candidate("beckettsmusic.co.uk")]))
    before = (store.list_decisions(), store.get_candidate("beckettsmusic.co.uk"))
    put(client, "beckettsmusic.co.uk", assess("INSUFFICIENT_EVIDENCE"))
    put(client, "beckettsmusic.co.uk", exc(search=True, benchmark=True), "/exception")
    assert (store.list_decisions(), store.get_candidate("beckettsmusic.co.uk")) == before
    cfg = client.get("/api/intelligence/config").json()
    assert "beckettsmusic.co.uk" not in {t["domain"] for t in cfg["search_targets"]}  # still needs approval


def test_no_delete_or_history_edit_routes(client):
    put(client, "andertons.co.uk", assess())
    for method in ("delete", "patch", "post"):
        assert getattr(client, method)(f"{API}/andertons.co.uk").status_code == 405
    assert client.delete(f"{API}/andertons.co.uk/exception").status_code == 405
    assert client.put(f"{API}/andertons.co.uk/history", json={}).status_code in (404, 405)


# --------------------------------------------------------------------------- version-1 database


def test_version_1_database_reads_blocked_and_writes_503_without_changing_the_file(tmp_path):
    db = make_v1_db(tmp_path / "v1.sqlite3")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    with TestClient(create_app(make_settings(review_db_path=db))) as client:
        body = client.get(API).json()
        assert body["uk_supported"] is False and body["schema_version"] == 1
        assert all(not d["gate"]["search_allowed"] for d in body["domains"])
        assert client.get(f"{API}/andertons.co.uk").json()["uk_supported"] is False
        r = put(client, "andertons.co.uk", assess())
        assert r.status_code == 503 and r.json()["detail"]["error"] == "MIGRATION_REQUIRED"
        assert put(client, "gibson.com", exc(), "/exception").status_code == 503
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
