"""Stage 9 budget: unit prices, a run's worst-case cost, and the persistent ledger.

Nothing here knows a provider price. Every price comes from settings (STAGE9_*), has no default,
and must be the provider's MAXIMUM price for that operation, confirmed by the user. A missing
price makes every run refused: an unknown cost is never treated as zero.

Worst case per HTTP attempt (the meter enforces each of these before the request is sent):
  - Exa /search:   STAGE9_EXA_USD_PER_SEARCH_MAX, for at most STAGE9_EXA_SEARCH_MAX_RESULTS results;
  - Exa /contents: STAGE9_EXA_USD_PER_PAGE_MAX x number of URLs in the request;
  - OpenRouter /chat/completions: input price x (request bytes + STAGE9_LLM_TOKEN_OVERHEAD)
    + output price x max_completion_tokens. A byte-level tokenizer never produces more tokens
    than bytes, and max_completion_tokens caps the output including reasoning tokens.

A run's worst case multiplies these by the most calls the pipeline can make with the current
settings, and by every HTTP attempt a call can take (transport retries, the LLM repair attempt).
Failed attempts are counted as if billed.
"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

LEDGER_VERSION = 1
LLM_REPAIR_ATTEMPTS = 1  # OpenRouterClient default, used by both agents (services/llm/_build)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _d(v) -> Decimal:
    return Decimal(str(v))


# --------------------------------------------------------------------------- prices


@dataclass(frozen=True)
class UnitPrices:
    exa_search: Decimal
    exa_search_max_results: int
    exa_page: Decimal
    llm_input_token: Decimal
    llm_output_token: Decimal
    llm_max_request_bytes: int
    llm_token_overhead: int

    @staticmethod
    def missing(settings) -> list[str]:
        names = {
            "STAGE9_EXA_USD_PER_SEARCH_MAX": settings.stage9_exa_usd_per_search_max,
            "STAGE9_EXA_USD_PER_PAGE_MAX": settings.stage9_exa_usd_per_page_max,
            "STAGE9_LLM_USD_PER_INPUT_TOKEN_MAX": settings.stage9_llm_usd_per_input_token_max,
            "STAGE9_LLM_USD_PER_OUTPUT_TOKEN_MAX": settings.stage9_llm_usd_per_output_token_max,
        }
        return [k for k, v in names.items() if v is None]

    @classmethod
    def from_settings(cls, settings) -> UnitPrices:
        missing = cls.missing(settings)
        if missing:
            raise ValueError(f"Stage 9 unit prices not configured: {', '.join(missing)}")
        return cls(
            exa_search=settings.stage9_exa_usd_per_search_max,
            exa_search_max_results=settings.stage9_exa_search_max_results,
            exa_page=settings.stage9_exa_usd_per_page_max,
            llm_input_token=settings.stage9_llm_usd_per_input_token_max,
            llm_output_token=settings.stage9_llm_usd_per_output_token_max,
            llm_max_request_bytes=settings.stage9_llm_max_request_bytes,
            llm_token_overhead=settings.stage9_llm_token_overhead,
        )

    def llm_call_max(self, request_bytes: int, max_completion_tokens: int) -> Decimal:
        return (self.llm_input_token * (request_bytes + self.llm_token_overhead)
                + self.llm_output_token * max_completion_tokens)  # fmt: skip


# --------------------------------------------------------------------------- worst case


@dataclass
class RunEstimate:
    """The most a run can make and cost with the current settings. Informational fields are
    the call counts; `worst_case_usd` is their sum at the configured maximum unit prices."""

    products: int
    discovery: bool
    max_targets: int
    llm1: bool
    llm2: bool
    exa_searches: int
    exa_pages: int
    llm1_calls: int
    llm2_calls: int
    exa_attempts_per_call: int
    llm_attempts_per_call: int
    exa_usd: Decimal
    openrouter_usd: Decimal
    notes: list[str] = field(default_factory=list)

    @property
    def worst_case_usd(self) -> Decimal:
        return self.exa_usd + self.openrouter_usd

    def to_json(self) -> str:
        d = asdict(self)
        d["worst_case_usd"] = self.worst_case_usd
        return json.dumps(d, default=str, sort_keys=True)


def estimate_run(settings, prices: UnitPrices, *, products: int, run_discovery: bool, max_targets: int,
                 llm1: bool, llm2: bool) -> RunEstimate:  # fmt: skip
    """Upper bound of one pipeline run's paid calls and cost (see the module docstring)."""
    s = settings
    exa_attempts = s.search_max_retries + 1
    llm_attempts = (s.llm_max_retries + 1) * (LLM_REPAIR_ATTEMPTS + 1)
    notes = []
    # Discovery: (baseline + up to max_global) searches per product, one profile search per
    # candidate (at most discovery_max_candidates), LLM 1 plan + classification batches.
    d_searches = d_llm = 0
    if run_discovery:
        d_searches = products * (s.discovery_max_global_queries_per_product + 1)
        if s.discovery_profile_results:
            d_searches += s.discovery_max_candidates
        if llm1:
            d_llm = 1 + math.ceil(s.discovery_max_candidates / s.discovery_classification_batch_size)
    # Product search: every target x queries per domain, per product; at most
    # intelligence_max_pages_per_product pages; LLM 2 extraction (+ match proposal) per page.
    i_searches = products * max_targets * s.intelligence_queries_per_domain
    pages_per_product = min(s.intelligence_max_pages_per_product, max_targets * s.intelligence_max_pages_per_domain)
    pages = products * pages_per_product
    i_llm = pages * (2 if s.intelligence_match_proposals else 1) if llm2 else 0
    if s.discovery_results_per_query > prices.exa_search_max_results or \
            s.intelligence_results_per_query > prices.exa_search_max_results:  # fmt: skip
        notes.append("a configured results-per-query exceeds STAGE9_EXA_SEARCH_MAX_RESULTS: such searches "
                     "will be refused by the meter")  # fmt: skip
    searches = d_searches + i_searches
    exa_usd = (prices.exa_search * searches + prices.exa_page * pages) * exa_attempts
    llm1_max = prices.llm_call_max(prices.llm_max_request_bytes, s.llm1_max_completion_tokens)
    llm2_max = prices.llm_call_max(prices.llm_max_request_bytes, s.llm2_max_completion_tokens)
    or_usd = (llm1_max * d_llm + llm2_max * i_llm) * llm_attempts
    return RunEstimate(products=products, discovery=run_discovery, max_targets=max_targets, llm1=llm1, llm2=llm2,
                       exa_searches=searches, exa_pages=pages, llm1_calls=d_llm, llm2_calls=i_llm,
                       exa_attempts_per_call=exa_attempts, llm_attempts_per_call=llm_attempts,
                       exa_usd=exa_usd, openrouter_usd=or_usd, notes=notes)  # fmt: skip


# --------------------------------------------------------------------------- ledger


class RunStatus(StrEnum):
    RESERVED = "RESERVED"  # reservation held; run running, or the process stopped mid-run
    RECONCILED = "RECONCILED"  # every attempt had a provider-reported cost within its bound
    NEEDS_REVIEW = "NEEDS_REVIEW"  # unreported costs (charged at their bound), a cost above its bound, or a mismatch
    RESOLVED = "RESOLVED"  # amounts confirmed by hand (e.g. from the provider dashboards)
    RELEASED = "RELEASED"  # reserved but never started: nothing was spent
    REFUSED = "REFUSED"  # the reservation did not fit the remaining budget: never started


class AttemptOutcome(StrEnum):
    REPORTED = "REPORTED"  # provider reported the cost
    UNREPORTED = "UNREPORTED"  # a response without a cost (error or missing field): charged at the bound
    NO_RESPONSE = "NO_RESPONSE"  # timeout or transport error: charged at the bound
    REFUSED = "REFUSED"  # stopped by the meter before sending: nothing spent


class BudgetRefused(Exception):
    def __init__(self, message: str, requested: Decimal, remaining: Decimal):
        super().__init__(message)
        self.requested, self.remaining = requested, remaining


class LedgerError(Exception):
    pass


_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, status TEXT NOT NULL, reserved_usd TEXT NOT NULL, estimate_json TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    reported_exa_usd TEXT, reported_openrouter_usd TEXT, charged_exa_usd TEXT, charged_openrouter_usd TEXT,
    pipeline_exa_usd TEXT, pipeline_openrouter_usd TEXT,
    unreported_attempts INTEGER NOT NULL DEFAULT 0, refused_attempts INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), provider TEXT NOT NULL,
    endpoint TEXT NOT NULL, bound_usd TEXT NOT NULL, reported_usd TEXT, charged_usd TEXT NOT NULL,
    http_status INTEGER, outcome TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, run_id TEXT, action TEXT NOT NULL, detail TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class LedgerSummary:
    ceiling_usd: Decimal
    committed_usd: Decimal
    remaining_usd: Decimal
    runs: dict[str, int]
    open_reservations: list[str]
    needs_review: list[str]


class BudgetLedger:
    """Persistent Stage 9 ledger (its own SQLite file; never the review database).

    Reservations are made in an IMMEDIATE transaction, so two runs (or two processes) can never
    reserve the same remaining budget. Committed spend per run: RESERVED counts the full
    reservation (or more, if charges already exceed it); RECONCILED / NEEDS_REVIEW count the
    charged amount (reported costs, plus the bound for every attempt without one); RESOLVED counts
    the confirmed amount; RELEASED and REFUSED count nothing."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise LedgerError(f"Stage 9 ledger not found at {self.path}; create it with "
                              "`python -m app.stage9.cli init --ceiling <USD>`")  # fmt: skip
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        if row is None or int(row[0]) != LEDGER_VERSION:
            raise LedgerError(f"{self.path} is not a Stage 9 ledger (version {LEDGER_VERSION})")

    @classmethod
    def create(cls, path: Path, ceiling_usd: Decimal, note: str = "") -> BudgetLedger:
        path = Path(path)
        if path.exists():
            raise LedgerError(f"{path} already exists; a ledger is never overwritten")
        if ceiling_usd <= 0:
            raise LedgerError("The ceiling must be positive")
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            conn.executescript(_SCHEMA)
            conn.executemany("INSERT INTO meta VALUES (?, ?)", [
                ("version", str(LEDGER_VERSION)), ("ceiling_usd", str(ceiling_usd)), ("created_at", _now()),
                ("note", note)])  # fmt: skip
            conn.execute("INSERT INTO history (at, action, detail) VALUES (?, 'create', ?)",
                         (_now(), f"ceiling {ceiling_usd} USD {note}".strip()))  # fmt: skip
            conn.commit()
        finally:
            conn.close()
        return cls(path)

    @contextmanager
    def _conn(self, write: bool = False):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")  # one writer at a time, across processes
            yield conn
            if write:
                conn.execute("COMMIT")
        except BaseException:
            if write:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        finally:
            conn.close()

    # ---------------------------------------------------------------- reading

    @property
    def ceiling_usd(self) -> Decimal:
        with self._conn() as c:
            return _d(c.execute("SELECT value FROM meta WHERE key='ceiling_usd'").fetchone()[0])

    @staticmethod
    def _committed(row: sqlite3.Row | tuple) -> Decimal:
        status, reserved, ch_exa, ch_or = row
        charged = _d(ch_exa or 0) + _d(ch_or or 0)
        if status == RunStatus.RESERVED:
            return max(_d(reserved), charged)
        if status in (RunStatus.RECONCILED, RunStatus.NEEDS_REVIEW, RunStatus.RESOLVED):
            return charged
        return Decimal(0)

    def _committed_total(self, c) -> Decimal:
        rows = c.execute("SELECT status, reserved_usd, charged_exa_usd, charged_openrouter_usd FROM runs").fetchall()
        return sum((self._committed(r) for r in rows), Decimal(0))

    def summary(self) -> LedgerSummary:
        with self._conn() as c:
            ceiling = _d(c.execute("SELECT value FROM meta WHERE key='ceiling_usd'").fetchone()[0])
            committed = self._committed_total(c)
            counts = dict(c.execute("SELECT status, COUNT(*) FROM runs GROUP BY status").fetchall())
            open_ = [r[0] for r in c.execute("SELECT run_id FROM runs WHERE status=? ORDER BY created_at",
                                             (RunStatus.RESERVED,))]  # fmt: skip
            review = [r[0] for r in c.execute("SELECT run_id FROM runs WHERE status=? ORDER BY created_at",
                                              (RunStatus.NEEDS_REVIEW,))]  # fmt: skip
        return LedgerSummary(ceiling, committed, ceiling - committed, counts, open_, review)

    def run(self, run_id: str) -> dict | None:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            row = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def attempts(self, run_id: str) -> list[dict]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY id", (run_id,))]

    # ---------------------------------------------------------------- writing

    def reserve(self, run_id: str, amount: Decimal, estimate_json: str | None = None) -> Decimal:
        """Reserve `amount` for `run_id` atomically. Raises BudgetRefused (recorded as REFUSED)."""
        if amount <= 0:
            raise LedgerError("A reservation must be positive")
        with self._conn(write=True) as c:
            if c.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
                raise LedgerError(f"Run {run_id} already has a ledger entry")
            ceiling = _d(c.execute("SELECT value FROM meta WHERE key='ceiling_usd'").fetchone()[0])
            remaining = ceiling - self._committed_total(c)
            now = _now()
            if amount > remaining:
                c.execute("INSERT INTO runs (run_id, status, reserved_usd, estimate_json, created_at, updated_at, "
                          "notes) VALUES (?, ?, '0', ?, ?, ?, ?)",
                          (run_id, RunStatus.REFUSED, estimate_json, now, now,
                           json.dumps([f"requested {amount} USD, remaining {remaining} USD"])))  # fmt: skip
                c.execute("INSERT INTO history (at, run_id, action, detail) VALUES (?, ?, 'refuse', ?)",
                          (now, run_id, f"requested {amount}, remaining {remaining}"))  # fmt: skip
                refused = BudgetRefused(f"Stage 9 budget: this run needs up to {amount} USD but only {remaining} "
                                        f"USD of the {ceiling} USD ceiling remains", amount, remaining)  # fmt: skip
            else:
                c.execute("INSERT INTO runs (run_id, status, reserved_usd, estimate_json, created_at, updated_at) "
                          "VALUES (?, ?, ?, ?, ?, ?)", (run_id, RunStatus.RESERVED, str(amount), estimate_json, now, now))
                c.execute("INSERT INTO history (at, run_id, action, detail) VALUES (?, ?, 'reserve', ?)",
                          (now, run_id, f"{amount} USD; remaining after: {remaining - amount}"))  # fmt: skip
                refused = None
        if refused is not None:
            raise refused
        return remaining - amount

    def record_attempt(self, run_id: str, provider: str, endpoint: str, bound: Decimal, reported: Decimal | None,
                       charged: Decimal, http_status: int | None, outcome: AttemptOutcome) -> None:  # fmt: skip
        """Persist one HTTP attempt immediately (it survives a crash) and update the run's charges."""
        col = "charged_exa_usd" if provider == "exa" else "charged_openrouter_usd"
        rep = "reported_exa_usd" if provider == "exa" else "reported_openrouter_usd"
        with self._conn(write=True) as c:
            c.execute("INSERT INTO attempts (run_id, provider, endpoint, bound_usd, reported_usd, charged_usd, "
                      "http_status, outcome, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (run_id, provider, endpoint, str(bound), None if reported is None else str(reported),
                       str(charged), http_status, outcome, _now()))  # fmt: skip
            row = c.execute(f"SELECT {col}, {rep} FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise LedgerError(f"Attempt for unknown run {run_id}")
            new_charged = _d(row[0] or 0) + charged
            new_rep = _d(row[1] or 0) + (reported or Decimal(0))
            extra = {AttemptOutcome.REFUSED: ", refused_attempts = refused_attempts + 1",
                     AttemptOutcome.UNREPORTED: ", unreported_attempts = unreported_attempts + 1",
                     AttemptOutcome.NO_RESPONSE: ", unreported_attempts = unreported_attempts + 1"}.get(outcome, "")  # fmt: skip
            c.execute(f"UPDATE runs SET {col}=?, {rep}=?, updated_at=?{extra} WHERE run_id=?",
                      (str(new_charged), str(new_rep), _now(), run_id))  # fmt: skip

    def reconcile(self, run_id: str, pipeline_exa: Decimal | None, pipeline_openrouter: Decimal | None,
                  notes: list[str] | None = None) -> RunStatus:  # fmt: skip
        """Close a finished run: compare metered charges with the reservation and with the costs
        the pipeline itself recorded. Clean -> RECONCILED; anything doubtful -> NEEDS_REVIEW."""
        notes = list(notes or [])
        with self._conn(write=True) as c:
            row = c.execute("SELECT status, reserved_usd, reported_exa_usd, reported_openrouter_usd, charged_exa_usd, "
                            "charged_openrouter_usd, unreported_attempts, notes FROM runs WHERE run_id=?",
                            (run_id,)).fetchone()  # fmt: skip
            if row is None:
                raise LedgerError(f"Unknown run {run_id}")
            status, reserved, rep_exa, rep_or, ch_exa, ch_or, unreported, old_notes = row
            if status != RunStatus.RESERVED:
                raise LedgerError(f"Run {run_id} is {status}, not RESERVED")
            pairs = c.execute("SELECT reported_usd, bound_usd FROM attempts WHERE run_id=? AND reported_usd IS NOT NULL",
                              (run_id,)).fetchall()  # fmt: skip
            above = sum(_d(r) > _d(b) for r, b in pairs)
            charged = _d(ch_exa or 0) + _d(ch_or or 0)
            if unreported:
                notes.append(f"{unreported} attempt(s) had no provider-reported cost: charged at their maximum")
            if above:
                notes.append(f"{above} attempt(s) cost more than their configured maximum: check the unit prices")
            if charged > _d(reserved):
                notes.append(f"charged {charged} USD exceeds the reservation {reserved} USD")
            for name, metered, piped in (("exa", _d(rep_exa or 0), pipeline_exa),
                                         ("openrouter", _d(rep_or or 0), pipeline_openrouter)):  # fmt: skip
                if piped is None:
                    notes.append(f"{name}: the pipeline recorded no cost to compare")
                elif metered != piped:
                    notes.append(f"{name}: metered {metered} USD but the pipeline recorded {piped} USD")
            new = RunStatus.NEEDS_REVIEW if notes else RunStatus.RECONCILED
            c.execute("UPDATE runs SET status=?, pipeline_exa_usd=?, pipeline_openrouter_usd=?, notes=?, updated_at=? "
                      "WHERE run_id=?",
                      (new, None if pipeline_exa is None else str(pipeline_exa),
                       None if pipeline_openrouter is None else str(pipeline_openrouter),
                       json.dumps(json.loads(old_notes) + notes), _now(), run_id))  # fmt: skip
            c.execute("INSERT INTO history (at, run_id, action, detail) VALUES (?, ?, 'reconcile', ?)",
                      (_now(), run_id, f"{new}: charged {charged} of {reserved} reserved"))  # fmt: skip
        return new

    def release(self, run_id: str, reason: str) -> None:
        """A reserved run that never started (no attempts): its reservation is returned."""
        with self._conn(write=True) as c:
            n = c.execute("SELECT COUNT(*) FROM attempts WHERE run_id=? AND outcome != ?",
                          (run_id, AttemptOutcome.REFUSED)).fetchone()[0]  # fmt: skip
            if n:
                raise LedgerError(f"Run {run_id} made {n} paid attempt(s); reconcile it instead of releasing it")
            c.execute("UPDATE runs SET status=?, updated_at=? WHERE run_id=? AND status=?",
                      (RunStatus.RELEASED, _now(), run_id, RunStatus.RESERVED))  # fmt: skip
            c.execute("INSERT INTO history (at, run_id, action, detail) VALUES (?, ?, 'release', ?)",
                      (_now(), run_id, reason))  # fmt: skip

    def resolve(self, run_id: str, exa_usd: Decimal, openrouter_usd: Decimal, note: str) -> None:
        """Confirm a RESERVED (interrupted) or NEEDS_REVIEW run's real cost by hand. Never below
        what the providers already reported for it."""
        if not note.strip():
            raise LedgerError("A resolution needs a note saying where the amounts come from")
        with self._conn(write=True) as c:
            row = c.execute("SELECT status, reported_exa_usd, reported_openrouter_usd, notes FROM runs WHERE run_id=?",
                            (run_id,)).fetchone()  # fmt: skip
            if row is None:
                raise LedgerError(f"Unknown run {run_id}")
            status, rep_exa, rep_or, old = row
            if status not in (RunStatus.RESERVED, RunStatus.NEEDS_REVIEW):
                raise LedgerError(f"Run {run_id} is {status}; only RESERVED or NEEDS_REVIEW runs can be resolved")
            if exa_usd < _d(rep_exa or 0) or openrouter_usd < _d(rep_or or 0):
                raise LedgerError("Resolved amounts cannot be below the provider-reported costs already recorded")
            c.execute("UPDATE runs SET status=?, charged_exa_usd=?, charged_openrouter_usd=?, notes=?, updated_at=? "
                      "WHERE run_id=?",
                      (RunStatus.RESOLVED, str(exa_usd), str(openrouter_usd),
                       json.dumps(json.loads(old) + [f"resolved by hand: {note}"]), _now(), run_id))  # fmt: skip
            c.execute("INSERT INTO history (at, run_id, action, detail) VALUES (?, ?, 'resolve', ?)",
                      (_now(), run_id, f"exa {exa_usd}, openrouter {openrouter_usd}: {note}"))  # fmt: skip
