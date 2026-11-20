"""Offline classification backfill (dry run): a copied temporary database and a fake LLM only.

No test here opens the real review database, and every network send is blocked.
"""

import asyncio
import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.agents.discovery import backfill as bf
from app.agents.discovery.backfill import Backfill, BackfillStatus as B
from app.agents.discovery.schemas import (
    ClassificationBatch,
    ClassificationStatus,
    CompetitorClass,
    DiscoveredCompetitor,
)
from app.schemas.competitor import CompanyType as T
from app.schemas.review import ReviewDecision, ReviewStatus
from app.services.llm.base import LLMClient, LLMOutputError, LLMResult, LLMUsage
from app.services.review_store import ReviewStore
from tests.test_discovery_rules import candidate, cls, ev
from tests.test_review_store import decision, run

D, R, N = CompetitorClass.DIRECT_COMPETITOR, CompetitorClass.RELATED_RETAILER, CompetitorClass.NEEDS_REVIEW
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
MARKETPLACES = ("amazon", "ebay", "reverb", "etsy", "gumtree")
OMIT = object()  # fake LLM answers, but without this domain


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any HTTP send (OpenRouter, Exa, ...) fails the test."""

    def blocked(*a, **kw):
        raise AssertionError("network access attempted during a backfill test")

    monkeypatch.setattr(httpx.AsyncClient, "send", blocked)
    monkeypatch.setattr(httpx.Client, "send", blocked)


class FakeLLM(LLMClient):
    """One scripted answer per domain: a CompanyClassification, an exception, or OMIT."""

    model = "fake/llm-1"

    def __init__(self, answers: dict):
        self.answers = answers
        self.calls: list[str] = []
        self.closed = False

    async def complete_structured(self, *, system, user, output_model, schema_name):
        assert output_model is ClassificationBatch and schema_name == "company_classification"
        data = json.loads(user.split("<untrusted_evidence>\n", 1)[1].rsplit("\n</untrusted_evidence>", 1)[0])
        (domain,) = [d["domain"] for d in data["domains"]]  # one domain per call
        self.calls.append(domain)
        answer = self.answers[domain]
        if isinstance(answer, BaseException):
            raise answer
        companies = [] if answer is OMIT else [answer]
        return LLMResult(value=ClassificationBatch(companies=companies), model="fake/llm-1@2026",
                         request_ids=[f"gen-{len(self.calls)}"], latency_ms=1,
                         usage=LLMUsage(100, 20, None))  # fmt: skip

    async def aclose(self):
        self.closed = True


def stored(domain, *, evidence=None, products=("SB-001",), **fields) -> DiscoveredCompetitor:
    c = candidate(domain, evidence=evidence, products=products) if evidence != [] else DiscoveredCompetitor(
        domain=domain, hostnames=[domain], homepage_url=f"https://{domain}/", evidence=[], products_matched=[],
        uk_signals=[], discovered_at=NOW)  # fmt: skip
    return c.model_copy(update={"classification_status": ClassificationStatus.SKIPPED_LLM_DISABLED,
                                "competitor_class": N,
                                "competitor_class_reasons": ["No completed LLM 1 classification"], **fields})


ANSWERS = {
    # A UK specialist retailer: proposal accepted.
    "beckettsmusic.co.uk": cls("beckettsmusic.co.uk", competitor_class=D),
    # Known marketplace: LLM 1 says Direct retailer; the override makes it Related.
    "amazon.co.uk": cls("amazon.co.uk", company_name=None, company_type=T.RETAILER, competitor_class=D,
                        evidence_urls=["https://amazon.co.uk/boss-rc-5"]),
    # Manufacturer proposed as Direct: downgraded to Needs Review.
    "boss.info": cls("boss.info", company_name=None, company_type=T.MANUFACTURER, competitor_class=D,
                     evidence_urls=["https://boss.info/boss-rc-5"]),
    "broken.co.uk": LLMOutputError("openrouter", "Model output did not match the schema",
                                   usage=LLMUsage(50, 8000, None), request_ids=["gen-x"]),
    "silent.co.uk": OMIT,
    "crash.co.uk": RuntimeError("simulated per-domain failure"),
}


@pytest.fixture
def source_db(tmp_path):
    """A synthetic review database built through the real store, as discovery would."""
    path = tmp_path / "source" / "review.sqlite3"
    store = ReviewStore(path)
    store.record_run(run([
        stored("beckettsmusic.co.uk"),
        stored("amazon.co.uk"),
        stored("boss.info"),
        stored("broken.co.uk"),
        stored("silent.co.uk"),
        stored("crash.co.uk"),
        stored("no-evidence.co.uk", evidence=[]),
        stored("blank.co.uk", evidence=[ev("https://blank.co.uk/p", title=None, snippet="  ")]),
        stored("andertons.co.uk"),  # configured competitor: never re-classified
    ], run_id="run-1"))  # fmt: skip
    store.record_run(run([stored("beckettsmusic.co.uk")], run_id="run-2"))  # times_seen 2, merged evidence
    store.create_decision(decision("beckettsmusic.co.uk", benchmark_eligible=True))
    store.update_decision(decision("beckettsmusic.co.uk", benchmark_eligible=True, notes="re-checked"))
    store.create_decision(decision("amazon.co.uk", company_type=T.MARKETPLACE))
    store.create_decision(decision("boss.info", decision=ReviewDecision.REJECTED, company_type=T.MANUFACTURER))
    with sqlite3.connect(path) as conn:  # a record that no longer validates
        conn.execute("INSERT INTO review_candidates VALUES ('corrupt.co.uk', ?, ?, 'run-1', 1, '{\"bad\": 1}')",
                     (NOW.isoformat(), NOW.isoformat()))  # fmt: skip
    conn.close()
    return path


@pytest.fixture
def db_copy(source_db, tmp_path):
    copy = tmp_path / "copy" / "review.sqlite3"
    copy.parent.mkdir()
    shutil.copy2(source_db, copy)
    return copy


def make_backfill(catalogue, llm) -> Backfill:
    return Backfill(llm, products=catalogue.products,
                    allowed_categories=[p.category for p in catalogue.products.values()],
                    marketplace_labels=MARKETPLACES, preconfigured_domains=[c.domain for c in catalogue.competitors],
                    excluded_domains=("pmtonline.co.uk",), clock=lambda: NOW)  # fmt: skip


def snapshot(path) -> dict:
    """Every table's rows, for comparing what the store holds."""
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as conn:
        out = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()
               for t in ("review_candidates", "domain_decisions", "decision_history")}  # fmt: skip
    conn.close()
    return out


def sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_all_records_are_processed_with_safe_statuses(catalogue, db_copy):
    llm = FakeLLM(ANSWERS)
    report = asyncio.run(make_backfill(catalogue, llm).run(db_copy))
    by = {e.domain: e for e in report.entries}

    assert report.dry_run is True and report.requested_domains is None
    assert {d: e.status for d, e in by.items()} == {
        "amazon.co.uk": B.CLASSIFIED, "andertons.co.uk": B.SKIPPED_PRECONFIGURED, "beckettsmusic.co.uk": B.CLASSIFIED,
        "blank.co.uk": B.SKIPPED_NO_USABLE_EVIDENCE, "boss.info": B.CLASSIFIED, "broken.co.uk": B.FAILED_LLM_ERROR,
        "corrupt.co.uk": B.FAILED_INVALID_RECORD, "crash.co.uk": B.FAILED_ERROR, "no-evidence.co.uk": B.SKIPPED_NO_EVIDENCE,
        "silent.co.uk": B.FAILED_LLM_MISSING,
    }  # fmt: skip
    s = report.summary
    assert (s.records_in_database, s.selected, s.classified, s.skipped, s.failed, s.not_found) == (10, 10, 3, 3, 4, 0)
    # Only domains with usable evidence reached the (fake) LLM; one call each.
    assert sorted(llm.calls) == ["amazon.co.uk", "beckettsmusic.co.uk", "boss.info", "broken.co.uk",
                                 "crash.co.uk", "silent.co.uk"]  # fmt: skip
    # The RuntimeError raised before any usage was reported, so 5 calls are accounted for.
    assert s.llm_calls == 5 and s.llm_prompt_tokens == 4 * 100 + 50 and s.changed == 2

    b = by["beckettsmusic.co.uk"]
    assert b.previous.competitor_class is N and b.previous.classification_status is ClassificationStatus.SKIPPED_LLM_DISABLED
    assert (b.proposed_competitor_class, b.final_competitor_class, b.changed) == (D, D, True)
    assert b.classification_reasons[-1] == "Accepted: DIRECT_COMPETITOR" and b.classification_model == "fake/llm-1@2026"
    # 4 = two product pages + the about/delivery profile pages (shared fixture with verified UK evidence)
    assert b.review_status is ReviewStatus.APPROVED and b.has_usable_evidence and b.evidence_count == 4

    for domain in ("broken.co.uk", "silent.co.uk", "crash.co.uk", "corrupt.co.uk", "no-evidence.co.uk", "blank.co.uk",
                   "andertons.co.uk"):  # fmt: skip
        e = by[domain]  # nothing invented for skipped/failed domains
        assert e.proposed_competitor_class is None and e.final_competitor_class is None and e.changed is None
        assert e.classification_reasons == [] and e.detail
    assert "did not match the schema" in by["broken.co.uk"].detail
    assert by["crash.co.uk"].detail == "RuntimeError: simulated per-domain failure"
    assert by["no-evidence.co.uk"].evidence_count == 0 and not by["no-evidence.co.uk"].has_usable_evidence
    assert by["blank.co.uk"].evidence_count == 1 and by["blank.co.uk"].usable_evidence_count == 0
    assert by["corrupt.co.uk"].previous is None


def test_marketplace_override_and_manufacturer_safeguard_come_from_classify_competitor(catalogue, db_copy):
    report = asyncio.run(make_backfill(catalogue, FakeLLM(ANSWERS)).run(db_copy, ["amazon.co.uk", "boss.info"]))
    amazon, boss = report.entries
    assert (amazon.proposed_competitor_class, amazon.final_competitor_class) == (D, R)
    assert amazon.classification_reasons == [
        "Known marketplace domain (amazon.co.uk): always RELATED_RETAILER, overriding the LLM 1 proposal "
        "(DIRECT_COMPETITOR)"]  # fmt: skip
    assert (boss.proposed_competitor_class, boss.final_competitor_class) == (D, N)
    assert boss.classification_reasons[-1] == (
        "Downgraded DIRECT_COMPETITOR -> NEEDS_REVIEW: type manufacturer cannot be a Direct Competitor or Related Retailer")
    assert boss.review_status is ReviewStatus.REJECTED  # reported, not changed


def test_selected_subset_processes_only_those_records(catalogue, db_copy):
    llm = FakeLLM(ANSWERS)
    report = asyncio.run(make_backfill(catalogue, llm).run(
        db_copy, ["WWW.Amazon.co.uk", "no-evidence.co.uk", "missing.co.uk", "amazon.co.uk"]))
    assert [(e.domain, e.status) for e in report.entries] == [
        ("amazon.co.uk", B.CLASSIFIED), ("no-evidence.co.uk", B.SKIPPED_NO_EVIDENCE), ("missing.co.uk", B.NOT_FOUND)]
    assert llm.calls == ["amazon.co.uk"]
    s = report.summary
    assert (s.records_in_database, s.selected, s.classified, s.skipped, s.failed, s.not_found) == (10, 2, 1, 1, 0, 1)
    assert report.requested_domains == ["amazon.co.uk", "no-evidence.co.uk", "missing.co.uk"]


def test_dry_run_leaves_the_copy_byte_for_byte_unchanged(catalogue, source_db, db_copy):
    before_bytes, before_rows = sha(db_copy), snapshot(db_copy)
    files_before = sorted(p.name for p in db_copy.parent.iterdir())
    assert before_bytes == sha(source_db)

    asyncio.run(make_backfill(catalogue, FakeLLM(ANSWERS)).run(db_copy))

    assert sha(db_copy) == before_bytes  # byte for byte
    assert sorted(p.name for p in db_copy.parent.iterdir()) == files_before  # no journal or side files
    after = snapshot(db_copy)
    assert after == before_rows
    # Decisions and history: approval, rejection, benchmark eligibility and versions intact.
    store_view = {r[0]: (r[1], r[2]) for r in after["domain_decisions"]}
    assert store_view == {"amazon.co.uk": ("APPROVED", 1), "beckettsmusic.co.uk": ("APPROVED", 2),
                          "boss.info": ("REJECTED", 1)}  # fmt: skip
    assert [(r[1], r[2]) for r in after["decision_history"]] == [
        ("beckettsmusic.co.uk", 1), ("beckettsmusic.co.uk", 2), ("amazon.co.uk", 1), ("boss.info", 1)]
    # Evidence, run IDs and counts in every stored record are unchanged.
    rows = {r[0]: r for r in after["review_candidates"]}
    beck = json.loads(rows["beckettsmusic.co.uk"][5])
    assert rows["beckettsmusic.co.uk"][3:5] == ("run-2", 2)
    assert beck["candidate"]["competitor_class"] == "NEEDS_REVIEW" and len(beck["candidate"]["evidence"]) == 4
    assert beck["candidate"]["review_decision"] is None


def test_failures_do_not_stop_other_domains(catalogue, db_copy):
    answers = dict(ANSWERS, **{"beckettsmusic.co.uk": RuntimeError("boom"), "amazon.co.uk": OMIT})
    report = asyncio.run(make_backfill(catalogue, FakeLLM(answers)).run(db_copy))
    by = {e.domain: e for e in report.entries}
    assert by["beckettsmusic.co.uk"].status is B.FAILED_ERROR
    assert by["amazon.co.uk"].status is B.FAILED_LLM_MISSING
    assert by["boss.info"].status is B.CLASSIFIED  # later domains still processed


def test_llm_answer_for_a_different_domain_is_not_applied(catalogue, db_copy):
    answers = dict(ANSWERS, **{"silent.co.uk": cls("someone-else.co.uk", competitor_class=D)})
    report = asyncio.run(make_backfill(catalogue, FakeLLM(answers)).run(db_copy, ["silent.co.uk"]))
    (e,) = report.entries
    assert e.status is B.FAILED_LLM_MISSING and e.final_competitor_class is None
    assert "1 entry(ies) for other domains ignored" in e.detail


def test_missing_database_is_never_created(catalogue, tmp_path):
    path = tmp_path / "absent.sqlite3"
    with pytest.raises(FileNotFoundError):
        asyncio.run(make_backfill(catalogue, FakeLLM({})).run(path))
    assert not path.exists()


def test_cli_dry_run_writes_only_the_report(db_copy, tmp_path, capsys):
    before = sha(db_copy)
    llm = FakeLLM(ANSWERS)
    out = tmp_path / "report.json"
    code = bf.main(["--db", str(db_copy), "--domain", "amazon.co.uk", "--domain", "blank.co.uk", "--output", str(out)],
                   llm_factory=lambda settings: llm)  # fmt: skip
    assert code == 0 and sha(db_copy) == before and llm.closed
    report = bf.BackfillReport.model_validate_json(out.read_text(encoding="utf-8"))
    assert [e.status for e in report.entries] == [B.CLASSIFIED, B.SKIPPED_NO_USABLE_EVIDENCE]
    printed = capsys.readouterr().out
    assert printed.startswith("DRY RUN ") and "nothing was written" in printed
    assert "class: NEEDS_REVIEW -> proposed DIRECT_COMPETITOR -> final RELATED_RETAILER  (changed)" in printed
    # The output path may never be the database itself.
    assert bf.main(["--db", str(db_copy), "--output", str(db_copy)], llm_factory=lambda s: llm) == 2
    assert sha(db_copy) == before


# --------------------------------------------------------------------------- write mode


def write(catalogue, db, llm=None, backup_dir=None, domains=None):
    return asyncio.run(make_backfill(catalogue, llm or FakeLLM(ANSWERS)).run(
        db, domains, dry_run=False, backup_dir=backup_dir or db.parent / "backups"))


def records(db) -> dict[str, dict]:
    return {r[0]: json.loads(r[5]) for r in snapshot(db)["review_candidates"]}


def without_class_fields(record: dict) -> dict:
    out = json.loads(json.dumps(record))
    for f in bf.CLASS_FIELDS:
        out["candidate"].pop(f, None)
    return out


WRITTEN = {"amazon.co.uk", "beckettsmusic.co.uk", "boss.info"}


def test_write_updates_only_the_five_class_fields_of_classified_records(catalogue, db_copy):
    before, old = snapshot(db_copy), records(db_copy)
    report = write(catalogue, db_copy)
    after, new = snapshot(db_copy), records(db_copy)

    w = report.write
    assert report.dry_run is False and w.backup_verified and w.post_write_verified and w.post_write_problems == []
    assert w.written == 3 and w.conflicts == [] and w.not_written == 7
    by = {e.domain: e for e in report.entries}
    assert {d for d, e in by.items() if e.write_status is bf.WriteStatus.WRITTEN} == WRITTEN
    # Decisions, history and every row column except the record JSON are untouched.
    assert after["domain_decisions"] == before["domain_decisions"]
    assert after["decision_history"] == before["decision_history"]
    assert [r[:5] for r in after["review_candidates"]] == [r[:5] for r in before["review_candidates"]]
    # Skipped and failed records are byte-for-byte as stored.
    raw_before = {r[0]: r[5] for r in before["review_candidates"]}
    raw_after = {r[0]: r[5] for r in after["review_candidates"]}
    for d in set(raw_before) - WRITTEN:
        assert raw_after[d] == raw_before[d], d
        assert by[d].write_status is bf.WriteStatus.NOT_WRITTEN
    # Written records differ only in the five fields; evidence, approval and LLM proposal fields stay.
    for d in WRITTEN:
        assert without_class_fields(new[d]) == without_class_fields(old[d])
        assert new[d]["candidate"]["evidence"] == old[d]["candidate"]["evidence"]
    beck, beck_old = new["beckettsmusic.co.uk"]["candidate"], old["beckettsmusic.co.uk"]["candidate"]
    assert (beck["proposed_competitor_class"], beck["competitor_class"]) == ("DIRECT_COMPETITOR", "DIRECT_COMPETITOR")
    assert beck["competitor_class_reasons"][-1] == "Accepted: DIRECT_COMPETITOR"
    assert beck["classification_model"] == "fake/llm-1@2026" and beck["classified_at"].startswith("2026-11-15T12:00")
    assert beck["classification_status"] == "SKIPPED_LLM_DISABLED" and beck["company_type"] is None  # not written
    assert beck["approval_status"] == beck_old["approval_status"]
    assert beck["price_benchmark"] == beck_old["price_benchmark"]


def test_written_records_keep_marketplace_and_manufacturer_safeguards_and_still_load(catalogue, db_copy):
    write(catalogue, db_copy)
    store = ReviewStore(db_copy)  # the store reads the updated rows (this copy only)
    amazon, boss = store.get_candidate("amazon.co.uk").candidate, store.get_candidate("boss.info").candidate
    assert (amazon.proposed_competitor_class, amazon.competitor_class) == (D, R)
    assert amazon.competitor_class_reasons[0].startswith("Known marketplace domain (amazon.co.uk)")
    assert (boss.proposed_competitor_class, boss.competitor_class) == (D, N)
    assert "type manufacturer cannot be" in boss.competitor_class_reasons[-1]
    # Decisions and benchmark eligibility unchanged.
    assert store.get_decision("beckettsmusic.co.uk").benchmark_eligible is True
    assert store.get_decision("beckettsmusic.co.uk").version == 2
    assert store.get_decision("boss.info").decision is ReviewDecision.REJECTED
    assert [h.version for h in store.history("beckettsmusic.co.uk")] == [1, 2]


def test_backup_is_made_verified_and_matches_the_pre_write_database(catalogue, db_copy, tmp_path):
    before = snapshot(db_copy)
    report = write(catalogue, db_copy, backup_dir=tmp_path / "bk")
    backup = Path(report.write.backup_path)
    assert backup.parent == tmp_path / "bk" and "pre-classification-backfill-20261115T120000Z" in backup.name
    assert snapshot(backup) == before  # readable and identical to the database before the write
    with sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_failed_backup_verification_writes_nothing(catalogue, db_copy, monkeypatch):
    before = sha(db_copy)

    def bad(backup, expected):
        raise bf.BackupError("simulated unreadable backup")

    monkeypatch.setattr(bf, "verify_backup", bad)
    with pytest.raises(bf.BackupError):
        write(catalogue, db_copy)
    assert sha(db_copy) == before


def test_verify_backup_rejects_missing_or_different_copies(db_copy, tmp_path):
    with pytest.raises(bf.BackupError):
        bf.verify_backup(tmp_path / "nope.sqlite3", snapshot(db_copy))
    other = tmp_path / "other.sqlite3"
    shutil.copy2(db_copy, other)
    with sqlite3.connect(other) as conn:
        conn.execute("UPDATE review_candidates SET times_seen = times_seen + 1 WHERE domain = 'boss.info'")
    conn.close()
    with pytest.raises(bf.BackupError, match="contents differ"):
        bf.verify_backup(other, snapshot(db_copy))


def test_an_existing_backup_is_never_overwritten(catalogue, db_copy):
    write(catalogue, db_copy)  # same fixed clock -> same backup name on the second run
    after_first = sha(db_copy)
    with pytest.raises(bf.BackupError, match="already exists"):
        write(catalogue, db_copy)
    assert sha(db_copy) == after_first


def test_any_failure_during_the_write_rolls_everything_back(catalogue, db_copy, monkeypatch):
    before_sha, before = sha(db_copy), snapshot(db_copy)
    real, calls = bf.check_only_class_fields, []

    def fail_second(old, new):
        calls.append(1)
        if len(calls) == 2:
            raise bf.UnsafeWriteError("simulated failure after one update")
        real(old, new)

    monkeypatch.setattr(bf, "check_only_class_fields", fail_second)
    with pytest.raises(bf.UnsafeWriteError):
        write(catalogue, db_copy)
    assert len(calls) == 2  # the first record had already been updated inside the transaction
    assert snapshot(db_copy) == before and sha(db_copy) == before_sha


def test_an_update_touching_any_other_field_is_refused(catalogue, db_copy, monkeypatch):
    before = snapshot(db_copy)
    real = bf.updated_record

    def sneaky(raw, entry):
        data = json.loads(real(raw, entry))
        data["candidate"]["company_type"] = "retailer"
        return json.dumps(data)

    monkeypatch.setattr(bf, "updated_record", sneaky)
    with pytest.raises(bf.UnsafeWriteError, match="candidate.company_type"):
        write(catalogue, db_copy)
    assert snapshot(db_copy) == before


def test_check_only_class_fields():
    old = json.dumps({"domain": "a", "times_seen": 1, "candidate": {"competitor_class": None, "evidence": [1]}})
    ok = json.dumps({"domain": "a", "times_seen": 1,
                     "candidate": {"competitor_class": "NEEDS_REVIEW", "classified_at": "x", "evidence": [1]}})
    bf.check_only_class_fields(old, ok)
    for bad in ({"domain": "a", "times_seen": 2, "candidate": {"evidence": [1]}},
                {"domain": "a", "times_seen": 1, "candidate": {"evidence": []}}):  # fmt: skip
        with pytest.raises(bf.UnsafeWriteError):
            bf.check_only_class_fields(old, json.dumps(bad))


class ConcurrentChangeLLM(FakeLLM):
    """While classifying amazon.co.uk, something else updates its row (e.g. a discovery run)."""

    def __init__(self, answers, db):
        super().__init__(answers)
        self.db = db

    async def complete_structured(self, **kw):
        result = await super().complete_structured(**kw)
        if self.calls[-1] == "amazon.co.uk":
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE review_candidates SET times_seen = times_seen + 1 WHERE domain = 'amazon.co.uk'")
            conn.close()
        return result


def test_a_record_changed_since_it_was_read_is_a_conflict_and_not_overwritten(catalogue, db_copy):
    report = write(catalogue, db_copy, llm=ConcurrentChangeLLM(ANSWERS, db_copy))
    w = report.write
    assert w.conflicts == ["amazon.co.uk"] and w.written == 2
    by = {e.domain: e for e in report.entries}
    assert by["amazon.co.uk"].write_status is bf.WriteStatus.CONFLICT
    amazon = records(db_copy)["amazon.co.uk"]
    assert amazon["candidate"]["competitor_class"] == "NEEDS_REVIEW"  # the stored class, not overwritten
    assert by["boss.info"].write_status is bf.WriteStatus.WRITTEN
    # The backup was taken after the outside change, so the post-write check is clean.
    assert w.post_write_verified and w.post_write_problems == []


def test_write_never_creates_a_missing_database(catalogue, tmp_path):
    path = tmp_path / "absent.sqlite3"
    with pytest.raises(FileNotFoundError):
        write(catalogue, path)
    assert not path.exists()
    assert bf.main(["--db", str(path), "--write"], llm_factory=lambda s: pytest.fail("no LLM")) == 2
    assert not path.exists()


def test_cli_write_mode(db_copy, tmp_path, capsys):
    old = records(db_copy)
    out = tmp_path / "report.json"
    code = bf.main(["--db", str(db_copy), "--write", "--backup-dir", str(tmp_path / "bk"), "--output", str(out)],
                   llm_factory=lambda s: FakeLLM(ANSWERS))  # fmt: skip
    assert code == 0
    report = bf.BackfillReport.model_validate_json(out.read_text(encoding="utf-8"))
    assert report.write.written == 3 and report.write.post_write_verified
    printed = capsys.readouterr().out
    assert printed.startswith("WRITE ") and "post-write check: OK" in printed
    new = records(db_copy)
    assert {d for d in new if new[d] != old[d]} == WRITTEN
    assert len(list((tmp_path / "bk").iterdir())) == 1


def test_cli_write_abort_returns_an_error_and_writes_nothing(db_copy, monkeypatch, capsys):
    before = sha(db_copy)

    def bad(backup, expected):
        raise bf.BackupError("simulated")

    monkeypatch.setattr(bf, "verify_backup", bad)
    assert bf.main(["--db", str(db_copy), "--write"], llm_factory=lambda s: FakeLLM(ANSWERS)) == 4
    assert "nothing was written" in capsys.readouterr().err and sha(db_copy) == before
