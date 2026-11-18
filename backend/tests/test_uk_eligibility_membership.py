"""UK eligibility review API refinements: only configured competitors and discovered domains can be
assessed or given exceptions, and the list covers every configured and discovered domain, assessed
or not, once per canonical domain. Temporary databases only."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.search_fakes import make_settings
from tests.test_review_store import candidate, run
from tests.test_uk_eligibility_api import API, assess, exc, put
from tests.uk_fakes import CONFIGURED_13, S, make_v1_db, uk_exception, uk_record

VIEW_FIELDS = {"domain", "configured_competitor", "in_review_queue", "assessable", "eligibility", "exception", "gate"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(review_db_path=tmp_path / "review.sqlite3"))) as c:
        yield c


def discover(client, *domains):
    client.app.state.review_store.record_run(run([candidate(d) for d in domains]))


def rows(client):
    body = client.get(API).json()
    domains = [d["domain"] for d in body["domains"]]
    assert len(domains) == len(set(domains)), "duplicate domains in the list"
    return {d["domain"]: d for d in body["domains"]}


# --------------------------------------------------------------------------- who can be assessed


@pytest.mark.parametrize("spelling", ["andertons.co.uk", "WWW.Andertons.CO.UK"])
def test_configured_competitor_can_be_assessed_and_excepted(client, spelling):
    assert put(client, spelling, assess()).status_code == 200
    assert put(client, "www.gibson.com", exc(), "/exception").status_code == 200


@pytest.mark.parametrize("spelling", ["beckettsmusic.co.uk", "WWW.BeckettsMusic.CO.UK"])
def test_discovered_domain_can_be_assessed_and_excepted_under_any_spelling(client, spelling):
    discover(client, "beckettsmusic.co.uk")
    r = put(client, spelling, assess("INSUFFICIENT_EVIDENCE"))
    assert r.status_code == 200 and r.json()["domain"] == "beckettsmusic.co.uk"
    r = put(client, spelling, exc(search=True, benchmark=True), "/exception")
    assert r.status_code == 200 and r.json()["gate"]["benchmark_allowed"]


@pytest.mark.parametrize("domain", ["unknown-shop.co.uk", "WWW.Unknown-Shop.co.uk", "pmtonline.co.uk"])
def test_unknown_domains_cannot_be_assessed_or_excepted(client, domain):
    for r in (put(client, domain, assess()), put(client, domain, exc(), "/exception")):
        assert r.status_code == 404 and r.json()["detail"]["error"] == "UNKNOWN_DOMAIN"
    store = client.app.state.review_store
    key = domain.lower().removeprefix("www.")
    assert store.get_uk_eligibility(key) is None and store.uk_eligibility_history(key) == []
    assert store.get_uk_exception(key) is None and store.uk_exception_history(key) == []
    assert key not in rows(client)


def test_invalid_domain_is_still_422_before_membership(client):
    # (A URL with slashes never reaches the handler: it does not match the route at all.)
    for d in ("shop.andertons.co.uk", "andertons.co.uk:443", "andertons", "unknown_shop.co.uk"):
        r = put(client, d, assess())
        assert r.status_code == 422 and r.json()["detail"]["error"] == "INVALID_DOMAIN"


def test_unknown_domain_on_a_version_1_database_is_404_and_known_is_503(tmp_path):
    with TestClient(create_app(make_settings(review_db_path=make_v1_db(tmp_path / "v1.sqlite3")))) as client:
        assert put(client, "unknown-shop.co.uk", assess()).status_code == 404
        assert put(client, "andertons.co.uk", assess()).status_code == 503


# --------------------------------------------------------------------------- the list


def test_list_includes_unassessed_discovered_candidates(client):
    discover(client, "beckettsmusic.co.uk", "idealo.co.uk")
    r = rows(client)
    assert set(r) == {*CONFIGURED_13, "beckettsmusic.co.uk", "idealo.co.uk"}
    b = r["beckettsmusic.co.uk"]
    assert set(b) == VIEW_FIELDS
    assert (b["configured_competitor"], b["in_review_queue"], b["assessable"]) == (False, True, True)
    assert b["eligibility"] is None and b["exception"] is None
    assert b["gate"]["evidence_status"] == "INSUFFICIENT_EVIDENCE" and not b["gate"]["assessed"]
    assert not b["gate"]["search_allowed"] and not b["gate"]["benchmark_allowed"]


def test_configured_and_discovered_duplicates_are_merged(client):
    discover(client, "andertons.co.uk", "gibson.com")  # also configured competitors
    r = rows(client)
    assert len(r) == len(CONFIGURED_13)
    for d in ("andertons.co.uk", "gibson.com"):
        assert r[d]["configured_competitor"] and r[d]["in_review_queue"] and r[d]["assessable"]
    assert r["thomann.co.uk"]["in_review_queue"] is False and r["thomann.co.uk"]["assessable"] is True


def test_stored_assessments_and_exceptions_are_merged_into_entries(client):
    discover(client, "beckettsmusic.co.uk")
    put(client, "WWW.andertons.co.uk", assess())
    put(client, "thomann.co.uk", assess("NOT_UK_ELIGIBLE"))
    put(client, "gibson.com", exc(search=True, benchmark=False), "/exception")
    put(client, "beckettsmusic.co.uk", assess("INSUFFICIENT_EVIDENCE"))
    put(client, "beckettsmusic.co.uk", exc(search=True, benchmark=True), "/exception")
    r = rows(client)
    assert r["andertons.co.uk"]["eligibility"]["status"] == "UK_ELIGIBLE" and r["andertons.co.uk"]["gate"]["benchmark_allowed"]
    assert r["thomann.co.uk"]["eligibility"]["status"] == "NOT_UK_ELIGIBLE" and not r["thomann.co.uk"]["gate"]["search_allowed"]
    g = r["gibson.com"]
    assert g["eligibility"] is None and g["exception"]["allow_search"] and not g["exception"]["allow_benchmark"]
    assert g["gate"]["search_basis"] == "EXCEPTION" and g["gate"]["benchmark_basis"] == "BLOCKED"
    b = r["beckettsmusic.co.uk"]
    assert b["eligibility"]["status"] == "INSUFFICIENT_EVIDENCE" and b["gate"]["benchmark_basis"] == "EXCEPTION"
    assert sum(1 for d in r.values() if d["eligibility"] is not None) == 3


def test_a_stored_record_for_an_unlisted_domain_stays_readable_but_not_writable(client):
    # Only possible through a direct store write (e.g. a record made before this restriction).
    store = client.app.state.review_store
    store.put_uk_eligibility(uk_record("orphan.co.uk", S.INSUFFICIENT_EVIDENCE))
    store.put_uk_exception(uk_exception("orphan.co.uk", search=True))
    d = client.get(f"{API}/www.orphan.co.uk").json()
    assert d["eligibility"]["status"] == "INSUFFICIENT_EVIDENCE" and d["assessable"] is False
    assert rows(client)["orphan.co.uk"]["assessable"] is False
    assert put(client, "orphan.co.uk", assess(version=1)).status_code == 404


def test_detail_reports_membership_flags(client):
    discover(client, "beckettsmusic.co.uk")
    d = client.get(f"{API}/beckettsmusic.co.uk").json()
    assert (d["configured_competitor"], d["in_review_queue"], d["assessable"]) == (False, True, True)
    d = client.get(f"{API}/andertons.co.uk").json()
    assert (d["configured_competitor"], d["in_review_queue"], d["assessable"]) == (True, False, True)
    d = client.get(f"{API}/unknown-shop.co.uk").json()  # reads are not restricted
    assert d["assessable"] is False and d["gate"]["evidence_status"] == "INSUFFICIENT_EVIDENCE"


def test_review_writes_leave_classification_and_approval_untouched(client):
    discover(client, "beckettsmusic.co.uk")
    store = client.app.state.review_store
    before = (store.get_candidate("beckettsmusic.co.uk"), store.list_decisions())
    put(client, "beckettsmusic.co.uk", assess())
    client.get(API)
    assert (store.get_candidate("beckettsmusic.co.uk"), store.list_decisions()) == before


# --------------------------------------------------------------------------- manufacturer policy unchanged


def test_yamaha_assessed_through_the_api_is_a_manufacturer_benchmark(client):
    from tests.matching_fakes import target
    from tests.test_manufacturer_benchmark import YAMAHA, YAMAHA_RC5
    from tests.test_uk_gate_paths import evaluate, fresh

    assert put(client, "www.yamahamusiclondon.com", assess()).status_code == 200
    c = evaluate(client, fresh(YAMAHA_RC5, target(YAMAHA)))["products"][0]["candidates"][0]
    assert c["competitor"]["company_type"] == "manufacturer" and c["competitor"]["price_benchmark"] is True
    assert c["comparison"]["eligible"]
    assert put(client, "gibson.com", assess(), "").status_code == 200  # Gibson UK-eligible...
    g = evaluate(client, fresh("https://www.gibson.com/boss-rc-5-loop-station-pedal",
                               target("gibson.com")))["products"][0]["candidates"][0]  # fmt: skip
    assert not g["comparison"]["eligible"] and g["competitor"]["price_benchmark"] is False  # ...never a benchmark
