"""Budgeted pipeline runs (Stage 9): reserve before start, meter during, reconcile after.

`BudgetedRunManager` replaces the normal RunManager when STAGE9_LEDGER_PATH is set. For each
POST /api/runs it:

  1. runs the normal validation (products, provider, domain subset, active-run limit);
  2. refuses (503) unless the ledger exists, every unit price is configured and the search
     provider is Exa (the only priced search provider);
  3. computes the run's worst case (`budget.estimate_run`) and reserves
     min(worst case, STAGE9_MAX_RESERVATION_PER_RUN_USD) in the ledger, atomically; a
     reservation that does not fit the remaining budget is refused (402) and recorded;
  4. starts the run with its own RunMeter in context, so every paid client the run creates is
     metered and capped at the reservation;
  5. when the run's task ends (completed, failed or cancelled), closes the meter and reconciles
     the ledger against the costs the pipeline itself recorded.

A reservation is persisted before the run's task exists. If the process stops mid-run, the
reservation stays RESERVED and keeps counting in full until resolved by hand.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from app.agents.discovery import llm1_disabled_reason
from app.agents.intelligence import llm2_disabled_reason
from app.orchestration.pipeline import RunManager, RunRequestError
from app.orchestration.schemas import ExecutionRun, PipelineResult, RunRequest
from app.schemas.review import ReviewDecision
from app.services.domains import registrable_domain
from app.stage9.budget import BudgetLedger, BudgetRefused, LedgerError, RunEstimate, UnitPrices, estimate_run
from app.stage9.context import CURRENT_METER
from app.stage9.meter import RunMeter

log = logging.getLogger(__name__)


def max_targets(manager: RunManager, req: RunRequest) -> int:
    """Upper bound on the domains this run can search: configured + reviewer-approved
    (+ the automatic cap when automatic search is on), or the requested subset if smaller."""
    s = manager.d.settings
    configured = {registrable_domain(c.domain) for c in manager.d.catalogue.competitors}
    approved = {d.domain for d in manager.d.review_store.list_decisions()
                if d.decision is ReviewDecision.APPROVED and d.domain not in configured}  # fmt: skip
    n = len(configured) + len(approved) + (s.max_auto_search_targets if s.pipeline_search_auto_approved else 0)
    if req.domains:
        n = min(n, len({registrable_domain(d) for d in req.domains}))
    return n


def pipeline_costs(result: PipelineResult | None) -> tuple[Decimal | None, Decimal | None]:
    """(Exa, OpenRouter) costs the pipeline itself recorded for a run, None if nothing was recorded."""
    if result is None:
        return None, None
    exa: Decimal | None = None
    llm: Decimal | None = None
    d = result.discovery.usage if result.discovery else None
    i = result.intelligence_usage
    for part in ((d.search_cost_usd,) if d else ()) + ((i.search_cost_usd, i.retrieval_cost_usd) if i else ()):
        exa = (exa or Decimal(0)) + Decimal(part)
    for part in ((d.llm_cost_usd,) if d else ()) + ((i.llm_cost_usd,) if i else ()):
        if part is not None:
            llm = (llm or Decimal(0)) + Decimal(part)
    if (d is not None or i is not None) and llm is None:
        llm = Decimal(0)
    return exa, llm


class BudgetedRunManager(RunManager):
    def __init__(self, store, deps):
        super().__init__(store, deps)
        self.meters: dict[str, RunMeter] = {}

    def ledger(self) -> BudgetLedger:
        try:
            return BudgetLedger(self.d.settings.stage9_ledger_path)
        except LedgerError as e:
            raise RunRequestError(503, str(e)) from None

    def prices(self) -> UnitPrices:
        missing = UnitPrices.missing(self.d.settings)
        if missing:
            raise RunRequestError(503, {"message": "Stage 9 budget mode: unit prices not configured; every run is "
                                                   "refused until they are", "missing": missing})  # fmt: skip
        return UnitPrices.from_settings(self.d.settings)

    def estimate(self, req: RunRequest, prices: UnitPrices) -> RunEstimate:
        s = self.d.settings
        return estimate_run(s, prices, products=len(req.product_ids), run_discovery=req.run_discovery,
                            max_targets=max_targets(self, req), llm1=llm1_disabled_reason(s) is None,
                            llm2=llm2_disabled_reason(s) is None)  # fmt: skip

    def reservation_for(self, est: RunEstimate) -> Decimal:
        cap = self.d.settings.stage9_max_reservation_per_run_usd
        return est.worst_case_usd if cap is None else min(est.worst_case_usd, cap)

    def start(self, req: RunRequest) -> ExecutionRun:
        products, req = self.validate(req)
        if self.d.settings.search_provider != "exa":
            raise RunRequestError(503, "Stage 9 budget mode prices Exa only: set SEARCH_PROVIDER=exa")
        ledger, prices = self.ledger(), self.prices()
        # Never continue after an unresolved accounting or reconciliation problem: runs are
        # sequential, and any RESERVED (running or interrupted) or NEEDS_REVIEW entry blocks new
        # runs until it is reconciled or resolved by hand (`cli resolve`).
        summary = ledger.summary()
        unresolved = summary.open_reservations + summary.needs_review
        if unresolved:
            raise RunRequestError(409, {"message": "Stage 9 ledger has unresolved runs; resolve them before starting "
                                                   "another", "unresolved": unresolved})  # fmt: skip
        est = self.estimate(req, prices)
        amount = self.reservation_for(est)
        run_id = self.new_run_id()
        try:
            ledger.reserve(run_id, amount, est.to_json())
        except BudgetRefused as e:
            raise RunRequestError(402, {"message": str(e), "requested_usd": str(e.requested),
                                        "remaining_usd": str(e.remaining)}) from None  # fmt: skip
        meter = RunMeter(run_id, amount, prices, ledger)
        token = CURRENT_METER.set(meter)
        try:
            run = self._launch(run_id, products, req)  # the run's task copies this context (its meter)
        except BaseException:
            ledger.release(run_id, "the run could not be started")
            raise
        finally:
            CURRENT_METER.reset(token)
        self.meters[run_id] = meter
        task = self._tasks.get(run_id)
        if task is not None:
            task.add_done_callback(lambda t, rid=run_id: self._finish(rid))
        else:  # finished synchronously (should not happen): settle now
            self._finish(run_id)
        return run

    def _finish(self, run_id: str) -> None:
        meter = self.meters.pop(run_id, None)
        if meter is None:
            return
        try:
            meter.close()
            exa, llm = pipeline_costs(self.store.get_result(run_id))
            notes = [f"accounting error during the run: {meter.accounting_error}"] if meter.accounting_error else []
            status = meter.ledger.reconcile(run_id, exa, llm, notes)
            log.info("Stage 9 ledger: run %s %s (charged %s of %s USD reserved)", run_id, status.value,
                     meter.charged, meter.reservation)  # fmt: skip
        except Exception:  # never lose the reservation: it stays RESERVED and counts in full
            log.exception("Stage 9 ledger: could not reconcile run %s; its reservation stays held", run_id)
