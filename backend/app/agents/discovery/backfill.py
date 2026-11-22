"""Competitor-classification backfill for the review database.

    python -m app.agents.discovery.backfill --db PATH [--domain amazon.co.uk ...] [--output report.json]
    python -m app.agents.discovery.backfill --db PATH --write      # explicit; updates the database

Re-classifies stored review candidates from their already-saved evidence:
  1. Each stored candidate is read through a read-only SQLite connection
     (`mode=ro`). `ReviewStore` is deliberately not used to open the file, because
     its constructor creates tables and sets the schema version, which would
     write to the database.
  2. LLM 1 is asked for a classification through the injectable `LLMClient`
     interface, with the same prompt, output schema and validation as live
     discovery (`prompts.CLASSIFY_SYSTEM`, `prompts.classify_user`,
     `ClassificationBatch`, `rules.apply_classification`). One domain per call,
     so one bad answer never affects another domain.
  3. The final class comes from the existing `rules.classify_competitor`
     (marketplace override, manufacturer and other safeguards).

Dry run (the default) writes nothing. `--write` must be given explicitly. It then:
  - opens the existing file read-write (`mode=rw`: a missing file is never created)
    and takes the write lock (`BEGIN IMMEDIATE`) so nothing else can write;
  - copies the database to a timestamped backup with the SQLite backup API and
    verifies the copy (integrity check, schema version, identical table contents)
    before any update;
  - updates, in that one transaction, only the five classification fields
    (`CLASS_FIELDS`) inside the candidate JSON, and only for CLASSIFIED domains;
  - skips a domain whose stored row changed since it was read (CONFLICT);
  - checks every new record differs from the old one in those five fields only,
    and rolls the whole transaction back if anything else would change;
  - after the commit, re-verifies the database against the backup.

Never changed: reviewer decisions, decision history, benchmark eligibility,
approval fields, the LLM proposal fields other than the class (company name,
type, relevance, ...), evidence, run IDs, counts and timestamps.

Skipped and failed domains get no proposal and no final class (null), and are
left exactly as stored. A failure is never turned into a Needs Review
classification, because that would overwrite a stored class with one nobody
proposed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import uuid
from collections.abc import Callable, Iterable
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.agents.discovery import prompts
from app.agents.discovery.rules import apply_classification, classify_competitor
from app.agents.discovery.schemas import (
    ClassificationBatch,
    ClassificationStatus,
    CompetitorClass,
    DiscoveredCompetitor,
    UkSeller,
)
from app.core.config import REPO_ROOT, Settings
from app.schemas.catalogue import StompboxProduct
from app.schemas.competitor import CompanyType
from app.schemas.review import ReviewStatus
from app.services.domains import registrable_domain
from app.services.llm import LLMClient, LLMNotConfiguredError, LLMUsage, build_llm1
from app.services import sqlite_backup
from app.services.review_store import CLASS_FIELDS, SUPPORTED_SCHEMA_VERSIONS, StoredCandidate
from app.services.sqlite_backup import BackupError, table_rows
from app.services.sqlite_backup import connect_read_only as _connect_read_only
from app.services.sqlite_backup import uri as _uri
from app.services.search.http import ProviderError

REPORT_DIR = REPO_ROOT / "data" / "backfill_reports"
# The only candidate fields write mode may change: review_store.CLASS_FIELDS.
TABLES = ("review_candidates", "domain_decisions", "decision_history")
_ROW_SQL = "SELECT domain, first_seen_at, last_seen_at, last_run_id, times_seen, record FROM review_candidates"

Row = tuple[str, str, str, str, int, str]  # one review_candidates row, exactly as stored


class BackfillStatus(StrEnum):
    CLASSIFIED = "CLASSIFIED"
    SKIPPED_NO_EVIDENCE = "SKIPPED_NO_EVIDENCE"  # the record has no evidence items
    SKIPPED_NO_USABLE_EVIDENCE = "SKIPPED_NO_USABLE_EVIDENCE"  # evidence has no title or snippet text
    SKIPPED_PRECONFIGURED = "SKIPPED_PRECONFIGURED"  # one of the configured competitors; never re-decided
    SKIPPED_EXCLUDED_DOMAIN = "SKIPPED_EXCLUDED_DOMAIN"  # removed by the user (e.g. PMT)
    NOT_FOUND = "NOT_FOUND"  # a requested domain that is not in the database
    FAILED_INVALID_RECORD = "FAILED_INVALID_RECORD"  # the stored record does not validate
    FAILED_LLM_ERROR = "FAILED_LLM_ERROR"  # call failed or output did not match the schema
    FAILED_LLM_MISSING = "FAILED_LLM_MISSING"  # the LLM answered without this domain
    FAILED_ERROR = "FAILED_ERROR"  # any other per-domain error

    @property
    def outcome(self) -> str:
        if self is BackfillStatus.CLASSIFIED:
            return "classified"
        if self is BackfillStatus.NOT_FOUND:
            return "not_found"
        return "skipped" if self.name.startswith("SKIPPED") else "failed"


class WriteStatus(StrEnum):
    WRITTEN = "WRITTEN"
    CONFLICT = "CONFLICT"  # the stored row changed after it was read; left as it is
    NOT_WRITTEN = "NOT_WRITTEN"  # skipped or failed: left exactly as stored


class ClassificationSnapshot(BaseModel):
    """The stored classification fields of a record, before the backfill."""

    classification_status: ClassificationStatus | None = None
    company_name: str | None = None
    company_type: CompanyType | None = None
    serves_uk_customers: UkSeller | None = None
    proposed_competitor_class: CompetitorClass | None = None
    competitor_class: CompetitorClass | None = None
    competitor_class_reasons: list[str] = Field(default_factory=list)
    classified_at: datetime | None = None
    classification_model: str | None = None

    @classmethod
    def of(cls, c: DiscoveredCompetitor) -> ClassificationSnapshot:
        return cls(**{f: getattr(c, f) for f in cls.model_fields})


class ClassFields(BaseModel):
    """The five fields write mode sets, serialised exactly as the store does."""

    proposed_competitor_class: CompetitorClass | None
    competitor_class: CompetitorClass
    competitor_class_reasons: list[str]
    classified_at: datetime
    classification_model: str | None


class BackfillEntry(BaseModel):
    domain: str
    company_name: str | None = None  # as stored (previous)
    review_status: ReviewStatus | None = None  # read-only context; never changed
    previous: ClassificationSnapshot | None = None
    # New LLM 1 proposal and the result of the existing rules. Null unless CLASSIFIED.
    # Only the class fields are ever written; the proposed name/type/UK answer are report-only.
    # The classification version is never written, so a backfill never makes a domain
    # automatically searchable (Stage 8.1: rediscovery is required).
    proposed_company_name: str | None = None
    proposed_company_type: CompanyType | None = None
    proposed_serves_uk_customers: UkSeller | None = None
    proposed_competitor_class: CompetitorClass | None = None
    final_competitor_class: CompetitorClass | None = None
    classification_reasons: list[str] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list)
    classification_model: str | None = None
    classified_at: datetime | None = None
    changed: bool | None = None  # final class differs from the stored final class; null unless CLASSIFIED
    evidence_count: int = 0
    usable_evidence_count: int = 0
    has_usable_evidence: bool = False
    status: BackfillStatus
    detail: str | None = None
    write_status: WriteStatus | None = None  # write mode only

    def class_fields(self) -> ClassFields:
        return ClassFields(proposed_competitor_class=self.proposed_competitor_class,
                           competitor_class=self.final_competitor_class,
                           competitor_class_reasons=self.classification_reasons,
                           classified_at=self.classified_at, classification_model=self.classification_model)  # fmt: skip


class WriteSummary(BaseModel):
    backup_path: str
    backup_verified: bool
    written: int
    conflicts: list[str]
    not_written: int  # skipped, failed, not found or conflict: unchanged in the database
    post_write_verified: bool
    post_write_problems: list[str] = Field(default_factory=list)


class BackfillSummary(BaseModel):
    records_in_database: int
    selected: int
    classified: int = 0
    skipped: int = 0
    failed: int = 0
    not_found: int = 0
    changed: int = 0
    llm_calls: int = 0
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_cost_usd: Decimal | None = None


class BackfillReport(BaseModel):
    report_id: str
    dry_run: bool = True
    database: str
    generated_at: datetime
    llm_model: str | None
    marketplace_domain_labels: list[str]
    requested_domains: list[str] | None  # None = all records
    summary: BackfillSummary
    entries: list[BackfillEntry]
    write: WriteSummary | None = None


class UnsafeWriteError(RuntimeError):
    """An update would change more than the classification fields; the transaction was rolled back."""


# --------------------------------------------------------------------------- database access


def _table_rows(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    return table_rows(conn, TABLES)


def read_database(db_path: Path) -> tuple[list[Row], dict[str, str]]:
    """(candidate rows exactly as stored, current decision per domain). Read only."""
    with closing(_connect_read_only(db_path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise RuntimeError(f"Review database has schema version {version}; expected one of "
                               f"{SUPPORTED_SCHEMA_VERSIONS}")
        rows = conn.execute(_ROW_SQL + " ORDER BY domain").fetchall()
        decisions = dict(conn.execute("SELECT domain, decision FROM domain_decisions").fetchall())
    return rows, decisions


def make_backup(db_path: Path, backup_dir: Path, now: datetime) -> Path:
    """Copy the database with the SQLite backup API (a consistent snapshot). Never overwrites."""
    return sqlite_backup.make_backup(db_path, backup_dir, now, "classification-backfill")


def verify_backup(backup: Path, expected: dict[str, list[tuple]]) -> None:
    """The backup is readable, intact, at a supported schema version and holds exactly the start rows."""
    sqlite_backup.verify_backup(backup, expected)
    with closing(_connect_read_only(backup)) as conn:
        if conn.execute("PRAGMA user_version").fetchone()[0] not in SUPPORTED_SCHEMA_VERSIONS:
            raise BackupError("Backup has the wrong schema version")


def updated_record(raw: str, entry: BackfillEntry) -> str:
    """The stored record JSON with only the five classification fields replaced."""
    data = json.loads(raw)
    data["candidate"].update(entry.class_fields().model_dump(mode="json"))
    new = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    StoredCandidate.model_validate_json(new)  # the store must still read it
    return new


def check_only_class_fields(old_raw: str, new_raw: str) -> None:
    """Raise UnsafeWriteError unless the records differ in CLASS_FIELDS only."""
    old, new = json.loads(old_raw), json.loads(new_raw)
    for d in (old, new):
        for f in CLASS_FIELDS:
            d.get("candidate", {}).pop(f, None)
    if old != new:
        keys = sorted({k for k in set(old) | set(new) if old.get(k) != new.get(k)}
                      | {f"candidate.{k}" for k in set(old.get("candidate", {})) | set(new.get("candidate", {}))
                         if old.get("candidate", {}).get(k) != new.get("candidate", {}).get(k)})  # fmt: skip
        raise UnsafeWriteError(f"Update would change non-classification fields: {keys}")


def verify_against_backup(db_path: Path, backup: Path, written: set[str]) -> list[str]:
    """Problems found comparing the database with its pre-write backup (empty = verified)."""
    problems: list[str] = []
    with closing(_connect_read_only(db_path)) as a, closing(_connect_read_only(backup)) as b:
        now, before = _table_rows(a), _table_rows(b)
    for t in ("domain_decisions", "decision_history"):
        if now[t] != before[t]:
            problems.append(f"{t} changed")
    old_rows = {r[0]: r for r in before["review_candidates"]}
    new_rows = {r[0]: r for r in now["review_candidates"]}
    if set(old_rows) != set(new_rows):
        problems.append("set of candidate domains changed")
    for d in sorted(set(old_rows) & set(new_rows)):
        o, n = old_rows[d], new_rows[d]
        if o[:5] != n[:5]:
            problems.append(f"{d}: row metadata (seen dates, run ID or count) changed")
        if o[5] == n[5]:
            if d in written:
                problems.append(f"{d}: reported written but unchanged")
            continue
        if d not in written:
            problems.append(f"{d}: changed but not reported written")
        try:
            check_only_class_fields(o[5], n[5])
        except UnsafeWriteError as e:
            problems.append(f"{d}: {e}")
    return problems


# --------------------------------------------------------------------------- per-domain work


def usable_evidence(c: DiscoveredCompetitor) -> int:
    """Evidence items with text LLM 1 can classify from (a title or snippet)."""
    return sum(bool((e.title or "").strip() or (e.snippet or "").strip()) for e in c.evidence)


def _products_for(c: DiscoveredCompetitor, products: dict[str, StompboxProduct]) -> list[StompboxProduct]:
    ids = set(c.products_matched) | {e.product_id for e in c.evidence if e.product_id}
    return [products[i] for i in sorted(ids) if i in products]


class Backfill:
    def __init__(
        self,
        llm: LLMClient,
        *,
        products: dict[str, StompboxProduct],
        allowed_categories: Iterable[str],
        marketplace_labels: tuple[str, ...],
        preconfigured_domains: Iterable[str] = (),
        excluded_domains: Iterable[str] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        concurrency: int = 1,
    ):
        self.llm = llm
        self.products = products
        self.allowed_categories = sorted(set(allowed_categories))
        self.marketplace_labels = tuple(marketplace_labels)
        self.preconfigured = {registrable_domain(d) for d in preconfigured_domains}
        self.excluded = {registrable_domain(d) for d in excluded_domains}
        self.clock = clock
        self.usage = LLMUsage()
        self.calls = 0
        self._sem = asyncio.Semaphore(max(1, concurrency))

    def _record_usage(self, usage: LLMUsage | None, calls: int) -> None:
        self.calls += calls
        if usage:
            self.usage.add(usage)

    async def classify_one(self, stored: StoredCandidate, review_status: ReviewStatus | None) -> BackfillEntry:
        original = stored.candidate
        n_usable = usable_evidence(original)
        entry = BackfillEntry(
            domain=stored.domain, company_name=original.company_name, review_status=review_status,
            previous=ClassificationSnapshot.of(original), evidence_count=len(original.evidence),
            usable_evidence_count=n_usable, has_usable_evidence=n_usable > 0,
            status=BackfillStatus.CLASSIFIED,
        )  # fmt: skip
        domain = registrable_domain(stored.domain)
        if domain in self.excluded:
            return entry.model_copy(update={"status": BackfillStatus.SKIPPED_EXCLUDED_DOMAIN,
                                            "detail": "Domain is excluded from competitor discovery"})  # fmt: skip
        if domain in self.preconfigured:
            return entry.model_copy(update={"status": BackfillStatus.SKIPPED_PRECONFIGURED,
                                            "detail": "Configured competitors are never re-classified"})  # fmt: skip
        if not original.evidence:
            return entry.model_copy(update={"status": BackfillStatus.SKIPPED_NO_EVIDENCE,
                                            "detail": "No saved evidence; nothing to classify from"})  # fmt: skip
        if not n_usable:
            return entry.model_copy(update={"status": BackfillStatus.SKIPPED_NO_USABLE_EVIDENCE,
                                            "detail": "Saved evidence has no title or snippet text"})  # fmt: skip

        work = original.model_copy(deep=True)  # never mutate what was read
        work.validation_notes = []
        try:
            async with self._sem:
                result = await self.llm.complete_structured(
                    system=prompts.CLASSIFY_SYSTEM,
                    user=prompts.classify_user([work], _products_for(work, self.products), self.allowed_categories),
                    output_model=ClassificationBatch, schema_name="company_classification",
                )  # fmt: skip
        except ProviderError as e:  # includes LLMOutputError (invalid or truncated output)
            self._record_usage(getattr(e, "usage", None), len(getattr(e, "request_ids", [])) or 1)
            return entry.model_copy(update={"status": BackfillStatus.FAILED_LLM_ERROR, "detail": str(e)})
        self._record_usage(result.usage, len(result.request_ids))

        matches = [c for c in result.value.companies if c.domain.strip().lower() == work.domain]
        others = len(result.value.companies) - len(matches)
        if not matches:
            detail = "LLM returned no classification for this domain"
            if others:
                detail += f" ({others} entry(ies) for other domains ignored)"
            return entry.model_copy(update={"status": BackfillStatus.FAILED_LLM_MISSING, "detail": detail})
        if len(matches) > 1:
            return entry.model_copy(update={"status": BackfillStatus.FAILED_LLM_ERROR,
                                            "detail": f"LLM returned {len(matches)} classifications for this domain"})  # fmt: skip

        apply_classification(work, matches[0], set(self.allowed_categories))
        work.classified_at = self.clock()
        work.classification_model = result.model
        classify_competitor(work, self.marketplace_labels)
        notes = list(work.validation_notes)
        if others:
            notes.append(f"Ignored {others} classification(s) for domains not given")
        return entry.model_copy(update={
            "proposed_company_name": work.company_name, "proposed_company_type": work.company_type,
            "proposed_serves_uk_customers": work.serves_uk_customers, "proposed_competitor_class": work.proposed_competitor_class,
            "final_competitor_class": work.competitor_class, "classification_reasons": work.competitor_class_reasons,
            "validation_notes": notes, "classification_model": result.model, "classified_at": work.classified_at,
            "changed": work.competitor_class != original.competitor_class,
        })  # fmt: skip

    async def _process(self, domain: str, raw: str, status: ReviewStatus) -> BackfillEntry:
        try:
            stored = StoredCandidate.model_validate_json(raw)
        except ValidationError as e:
            return BackfillEntry(domain=domain, review_status=status, status=BackfillStatus.FAILED_INVALID_RECORD,
                                 detail=f"Stored record is invalid ({e.error_count()} error(s))")  # fmt: skip
        try:
            return await self.classify_one(stored, status)
        except Exception as e:  # isolate any unexpected per-domain failure
            c = stored.candidate
            return BackfillEntry(
                domain=domain, company_name=c.company_name, review_status=status,
                previous=ClassificationSnapshot.of(c), evidence_count=len(c.evidence),
                usable_evidence_count=usable_evidence(c), has_usable_evidence=usable_evidence(c) > 0,
                status=BackfillStatus.FAILED_ERROR, detail=f"{type(e).__name__}: {e}",
            )  # fmt: skip

    def write(self, db_path: Path, snapshot: dict[str, Row], entries: list[BackfillEntry],
              backup_dir: Path) -> WriteSummary:  # fmt: skip
        """Apply CLASSIFIED entries in one transaction, after a verified backup. All or nothing."""
        path = Path(db_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Review database not found: {path}")
        written: set[str] = set()
        conflicts: list[str] = []
        # mode=rw: fails instead of creating a missing file.
        with closing(sqlite3.connect(_uri(path, "rw"), uri=True, timeout=10, isolation_level=None)) as conn:
            conn.execute("BEGIN IMMEDIATE")  # write lock: nothing else can write until COMMIT/ROLLBACK
            try:
                start = _table_rows(conn)
                backup = make_backup(path, backup_dir, self.clock())
                verify_backup(backup, start)
                for e in entries:
                    if e.status is not BackfillStatus.CLASSIFIED:
                        e.write_status = WriteStatus.NOT_WRITTEN
                        continue
                    old = snapshot[e.domain]
                    current = conn.execute(_ROW_SQL + " WHERE domain = ?", (e.domain,)).fetchone()
                    if current != old:
                        e.write_status = WriteStatus.CONFLICT
                        e.detail = "Stored record changed after it was read; not overwritten"
                        conflicts.append(e.domain)
                        continue
                    new = updated_record(old[5], e)
                    check_only_class_fields(old[5], new)
                    n = conn.execute("UPDATE review_candidates SET record = ? WHERE domain = ? AND record = ?",
                                     (new, e.domain, old[5])).rowcount  # fmt: skip
                    if n != 1:
                        raise UnsafeWriteError(f"{e.domain}: expected to update 1 row, updated {n}")
                    e.write_status = WriteStatus.WRITTEN
                    written.add(e.domain)
                after = _table_rows(conn)
                if any(after[t] != start[t] for t in ("domain_decisions", "decision_history")):
                    raise UnsafeWriteError("decisions or decision history changed during the write")
            except BaseException:
                conn.execute("ROLLBACK")
                for e in entries:
                    e.write_status = None
                raise
            conn.execute("COMMIT")
        problems = verify_against_backup(path, backup, written)
        return WriteSummary(backup_path=str(backup), backup_verified=True, written=len(written), conflicts=conflicts,
                            not_written=len(entries) - len(written), post_write_verified=not problems,
                            post_write_problems=problems)  # fmt: skip

    async def run(self, db_path: Path, domains: list[str] | None = None, *, dry_run: bool = True,
                  backup_dir: Path | None = None) -> BackfillReport:  # fmt: skip
        rows, decisions = read_database(db_path)
        requested = None if domains is None else list(dict.fromkeys(registrable_domain(d) for d in domains))
        by_domain = {r[0]: r for r in rows}
        selected = rows if requested is None else [by_domain[d] for d in requested if d in by_domain]

        entries: list[BackfillEntry] = list(await asyncio.gather(*(
            self._process(r[0], r[5], ReviewStatus(decisions[r[0]]) if r[0] in decisions else ReviewStatus.PENDING)
            for r in selected)))  # fmt: skip
        for d in requested or []:
            if d not in by_domain:
                entries.append(BackfillEntry(domain=d, status=BackfillStatus.NOT_FOUND,
                                             detail="Domain is not in the review database"))  # fmt: skip

        write = None
        if not dry_run:
            write = self.write(db_path, by_domain, entries,
                               backup_dir or Path(db_path).resolve().parent / "backups")  # fmt: skip

        summary = BackfillSummary(records_in_database=len(rows), selected=len(selected),
                                  llm_calls=self.calls, llm_prompt_tokens=self.usage.prompt_tokens,
                                  llm_completion_tokens=self.usage.completion_tokens,
                                  llm_cost_usd=self.usage.cost_usd)  # fmt: skip
        for e in entries:
            setattr(summary, e.status.outcome, getattr(summary, e.status.outcome) + 1)
            summary.changed += bool(e.changed)
        now = self.clock()
        return BackfillReport(
            report_id=f"backfill-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}", dry_run=dry_run,
            database=str(Path(db_path).resolve()), generated_at=now, llm_model=self.llm.model,
            marketplace_domain_labels=list(self.marketplace_labels), requested_domains=requested,
            summary=summary, entries=entries, write=write,
        )  # fmt: skip


# --------------------------------------------------------------------------- CLI


def summarise(report: BackfillReport) -> str:
    s, w = report.summary, report.write
    head = (f"DRY RUN {report.report_id}: nothing was written to {report.database}" if report.dry_run
            else f"WRITE {report.report_id}: {w.written} record(s) updated in {report.database}")  # fmt: skip
    lines = [
        head,
        f"LLM {report.llm_model}: {s.llm_calls} call(s), {s.llm_prompt_tokens}+{s.llm_completion_tokens} tokens, "
        f"cost ${s.llm_cost_usd}",
        f"Records in database: {s.records_in_database}  selected: {s.selected}  classified: {s.classified}  "
        f"skipped: {s.skipped}  failed: {s.failed}  not found: {s.not_found}  class changes: {s.changed}",
    ]
    if w:
        lines.append(f"Backup: {w.backup_path} (verified)  written: {w.written}  conflicts: {w.conflicts}  "
                     f"unchanged: {w.not_written}  post-write check: "
                     f"{'OK' if w.post_write_verified else 'PROBLEMS ' + str(w.post_write_problems)}")  # fmt: skip
    lines.append("")
    for e in report.entries:
        prev = e.previous.competitor_class.value if e.previous and e.previous.competitor_class else None
        ws = f" write={e.write_status.value}" if e.write_status else ""
        lines.append(f"  [{e.status.value:26}] {e.domain:30} review={e.review_status and e.review_status.value} "
                     f"evidence={e.evidence_count} (usable {e.usable_evidence_count}){ws}")  # fmt: skip
        if e.status is BackfillStatus.CLASSIFIED:
            proposed = e.proposed_competitor_class.value if e.proposed_competitor_class else None
            lines.append(f"      class: {prev} -> proposed {proposed} -> final "
                         f"{e.final_competitor_class.value}{'  (changed)' if e.changed else ''}")  # fmt: skip
            lines += [f"      - {r}" for r in e.classification_reasons]
        if e.detail:
            lines.append(f"      {e.detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None, llm_factory: Callable[[Settings], LLMClient] = build_llm1) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, help="review database (default: REVIEW_DB_PATH)")
    parser.add_argument("--domain", action="append", dest="domains", help="process only this domain (repeatable)")
    parser.add_argument("--output", type=Path, help="report JSON path (default: data/backfill_reports/<id>.json)")
    parser.add_argument("--write", action="store_true",
                        help="update the five classification fields (after a verified backup)")  # fmt: skip
    parser.add_argument("--backup-dir", type=Path, help="backup folder for --write (default: <db folder>/backups)")
    parser.add_argument("--concurrency", type=int, default=4, help="parallel LLM calls (default 4)")
    args = parser.parse_args(argv)

    settings = Settings()
    db = args.db or settings.review_db_path
    if not Path(db).is_file():
        print(f"Review database not found: {db}", file=sys.stderr)
        return 2
    if args.output and args.output.resolve() == Path(db).resolve():
        print("--output must not be the review database", file=sys.stderr)
        return 2
    from app.services.catalogue import load_catalogue  # deferred: only needed for a real run

    catalogue = load_catalogue(settings.catalogue_path)
    try:
        llm = llm_factory(settings)
    except LLMNotConfiguredError as e:
        print(f"LLM 1 not configured: {e}", file=sys.stderr)
        return 3
    backfill = Backfill(
        llm, products=catalogue.products, allowed_categories=[p.category for p in catalogue.products.values()],
        marketplace_labels=settings.marketplace_domain_labels,
        preconfigured_domains=[c.domain for c in catalogue.competitors],
        excluded_domains=settings.discovery_excluded_competitor_domains, concurrency=args.concurrency,
    )  # fmt: skip

    async def go() -> BackfillReport:
        try:
            return await backfill.run(db, args.domains, dry_run=not args.write, backup_dir=args.backup_dir)
        finally:
            await llm.aclose()

    try:
        report = asyncio.run(go())
    except (BackupError, UnsafeWriteError, sqlite3.Error) as e:
        print(f"Write aborted, transaction rolled back, nothing was written: {e}", file=sys.stderr)
        return 4
    out = args.output or REPORT_DIR / f"{report.report_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.model_dump(mode="json"), indent=1), encoding="utf-8")
    print(summarise(report))
    print(f"\nReport: {out}")
    return 0 if report.write is None or report.write.post_write_verified else 5


if __name__ == "__main__":
    raise SystemExit(main())
