"""Review database schema v2: store behaviour, explicit 1 -> 2 migration with verified backup,
versioned UK history, and preservation of existing data. Temporary databases only."""

import hashlib
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

from app.schemas.review import ReviewDecision
from app.schemas.uk_eligibility import UkConfidence, UkEligibilityProposal, UkProposalSource
from app.services import review_migration as rm
from app.services import sqlite_backup
from app.services.review_store import (
    UK_TABLES,
    V1_TABLES,
    MigrationRequiredError,
    ReviewStore,
    VersionConflictError,
)
from app.services.sqlite_backup import BackupError
from app.services.uk_gate import gate_decision
from tests.test_review_store import candidate, decision, run
from tests.uk_fakes import S, make_v1_db, uk_exception, uk_record

CLOCK = lambda: datetime(2026, 11, 17, 13, 0, tzinfo=UTC)  # noqa: E731


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rows(p, tables=V1_TABLES):
    with closing(sqlite_backup.connect_read_only(p)) as c:
        return sqlite_backup.table_rows(c, tables)


def version(p):
    with closing(sqlite_backup.connect_read_only(p)) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def tables(p):
    with closing(sqlite_backup.connect_read_only(p)) as c:
        return set(sqlite_backup.user_tables(c))


@pytest.fixture
def v1_db(tmp_path):
    """A populated version-1 database: candidates (with classes), decisions and versioned history."""
    src = make_v1_db(tmp_path / "source.sqlite3")
    store = ReviewStore(src)
    assert store.schema_version == 1
    store.record_run(run([candidate("a.co.uk"), candidate("b.co.uk"), candidate("c.com")]))
    store.create_decision(decision("a.co.uk", benchmark_eligible=True))
    store.update_decision(decision("a.co.uk", benchmark_eligible=False, notes="changed"))
    store.create_decision(decision("b.co.uk", decision=ReviewDecision.REJECTED))
    copy = tmp_path / "copy.sqlite3"
    shutil.copy(src, copy)
    return copy


# --------------------------------------------------------------------------- store


def test_a_new_database_is_created_at_version_2(tmp_path):
    store = ReviewStore(tmp_path / "new.sqlite3")
    assert store.schema_version == 2 and store.uk_supported
    assert set(UK_TABLES) | set(V1_TABLES) <= tables(store.path)
    ReviewStore(store.path)  # reopening is fine


def test_opening_a_version_1_database_never_writes_it(v1_db):
    before = sha(v1_db)
    store = ReviewStore(v1_db)
    assert store.schema_version == 1 and not store.uk_supported
    assert store.uk_gate_state().eligibility == {} and store.list_uk_proposals() == []
    assert store.get_uk_eligibility("a.co.uk") is None and store.uk_eligibility_history("a.co.uk") == []
    with pytest.raises(MigrationRequiredError, match="review_migration"):
        store.put_uk_eligibility(uk_record("a.co.uk"))
    with pytest.raises(MigrationRequiredError):
        store.put_uk_exception(uk_exception("a.co.uk"))
    assert len(store.list_decisions()) == 2  # version-1 features keep working
    assert sha(v1_db) == before and version(v1_db) == 1 and not set(UK_TABLES) & tables(v1_db)


def test_unsupported_versions_are_refused(tmp_path):
    p = tmp_path / "v9.sqlite3"
    with closing(sqlite3.connect(p)) as c:
        c.execute("PRAGMA user_version = 9")
    with pytest.raises(RuntimeError, match="unsupported schema version 9"):
        ReviewStore(p)


def test_uk_records_and_exceptions_are_versioned_with_append_only_history(tmp_path):
    store = ReviewStore(tmp_path / "r.sqlite3")
    r1 = store.put_uk_eligibility(uk_record("x.co.uk", S.INSUFFICIENT_EVIDENCE))
    r2 = store.put_uk_eligibility(uk_record("x.co.uk", S.UK_ELIGIBLE), expected_version=1)
    assert (r1.version, r2.version) == (1, 2)
    assert [h.status for h in store.uk_eligibility_history("x.co.uk")] == [S.INSUFFICIENT_EVIDENCE, S.UK_ELIGIBLE]
    assert store.get_uk_eligibility("x.co.uk") == r2
    with pytest.raises(VersionConflictError) as e:
        store.put_uk_eligibility(uk_record("x.co.uk", S.NOT_UK_ELIGIBLE), expected_version=1)
    assert e.value.current == 2 and store.get_uk_eligibility("x.co.uk").status is S.UK_ELIGIBLE
    with pytest.raises(VersionConflictError):
        store.put_uk_eligibility(uk_record("new.co.uk"), expected_version=3)
    store.put_uk_eligibility(uk_record("new.co.uk"), expected_version=0)

    store.put_uk_exception(uk_exception("x.co.uk", search=True, benchmark=True))
    store.put_uk_exception(uk_exception("x.co.uk", search=False, benchmark=False, reason="revoked"))
    hist = store.uk_exception_history("x.co.uk")
    assert [(h.version, h.allow_search, h.reason) for h in hist] == [(1, True, "explicit test approval"),
                                                                      (2, False, "revoked")]  # fmt: skip
    state = store.uk_gate_state()
    assert set(state.eligibility) == {"x.co.uk", "new.co.uk"} and state.exceptions["x.co.uk"].version == 2


def test_proposals_never_change_the_gate(tmp_path):
    store = ReviewStore(tmp_path / "r.sqlite3")
    store.put_uk_proposals([UkEligibilityProposal(domain="p.co.uk", status=S.UK_ELIGIBLE, confidence=UkConfidence.HIGH,
                                                  source=UkProposalSource.AUDIT, proposed_at=CLOCK())])  # fmt: skip
    assert store.get_uk_proposal("p.co.uk").status is S.UK_ELIGIBLE
    assert store.uk_gate_state().eligibility == {}
    assert not gate_decision("p.co.uk", store.uk_gate_state()).search_allowed


def test_uk_writes_leave_candidates_decisions_and_history_untouched(tmp_path):
    store = ReviewStore(tmp_path / "r.sqlite3")
    store.record_run(run([candidate("a.co.uk")]))
    store.create_decision(decision("a.co.uk", benchmark_eligible=True))
    before = rows(store.path)
    store.put_uk_eligibility(uk_record("a.co.uk"))
    store.put_uk_exception(uk_exception("a.co.uk"))
    assert rows(store.path) == before


# --------------------------------------------------------------------------- migration


def test_dry_run_reports_and_changes_nothing(v1_db, tmp_path):
    before = sha(v1_db)
    report = rm.migrate(v1_db, backup_dir=tmp_path / "bk", clock=CLOCK)
    assert report.status is rm.MigrationStatus.WOULD_MIGRATE and report.from_version == 1
    assert report.tables_to_create == list(UK_TABLES) and report.backup_path is None
    assert report.row_counts["review_candidates"] == 3 and report.row_counts["decision_history"] == 3
    assert sha(v1_db) == before and not (tmp_path / "bk").exists()


def test_apply_backs_up_verifies_and_migrates_without_touching_existing_rows(v1_db, tmp_path):
    before_rows, before_sha = rows(v1_db), sha(v1_db)
    old = ReviewStore(v1_db)
    decisions, history, cands = old.list_decisions(), old.history("a.co.uk"), old.list_candidates()

    report = rm.migrate(v1_db, apply=True, backup_dir=tmp_path / "bk", clock=CLOCK)

    assert report.status is rm.MigrationStatus.MIGRATED and report.problems == []
    assert report.db_sha256_before == before_sha
    backup = tmp_path / "bk" / "copy.pre-schema-v2-migration-20261117T130000Z.sqlite3"
    assert report.backup_path == str(backup) and sha(backup) != "" and version(backup) == 1
    assert rows(backup) == before_rows  # the backup is the pre-migration database
    assert version(v1_db) == 2 and set(UK_TABLES) <= tables(v1_db)
    assert rows(v1_db) == before_rows  # byte-identical existing rows
    assert all(not r for r in rows(v1_db, UK_TABLES).values())  # no UK data written
    new = ReviewStore(v1_db)
    assert new.uk_supported and new.list_decisions() == decisions and new.history("a.co.uk") == history
    assert new.list_candidates() == cands
    assert new.uk_gate_state().eligibility == {}  # still all blocked until assessed


def test_a_second_run_is_a_no_op(v1_db, tmp_path):
    rm.migrate(v1_db, apply=True, backup_dir=tmp_path / "bk", clock=CLOCK)
    after = sha(v1_db)
    again = rm.migrate(v1_db, apply=True, backup_dir=tmp_path / "bk2", clock=CLOCK)
    assert again.status is rm.MigrationStatus.ALREADY_CURRENT and again.backup_path is None
    assert sha(v1_db) == after and not (tmp_path / "bk2").exists()


def test_failed_backup_verification_writes_nothing(v1_db, tmp_path, monkeypatch):
    before = sha(v1_db)

    def bad(*a, **k):
        raise BackupError("simulated unreadable backup")

    monkeypatch.setattr(rm.sqlite_backup, "verify_backup", bad)
    with pytest.raises(BackupError):
        rm.migrate(v1_db, apply=True, backup_dir=tmp_path / "bk", clock=CLOCK)
    assert sha(v1_db) == before and version(v1_db) == 1


def test_a_failure_after_creating_tables_rolls_everything_back(v1_db, tmp_path, monkeypatch):
    before = sha(v1_db)
    monkeypatch.setattr(rm, "_SCHEMA_V2", rm._SCHEMA_V2 + "; CREATE TABLE broken (")
    with pytest.raises(sqlite3.Error):
        rm.migrate(v1_db, apply=True, backup_dir=tmp_path / "bk", clock=CLOCK)
    assert sha(v1_db) == before and version(v1_db) == 1 and not set(UK_TABLES) & tables(v1_db)


def test_an_existing_backup_file_is_never_overwritten(v1_db, tmp_path):
    bk = tmp_path / "bk"
    bk.mkdir()
    (bk / "copy.pre-schema-v2-migration-20261117T130000Z.sqlite3").write_bytes(b"keep")
    before = sha(v1_db)
    with pytest.raises(BackupError, match="already exists"):
        rm.migrate(v1_db, apply=True, backup_dir=bk, clock=CLOCK)
    assert sha(v1_db) == before and (bk / "copy.pre-schema-v2-migration-20261117T130000Z.sqlite3").read_bytes() == b"keep"


def test_unexpected_states_are_refused(tmp_path):
    p = tmp_path / "v0.sqlite3"
    with closing(sqlite3.connect(p)) as c:
        c.execute("CREATE TABLE t (x)")
    with pytest.raises(rm.MigrationError, match="Expected schema version 1"):
        rm.migrate(p, apply=True, backup_dir=tmp_path / "bk")
    half = make_v1_db(tmp_path / "half.sqlite3")
    with closing(sqlite3.connect(half)) as c:
        c.execute("CREATE TABLE uk_eligibility (domain TEXT)")
        c.commit()
    with pytest.raises(rm.MigrationError, match="already exist"):
        rm.migrate(half, apply=True, backup_dir=tmp_path / "bk")


def test_cli_dry_run_apply_and_missing_db(v1_db, tmp_path, capsys):
    before = sha(v1_db)
    assert rm.main(["--db", str(v1_db), "--backup-dir", str(tmp_path / "bk")]) == 0
    assert '"WOULD_MIGRATE"' in capsys.readouterr().out and sha(v1_db) == before
    assert rm.main(["--db", str(v1_db), "--apply", "--backup-dir", str(tmp_path / "bk")]) == 0
    assert '"MIGRATED"' in capsys.readouterr().out and version(v1_db) == 2
    assert rm.main(["--db", str(tmp_path / "missing.sqlite3")]) == 2
