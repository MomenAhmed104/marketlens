"""Review database versions after the UK gate was retired (Option B, 19 Nov 2026).

Rewritten from test_review_migration.py (the migration CLI is gone). It protects:
- a new database is created at version 1 with only the three core tables;
- existing version-1 and version-2 files open without any write and keep their version;
- a version-2 file's UK tables are never read, written or dropped, even when the core
  tables are written;
- unsupported versions are refused without touching the file;
- older records (legacy UK category fields, UK overrides) still load and are never rewritten.
Backup creation and verification (`sqlite_backup`) stay covered by test_classification_backfill.
Temporary databases only."""

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from app.schemas.review import ReviewDecision
from app.services import sqlite_backup
from app.services.review_store import CORE_TABLES, LEGACY_UK_TABLES, SUPPORTED_SCHEMA_VERSIONS, ReviewStore
from tests.test_review_store import candidate, decision, run
from tests.uk_fakes import make_v1_db, make_v2_db, table_snapshot


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def version(p):
    with closing(sqlite_backup.connect_read_only(p)) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def tables(p):
    with closing(sqlite_backup.connect_read_only(p)) as c:
        return set(sqlite_backup.user_tables(c))


def populate(path):
    store = ReviewStore(path)
    store.record_run(run([candidate("a.co.uk"), candidate("b.co.uk")]))
    store.create_decision(decision("a.co.uk", benchmark_eligible=True))
    store.update_decision(decision("a.co.uk", benchmark_eligible=False, notes="changed"))
    store.create_decision(decision("b.co.uk", decision=ReviewDecision.REJECTED))
    return store


def test_supported_versions_are_one_and_two():
    assert SUPPORTED_SCHEMA_VERSIONS == (1, 2)


def test_a_new_database_is_created_at_version_1_with_only_the_core_tables(tmp_path):
    store = ReviewStore(tmp_path / "new.sqlite3")
    assert store.schema_version == 1 and version(store.path) == 1
    assert set(CORE_TABLES) <= tables(store.path) and not set(LEGACY_UK_TABLES) & tables(store.path)
    ReviewStore(store.path)  # reopening is fine
    assert not hasattr(store, "uk_gate_state")  # the retired gate's API is gone


def test_opening_a_version_1_database_never_writes_it(tmp_path):
    path = make_v1_db(tmp_path / "v1.sqlite3")
    populate(path)
    before = sha(path)
    store = ReviewStore(path)
    assert store.schema_version == 1 and len(store.list_decisions()) == 2 and len(store.list_candidates()) == 2
    assert sha(path) == before and version(path) == 1


def test_opening_a_version_2_database_never_writes_it(tmp_path):
    path = make_v2_db(tmp_path / "v2.sqlite3")
    before = sha(path)
    store = ReviewStore(path)
    assert store.schema_version == 2 and store.list_decisions() == []
    assert sha(path) == before and version(path) == 2  # no downgrade, no migration


def test_core_writes_on_a_version_2_database_leave_its_uk_tables_untouched(tmp_path):
    path = make_v2_db(tmp_path / "v2.sqlite3")
    uk_before = table_snapshot(path)
    assert uk_before["uk_eligibility"] and uk_before["uk_gate_exceptions"]  # the fixture has UK rows
    store = populate(path)
    assert len(store.list_decisions()) == 2 and len(store.history("a.co.uk")) == 2
    assert version(path) == 2 and set(LEGACY_UK_TABLES) <= tables(path)  # nothing dropped
    assert table_snapshot(path) == uk_before  # never read into decisions, never written


@pytest.mark.parametrize("v", [3, 9])
def test_unsupported_versions_are_refused_and_untouched(tmp_path, v):
    p = tmp_path / f"v{v}.sqlite3"
    with closing(sqlite3.connect(p)) as c:
        c.execute(f"PRAGMA user_version = {v}")
        c.execute("CREATE TABLE t (x)")
        c.commit()
    before = sha(p)
    with pytest.raises(RuntimeError, match=f"unsupported schema version {v}"):
        ReviewStore(p)
    assert sha(p) == before


def test_legacy_records_still_load_and_are_never_rewritten(tmp_path):
    """A candidate stored with the retired four-category assessment and a decision stored with a
    UK relevance override (both written by the Phase A/B code) load; the legacy keys are ignored
    and the stored rows stay byte-for-byte as they were."""
    path = make_v2_db(tmp_path / "v2.sqlite3")
    store = ReviewStore(path)
    store.record_run(run([candidate("legacy.co.uk")]))
    store.create_decision(decision("legacy.co.uk", notes="checked the about page"))
    legacy_assessment = {"proposed": "UK_BASED", "relevance": "UK_BASED", "confidence": "HIGH",
                         "proposed_confidence": "HIGH", "evidence": [], "unverified_claims": [],
                         "reasons": ["UK_BASED on 2 supporting quote(s)"]}  # fmt: skip
    with closing(sqlite3.connect(path)) as c:
        rec = json.loads(c.execute("SELECT record FROM review_candidates").fetchone()[0])
        rec["candidate"]["uk_assessment"] = legacy_assessment
        c.execute("UPDATE review_candidates SET record = ?", (json.dumps(rec),))
        d = json.loads(c.execute("SELECT record FROM domain_decisions").fetchone()[0])
        d.update(uk_relevance_override="SERVES_UK_FROM_ABROAD", uk_override_note="ships from Germany")
        c.execute("UPDATE domain_decisions SET record = ?", (json.dumps(d),))
        c.execute("UPDATE decision_history SET record = ?", (json.dumps(d),))
        c.commit()
    rows_before = {t: r for t, r in table_snapshot(path, CORE_TABLES).items()}

    cand = store.get_candidate("legacy.co.uk").candidate
    assert cand.uk_assessment is not None and cand.uk_assessment.sufficient is False  # never trusted as verified
    assert not hasattr(cand.uk_assessment, "relevance")
    dec = store.get_decision("legacy.co.uk")
    assert dec.notes == "checked the about page" and not hasattr(dec, "uk_relevance_override")
    assert store.history("legacy.co.uk")[0].decision is ReviewDecision.APPROVED
    assert table_snapshot(path, CORE_TABLES) == rows_before  # reads never rewrite rows
