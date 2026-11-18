"""Matching CLI (audit fix D4): a saved run file is never trusted for who a competitor is.

Competitors are rebuilt from the catalogue and a temporary review database, exactly as
POST /api/matching/evaluate does; without readable trusted data the CLI fails closed.
Temporary databases and output directories only; no network."""

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from app.schemas.competitor import CompanyType as T
from app.services.matching import cli
from app.services.review_store import ReviewStore
from tests.matching_fakes import target
from tests.search_fakes import make_settings
from tests.test_uk_gate_paths import AND_RC5, fresh, payload
from tests.uk_fakes import S, make_v1_db, seed_store, uk_exception, uk_record

GIBSON = "https://www.gibson.com/boss-rc-5"
THOMANN = "https://www.thomann.co.uk/boss_rc5.htm"


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
    # Even a (search + benchmark) exception for Gibson: its reference-only flag comes from the workbook.
    seed_store(ReviewStore(settings.review_db_path), exceptions=[uk_exception("gibson.com", True, True)])
    tampered = target("gibson.com", ctype=T.RETAILER, benchmark=True, uk_eligible=True)
    code, result = run_cli(tmp, fresh(GIBSON, tampered))
    assert code == 0
    g = by_domain(result)["gibson.com"]
    assert not g["comparison"]["eligible"] and g["comparison"]["difference_gbp"] is None
    assert g["competitor"]["price_benchmark"] is False and g["competitor"]["company_type"] == "manufacturer"
    assert any("replaced" in w for w in result["warnings"])


@pytest.mark.parametrize("seeded", ["not_uk_eligible", "unassessed"])
def test_tampered_file_cannot_make_a_non_uk_eligible_domain_a_benchmark(env, seeded):
    settings, tmp = env
    store = ReviewStore(settings.review_db_path)
    if seeded == "not_uk_eligible":
        store.put_uk_eligibility(uk_record("thomann.co.uk", S.NOT_UK_ELIGIBLE))
    tampered = target("thomann.co.uk", benchmark=True, uk_eligible=True)  # claims UK_ELIGIBLE
    code, result = run_cli(tmp, fresh(THOMANN, tampered))
    t = by_domain(result)["thomann.co.uk"]
    assert code == 0 and not t["comparison"]["eligible"]
    expected = "NOT_UK_ELIGIBLE" if seeded == "not_uk_eligible" else "INSUFFICIENT_EVIDENCE"
    assert t["competitor"]["uk_gate"]["evidence_status"] == expected
    assert t["competitor"]["uk_gate"]["benchmark_allowed"] is False


def test_page_on_another_domain_is_never_a_benchmark(env):
    settings, tmp = env
    seed_store(ReviewStore(settings.review_db_path), ["andertons.co.uk"])
    code, result = run_cli(tmp, fresh("https://www.evil.example/boss-rc-5-loop-station-pedal",
                                      target("andertons.co.uk", uk_eligible=True)))  # fmt: skip
    c = result["products"][0]["candidates"][0]
    assert code == 0 and not c["comparison"]["eligible"] and c["competitor"]["price_benchmark"] is False


# --------------------------------------------------------------------------- legitimate use


def test_legitimate_uk_eligible_competitor_is_compared_with_server_flags(env, capsys):
    settings, tmp = env
    seed_store(ReviewStore(settings.review_db_path), ["andertons.co.uk"])
    # The file under-claims (no benchmark, no UK clearance): trusted state still wins.
    code, result = run_cli(tmp, fresh(AND_RC5, target("andertons.co.uk", benchmark=False, uk_eligible=False)))
    a = by_domain(result)["andertons.co.uk"]
    assert code == 0 and a["match_status"] == "EXACT_MATCH" and a["comparison"]["eligible"]
    assert a["competitor"]["price_benchmark"] is True and a["competitor"]["uk_gate"]["search_basis"] == "UK_ELIGIBLE"
    assert a["comparison"]["difference_gbp"] is not None
    out = capsys.readouterr()
    assert "Full result:" in out.out and "andertons.co.uk" in out.out and "warning:" not in out.err


def test_pre_gate_run_files_still_load(env):
    """Older Phase 5 files have no `uk_gate` on their competitors; they are rebuilt, not rejected."""
    settings, tmp = env
    seed_store(ReviewStore(settings.review_db_path), ["andertons.co.uk"])
    old = json.loads(payload(fresh(AND_RC5, target("andertons.co.uk"))))
    del old["products"][0]["candidates"][0]["competitor"]["uk_gate"]
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
    assert all(c["competitor"]["uk_gate"]["search_allowed"] is False for c in cands)
    assert any("Trusted review data unavailable" in w for w in result["warnings"])
    assert "no candidate can be a price benchmark" in capsys.readouterr().err


def test_missing_review_database_fails_closed_and_is_not_created(env, capsys):
    settings, tmp = env
    code, result = run_cli(tmp, fresh(AND_RC5, target("andertons.co.uk", uk_eligible=True)))
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
    code, result = run_cli(tmp, fresh(AND_RC5, target("andertons.co.uk", uk_eligible=True)), review_db=db)
    assert code == 0
    assert_fail_closed(result, capsys)
    assert sha(db) == before


def test_corrupt_uk_record_fails_closed(env, capsys):
    settings, tmp = env
    store = ReviewStore(settings.review_db_path)
    seed_store(store, ["andertons.co.uk"])
    with closing(sqlite3.connect(settings.review_db_path)) as c:
        c.execute("UPDATE uk_eligibility SET record = '{\"domain\": \"andertons.co.uk\"}'")
        c.commit()
    code, result = run_cli(tmp, fresh(AND_RC5, target("andertons.co.uk", uk_eligible=True)))
    assert code == 0
    assert_fail_closed(result, capsys)


def test_version_1_database_is_read_only_and_blocks_every_benchmark(env, capsys):
    """Like the real database today: schema version 1, no UK tables, so every domain is blocked."""
    settings, tmp = env
    db = make_v1_db(tmp / "v1.sqlite3")
    before = sha(db)
    code, result = run_cli(tmp, fresh(AND_RC5, target("andertons.co.uk", uk_eligible=True)), review_db=db)
    a = by_domain(result)["andertons.co.uk"]
    assert code == 0 and not a["comparison"]["eligible"]
    assert a["competitor"]["uk_gate"]["evidence_status"] == "INSUFFICIENT_EVIDENCE"
    assert not any("Trusted review data unavailable" in w for w in result["warnings"])  # readable, just strict
    assert sha(db) == before
