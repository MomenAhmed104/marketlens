"""The separate UK eligibility subsystem is retired (Option B, 19 Nov 2026).

Kept from test_uk_eligibility_api.py, test_uk_eligibility_membership.py and
test_uk_store_integrity.py: the protections that do not depend on the gate itself.
- the canonical domain key helper (D1) still normalises and refuses ambiguous values;
- the old UK eligibility endpoints are gone (not silently re-routed);
- reviewer decisions never change a candidate's stored evidence, classification or UK
  evidence assessment, and decision history cannot be deleted or edited through the API.
Temporary databases only."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.domains import canonical_domain
from tests.search_fakes import make_settings
from tests.test_review_store import candidate, run

OLD_API = "/api/review/uk-eligibility"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(review_db_path=tmp_path / "review.sqlite3"))) as c:
        yield c


# --------------------------------------------------------------------------- D1: canonical domain key


@pytest.mark.parametrize("raw", ["bax-shop.co.uk", "Bax-Shop.CO.UK", "www.bax-shop.co.uk", "WWW.Bax-Shop.co.uk",
                                 "  www.bax-shop.co.uk.  "])  # fmt: skip
def test_canonical_domain_normalises_case_www_whitespace_and_trailing_dot(raw):
    assert canonical_domain(raw) == "bax-shop.co.uk"


@pytest.mark.parametrize("raw", ["", "bax-shop", "https://www.bax-shop.co.uk", "bax-shop.co.uk/path",
                                 "bax-shop.co.uk:443", "user@bax-shop.co.uk", "shop.bax-shop.co.uk",
                                 "bax shop.co.uk", "-bax.co.uk", "bax..co.uk", "co.uk.evil.example", "co.uk",
                                 "www.co.uk"])  # fmt: skip
def test_canonical_domain_rejects_ambiguous_or_invalid_values(raw):
    with pytest.raises(ValueError):
        canonical_domain(raw)


# --------------------------------------------------------------------------- the old endpoints are gone


@pytest.mark.parametrize("method,path", [
    ("get", OLD_API), ("get", f"{OLD_API}/andertons.co.uk"), ("put", f"{OLD_API}/andertons.co.uk"),
    ("put", f"{OLD_API}/andertons.co.uk/exception"),
])  # fmt: skip
def test_uk_eligibility_endpoints_are_removed(client, method, path):
    r = getattr(client, method)(path, **({"json": {}} if method == "put" else {}))
    assert r.status_code in (404, 405)


def test_openapi_lists_no_uk_eligibility_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "uk-eligibility" in p]


# --------------------------------------------------------------------------- decisions and evidence stay separate


def test_review_decisions_never_change_stored_evidence_or_classification(client):
    store = client.app.state.review_store
    store.record_run(run([candidate("beckettsmusic.co.uk")]))
    before = store.get_candidate("beckettsmusic.co.uk")
    r = client.post("/api/review/candidates/beckettsmusic.co.uk/approve",
                    json={"reviewer": "sam", "company_type": "retailer", "notes": "Checked the shop page"})
    assert r.status_code == 201 and r.json()["notes"] == "Checked the shop page"
    r = client.put("/api/review/decisions/beckettsmusic.co.uk",
                   json={"reviewer": "sam", "decision": "REJECTED", "company_type": None, "notes": "Changed mind"})
    assert r.status_code == 200
    assert store.get_candidate("beckettsmusic.co.uk") == before
    assert [h.notes for h in store.history("beckettsmusic.co.uk")] == ["Checked the shop page", "Changed mind"]


def test_no_delete_or_history_edit_routes_for_decisions(client):
    client.app.state.review_store.record_run(run([candidate("beckettsmusic.co.uk")]))
    client.post("/api/review/candidates/beckettsmusic.co.uk/approve", json={"reviewer": "sam", "company_type": None})
    assert client.delete("/api/review/decisions/beckettsmusic.co.uk").status_code == 405
    assert client.put("/api/review/decisions/beckettsmusic.co.uk/history", json={}).status_code in (404, 405)


def test_the_old_uk_override_fields_are_refused_on_decisions(client):
    """The category override is removed; a client still sending it gets a clear 422, not a silent drop."""
    client.app.state.review_store.record_run(run([candidate("beckettsmusic.co.uk")]))
    r = client.post("/api/review/candidates/beckettsmusic.co.uk/approve",
                    json={"reviewer": "sam", "company_type": "retailer", "uk_relevance_override": "UK_BASED"})
    assert r.status_code == 422
