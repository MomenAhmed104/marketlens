"""Matching CLI (audit fix D4): a saved run file is never trusted for who a competitor is.

Competitors are rebuilt from the catalogue and a temporary review database, exactly as
POST /api/matching/evaluate does; without readable trusted data the CLI fails closed.
Temporary databases and output directories only; no network."""

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from app.schemas.common import DomainApprovalStatus as DS
from app.schemas.competitor import CompanyType as T
from app.schemas.review import ReviewDecision
from app.services.matching import cli
from app.services.review_store import ReviewStore
from tests.matching_fakes import target
from tests.search_fakes import make_settings
from tests.test_intelligence_agent import dec
from tests.test_search_targets import AND_RC5, fresh, payload
from tests.uk_fakes import make_v1_db, make_v2_db, table_snapshot

GIBSON = "https://www.gibson.com/boss-rc-5"
PENDING = "https://www.pending-shop.co.uk/boss-rc-5-loop-station-pedal"


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = make_settings(review_db_path=tmp_path / "review.sqlite3")
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    return settings, tmp_path


def run_cli(tmp_path, *cands, review_db=None, raw: str | None = None):
    f = tmp_path / "run.json"
    f.write_text(raw if raw is not None else payload(*cands), encoding="utf-8")
    argv = [str(f), "--out-dir", str(tmp_path / "out")]
    if review_db is not None:
        argv += ["--review-db", str(review_db)]
    code = cli.main(argv)
    out = tmp_path / "out" / "intel-test.json"
    return code, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)


def by_domain(result):
    return {c["competitor"]["domain"]: c for c in result["products"][0]["candidates"]}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- tampered files


def test_tampered_file_cannot_make_gibson_a_benchmark(env):
    settings, tmp = env
    ReviewStore(settings.review_db_path)  # an empty, readable review database
    tampered = target("gibson.com", ctype=T.RETAILER, benchmark=True)  # its reference-only flag is in the workbook
    code, result = run_cli(tmp, fresh(GIBSON, tampered))
    assert code == 0
    g = by_domain(result)["gibson.com"]
    assert not g["comparison"]["eligible"] and g["comparison"]["difference_gbp"] is None
    assert g["competitor"]["price_benchmark"] is False and g["competitor"]["company_type"] == "manufacturer"
    assert any("replaced" in w for w in result["warnings"])


@pytest.mark.parametrize("decided", ["pending", "rejected", "approved_without_flag"])
def test_tampered_file_cannot_make_an_unflagged_domain_a_benchmark(env, decided):
    settings, tmp = env
    store = ReviewStore(settings.review_db_path)
    if decided == "rejected":
        store.create_decision(dec("pending-shop.co.uk", ReviewDecision.REJECTED))
    elif decided == "approved_without_flag":
        store.create_decision(dec("pending-shop.co.uk", bench=False))
    tampered = target("pending-shop.co.uk", benchmark=True, status=DS.APPROVED)  # claims a benchmark
    code, result = run_cli(tmp, fresh(PENDING, tampered))
    c = by_domain(result)["pending-shop.co.uk"]
    assert code == 0 and not c["comparison"]["eligible"] and c["competitor"]["price_benchmark"] is False


def test_page_on_another_domain_is_never_a_benchmark(env):
    settings, tmp = env
    ReviewStore(settings.review_db_path)
    code, result = run_cli(tmp, fresh("https://www.evil.example/boss-rc-5-loop-station-pedal",
                                      target("andertons.co.uk")))  # fmt: skip
    c = result["products"][0]["candidates"][0]
    assert code == 0 and not c["comparison"]["eligible"] and c["competitor"]["price_benchmark"] is False


# --------------------------------------------------------------------------- legitimate use


def test_configured_competitor_is_compared_with_server_flags(env, capsys):
    settings, tmp = env
    ReviewStore(settings.review_db_path)
    # The file under-claims (no benchmark flag): the workbook flag still wins.
    code, result = run_cli(tmp, fresh(AND_RC5, target("andertons.co.uk", benchmark=False)))
    a = by_domain(result)["andertons.co.uk"]
    assert code == 0 and a["match_status"] == "EXACT_MATCH" and a["comparison"]["eligible"]
    assert a["competitor"]["price_benchmark"] is True and "uk_gate" not in a["competitor"]
    assert a["comparison"]["difference_gbp"] is not None
    out = capsys.readouterr()
    assert "Full result:" in out.out and "andertons.co.uk" in out.out and "warning:" not in out.err


def test_reviewer_flagged_domain_is_compared(env):
    settings, tmp = env
    ReviewStore(settings.review_db_path).create_decision(dec("pending-shop.co.uk", bench=True))
    code, result = run_cli(tmp, fresh(PENDING, target("pending-shop.co.uk", benchmark=False)))
    c = by_domain(result)["pending-shop.co.uk"]
    assert code == 0 and c["competitor"]["price_benchmark"] is True and c["comparison"]["eligible"]


@pytest.mark.parametrize("variant", ["without_uk_gate", "with_legacy_uk_gate"])
def test_older_run_files_still_load(env, variant):
    """Run files from before and during the UK gate both load; a legacy `uk_gate` key is ignored."""
    settings, tmp = env
    ReviewStore(settings.review_db_path)
    old = json.loads(payload(fresh(AND_RC5, target("andertons.co.uk"))))
    if variant == "with_legacy_uk_gate":
        old["products"][0]["candidates"][0]["competitor"]["uk_gate"] = {"domain": "andertons.co.uk",
                                                                        "benchmark_allowed": False}
    code, result = run_cli(tmp, raw=json.dumps(old))
    assert code == 0 and by_domain(result)["andertons.co.uk"]["comparison"]["eligible"]


def test_invalid_run_file_and_unknown_product_still_exit_2(env, capsys):
    settings, tmp = env
    assert run_cli(tmp, raw="{}")[0] == 2
    assert "not a valid Phase 5 run result" in capsys.readouterr().err
    bad = json.loads(payload(fresh(AND_RC5, target())))
    bad["products"][0]["product"]["product_id"] = "SB-999"
    assert run_cli(tmp, raw=json.dumps(bad))[0] == 2
    assert "Unknown product id" in capsys.readouterr().err


# --------------------------------------------------------------------------- missing or invalid trusted data


def assert_fail_closed(result, capsys):
    cands = result["products"][0]["candidates"]
    assert cands and not any(c["comparison"]["eligible"] for c in cands)
    # Configured competitors included: without readable trusted data nothing is a benchmark.
    assert all(c["competitor"]["price_benchmark"] is False for c in cands)
    assert any("Trusted review data unavailable" in w for w in result["warnings"])
    assert "no candidate can be a price benchmark" in capsys.readouterr().err


def test_missing_review_database_fails_closed_and_is_not_created(env, capsys):
    settings, tmp = env
    code, result = run_cli(tmp, fresh(AND_RC5, target("andertons.co.uk")))
    assert code == 0
    assert_fail_closed(result, capsys)
    assert not settings.review_db_path.exists()


@pytest.mark.parametrize("kind", ["garbage", "version_9", "version_0_empty"])
def test_unreadable_or_unsupported_review_database_fails_closed_and_is_untouched(env, capsys, kind):
    settings, tmp = env
    db = tmp / f"{kind}.sqlite3"
    if kind == "garbage":
        db.write_bytes(b"this is not a database" * 100)
    else:
        with closing(sqlite3.connect(db)) as c:
            c.execute(f"PRAGMA user_version = {9 if kind == 'version_9' else 0}")
            c.execute("CREATE TABLE t (x)") if kind == "version_9" else None
            c.commit()
    before = sha(db)
    code, result = run_cli(tmp, fresh(AND_RC5, target("andertons.co.uk")), review_db=db)
    assert code == 0
    assert_fail_closed(result, capsys)
    assert sha(db) == before


def test_corrupt_decision_record_fails_closed(env, capsys):
    settings, tmp = env
    ReviewStore(settings.review_db_path).create_decision(dec("pending-shop.co.uk", bench=True))
    with closing(sqlite3.connect(settings.review_db_path)) as c:
        c.execute("""UPDATE domain_decisions SET record = '{"domain": "pending-shop.co.uk"}'""")
        c.commit()
    code, result = run_cli(tmp, fresh(AND_RC5, target("andertons.co.uk")))
    assert code == 0
    assert_fail_closed(result, capsys)


def test_version_1_database_is_read_only_and_configured_flags_apply(env, capsys):
    """Like the real database today: schema version 1. Read without writing; workbook flags apply."""
    settings, tmp = env
    db = make_v1_db(tmp / "v1.sqlite3")
    before = sha(db)
    code, result = run_cli(tmp, fresh(AND_RC5, target("andertons.co.uk", benchmark=False)), review_db=db)
    a = by_domain(result)["andertons.co.uk"]
    assert code == 0 and a["comparison"]["eligible"] and a["competitor"]["price_benchmark"] is True
    assert not any("Trusted review data unavailable" in w for w in result["warnings"])
    assert sha(db) == before


def test_legacy_version_2_database_is_read_only_and_its_uk_rows_are_ignored(env, capsys):
    """A file left by the retired UK gate (andertons NOT_UK_ELIGIBLE, a Gibson benchmark exception):
    readable, unchanged, and its UK rows influence nothing."""
    settings, tmp = env
    db = make_v2_db(tmp / "v2.sqlite3")
    before, uk_rows = sha(db), table_snapshot(db)
    code, result = run_cli(tmp, fresh(AND_RC5, target("andertons.co.uk", benchmark=False)),
                           fresh(GIBSON, target("gibson.com", ctype=T.MANUFACTURER, benchmark=True)),
                           review_db=db)  # fmt: skip
    cands = by_domain(result)
    assert code == 0 and cands["andertons.co.uk"]["comparison"]["eligible"]
    assert cands["gibson.com"]["competitor"]["price_benchmark"] is False  # the exception means nothing
    assert sha(db) == before and table_snapshot(db) == uk_rows
