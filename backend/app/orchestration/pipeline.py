"""Phase 7: end-to-end pipeline orchestration.

One run, for 1..N selected Stompbox products, executes these stages:

  validate       product ids, run limits, search-provider configuration (before the run exists)
  discovery      Phase 3 agent (LLM 1 when configured, otherwise no-LLM baseline)
  review_queue   Phase 4: newly discovered candidates saved (PENDING unless a reviewer decided)
  targets        13 configured + reviewer-APPROVED domains + automatically search-eligible
                 domains (this run's and stored ones, Stage 8.1; capped by
                 MAX_AUTO_SEARCH_TARGETS; when PIPELINE_SEARCH_AUTO_APPROVED is on);
                 ineligible pending, rejected and directory domains are never searched
  product_search Phase 5 domain-restricted search + page retrieval, product by product
  extraction     Phase 5 LLM 2, or SKIPPED (SKIPPED_NO_LLM on every retrieved page)
  matching       Phase 6 matching + price validation, right after each product's search

Every stage reuses the existing agents/services; this module only sequences
them, isolates failures, records progress and emits events. Results are kept
in the temporary `RunStore` only.

Failure isolation: a failed discovery leaves the configured/approved domains
to search; a failed domain is recorded by the Phase 5 agent and the others
continue; a failed or timed-out product does not stop the next product.
Transport-level retries (429/5xx/timeouts) are done by the provider HTTP
client (`SEARCH_MAX_RETRIES`, `LLM_MAX_RETRIES`).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.agents.discovery import CompetitorDiscoveryAgent, build_discovery_agent
from app.agents.discovery.schemas import SearchTarget
from app.agents.intelligence import (
    ProductIntelligenceAgent,
    build_intelligence_agent,
    eligible_search_targets,
)
from app.agents.intelligence.schemas import ExtractionStatus, ProductIntelligence, SkippedTarget
from app.core.config import Settings
from app.orchestration.schemas import (
    TERMINAL,
    DomainRunOutcome,
    DomainRunStatus,
    ExecutionEvent,
    ExecutionRun,
    PipelineResult,
    ProductRunResult,
    ProductRunStatus,
    RunRequest,
    RunStatus,
    StageName,
    StageState,
    StageStatus,
)
from app.orchestration.store import RunStore
from app.schemas.catalogue import StompboxProduct
from app.schemas.common import DomainApprovalStatus, MatchStatus
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.schemas.search import RetrievalStatus
from app.services.catalogue import Catalogue
from app.services.domains import registrable_domain
from app.services.matching import evaluate_product
from app.services.review_store import ReviewStore, merge_candidate
from app.services.search import configured_providers

log = logging.getLogger(__name__)
MAX_ERROR_CHARS = 300

DiscoveryFactory = Callable[[list[DomainDecisionRecord]], Awaitable[CompetitorDiscoveryAgent]]
IntelligenceFactory = Callable[[list[SearchTarget], list[SkippedTarget]], Awaitable[ProductIntelligenceAgent]]


def _now() -> datetime:
    return datetime.now(UTC)


def _err(e: BaseException) -> str:
    text = str(e) or type(e).__name__
    return text[:MAX_ERROR_CHARS]


class RunRequestError(Exception):
    """Invalid run request. `status_code` maps to HTTP; `detail` is UI-safe."""

    def __init__(self, status_code: int, detail: Any):
        self.status_code, self.detail = status_code, detail
        super().__init__(str(detail))


@dataclass
class PipelineDeps:
    settings: Settings
    catalogue: Catalogue
    review_store: ReviewStore
    discovery_factory: DiscoveryFactory
    intelligence_factory: IntelligenceFactory
    clock: Callable[[], datetime] = _now

    @classmethod
    def default(cls, settings: Settings, catalogue: Catalogue, review_store: ReviewStore) -> PipelineDeps:
        return cls(
            settings, catalogue, review_store,
            discovery_factory=lambda decisions: build_discovery_agent(settings, catalogue, review_decisions=decisions),
            intelligence_factory=lambda targets, skipped: build_intelligence_agent(settings, targets, skipped),
        )  # fmt: skip


# --------------------------------------------------------------------------- one run


class PipelineRun:
    def __init__(self, run: ExecutionRun, result: PipelineResult, products: list[StompboxProduct],
                 store: RunStore, deps: PipelineDeps):  # fmt: skip
        self.run, self.result, self.products, self.store, self.d = run, result, products, store, deps
        self.s = deps.settings
        self._stages = {st.name: st for st in run.stages}

    # ------------------------------------------------------------------ helpers

    def emit(self, type_: str, message: str, *, product_id: str | None = None, stage: StageName | None = None,
             **payload: Any) -> None:  # fmt: skip
        seq = self.run.last_event_seq + 1
        essential = type_.startswith(("run_", "stage_", "product_"))
        if seq > self.s.pipeline_max_events_per_run and not essential:
            return  # cap detail events; lifecycle events are always kept
        self.store.append_event(ExecutionEvent(
            seq=seq, event_id=f"{self.run.run_id}:{seq}", run_id=self.run.run_id, type=type_,
            timestamp=self.d.clock(), product_id=product_id, stage=stage, message=message, payload=payload,
        ))  # fmt: skip

    def save(self) -> None:
        self.store.save(self.run, self.result)

    def stage(self, name: StageName) -> StageState:
        return self._stages[name]

    def start_stage(self, name: StageName, total: int = 0, message: str | None = None) -> StageState:
        st = self.stage(name)
        st.status, st.started_at, st.total, st.message = StageStatus.RUNNING, self.d.clock(), total, message
        self.emit("stage_started", message or f"{name.value} started", stage=name, total=total)
        self.save()
        return st

    def end_stage(self, name: StageName, status: StageStatus, message: str | None = None) -> None:
        st = self.stage(name)
        st.status, st.finished_at = status, self.d.clock()
        if st.started_at is None:
            st.started_at = st.finished_at
        if message:
            st.message = message
        self.emit("stage_completed", st.message or f"{name.value} {status.value.lower()}", stage=name,
                  status=status.value, done=st.done, total=st.total, errors=st.errors[:5])  # fmt: skip
        self.save()

    def warn(self, text: str) -> None:
        if text not in self.run.warnings:
            self.run.warnings.append(text)

    # ------------------------------------------------------------------ stages

    async def _discovery(self) -> None:
        if not self.run.request.run_discovery:
            self.end_stage(StageName.DISCOVERY, StageStatus.SKIPPED, "Discovery not requested for this run")
            self.end_stage(StageName.REVIEW_QUEUE, StageStatus.SKIPPED, "No discovery results to queue")
            return
        self.start_stage(StageName.DISCOVERY, total=len(self.products), message="Discovering UK competitors")
        agent = None
        try:
            agent = await self.d.discovery_factory(self.d.review_store.list_decisions())
            disc = await asyncio.wait_for(agent.run(self.products), self.s.pipeline_discovery_timeout_seconds)
        except TimeoutError:
            self._discovery_failed(f"Discovery timed out after {self.s.pipeline_discovery_timeout_seconds:.0f} s")
            return
        except Exception as e:  # provider errors, misconfiguration, unexpected bugs: never fatal for the run
            log.warning("discovery failed in run %s", self.run.run_id, exc_info=not _is_provider_error(e))
            self._discovery_failed(f"Discovery failed: {_err(e)}")
            return
        finally:
            if agent is not None:
                await agent.aclose()
        self.result.discovery = disc
        st = self.stage(StageName.DISCOVERY)
        st.done = len(self.products)
        llm = "LLM 1 used" if disc.mode.value == "LLM_ENABLED" else f"LLM 1 skipped ({disc.llm_disabled_reason})"
        for c in disc.candidates:
            payload = dict(domain=c.domain, approval_status=c.approval_status.value,
                           classification_status=c.classification_status.value,
                           company_type=c.company_type and c.company_type.value,
                           # Final class after the code safeguards, and LLM 1's raw proposal (null if none).
                           competitor_class=c.competitor_class and c.competitor_class.value,
                           proposed_competitor_class=c.proposed_competitor_class and c.proposed_competitor_class.value,
                           reviewed=c.review_decision is not None,
                           # Automatic search eligibility (Stage 8.1); informational UK answer.
                           auto_search_eligible=c.approval_status is DomainApprovalStatus.AUTO_APPROVED,
                           serves_uk_customers=c.serves_uk_customers.value)  # fmt: skip
            self.emit("competitor_discovered", f"Discovered {c.domain} ({c.approval_status.value})",
                      stage=StageName.DISCOVERY, **payload)  # fmt: skip
            if c.approval_status is DomainApprovalStatus.AUTO_APPROVED:
                msg = (f"{c.domain} is automatically searchable" if self.s.pipeline_search_auto_approved else
                       f"{c.domain} is automatically eligible, but automatic search is switched off "
                       "(PIPELINE_SEARCH_AUTO_APPROVED=false): not searched")  # fmt: skip
                self.emit("domain_auto_approved", msg, stage=StageName.DISCOVERY, domain=c.domain)
        for w in disc.warnings:
            self.warn(f"discovery: {w}")
        errors = disc.usage.search_errors
        if errors:
            st.errors.append(f"{errors} discovery search(es) failed")
        self.end_stage(StageName.DISCOVERY, StageStatus.PARTIAL if errors else StageStatus.COMPLETED,
                       f"{len(disc.candidates)} new candidate(s); {llm}")  # fmt: skip
        self._review_queue(disc)

    def _discovery_failed(self, message: str) -> None:
        self.stage(StageName.DISCOVERY).errors.append(message)
        self.warn(message)
        self.end_stage(StageName.DISCOVERY, StageStatus.FAILED, message + "; continuing with configured and "
                                                                          "approved domains")  # fmt: skip
        self.end_stage(StageName.REVIEW_QUEUE, StageStatus.SKIPPED, "Discovery failed: nothing to queue")

    def _review_queue(self, disc) -> None:
        self.start_stage(StageName.REVIEW_QUEUE, total=len(disc.candidates))
        try:
            written = self.d.review_store.record_run(disc)
        except Exception as e:  # sqlite failure must not lose the run
            log.exception("review queue write failed in run %s", self.run.run_id)
            self.stage(StageName.REVIEW_QUEUE).errors.append(_err(e))
            self.end_stage(StageName.REVIEW_QUEUE, StageStatus.FAILED, f"Could not save candidates: {_err(e)}")
            return
        pending = [c.domain for c in disc.candidates
                   if c.review_decision is None and c.approval_status is DomainApprovalStatus.APPROVAL_REQUIRED]  # fmt: skip
        self.result.new_pending_domains = pending
        for d in pending:
            self.emit("domain_approval_required", f"{d} needs review before it can be searched",
                      stage=StageName.REVIEW_QUEUE, domain=d)  # fmt: skip
        self.stage(StageName.REVIEW_QUEUE).done = written
        self.end_stage(StageName.REVIEW_QUEUE, StageStatus.COMPLETED,
                       f"{written} candidate(s) saved to the review queue: " + _queue_breakdown(disc.candidates, pending))

    def _targets(self) -> list[SearchTarget]:
        self.start_stage(StageName.TARGETS)
        disc = self.result.discovery
        # Automatic search eligibility is recomputed from the stored records (earlier runs) and
        # this run's candidates; it never grants benchmark permission and a reviewer decision
        # always wins (`eligible_search_targets`). A store read failure leaves this run's only.
        try:
            stored = [s.candidate for s in self.d.review_store.list_candidates()]
        except Exception as e:  # sqlite failure must not lose the run
            self.warn(f"Could not read stored candidates; automatic targets from this run only: {_err(e)}")
            stored = []
        # This run's candidates merged onto their stored records exactly as the review queue does
        # (`merge_candidate`), so a run without LLM 1 never hides a stored classification.
        by_domain = {c.domain: c for c in stored}
        for c in (disc.candidates if disc else []):
            fresh = c.model_copy(update={"review_decision": None})
            by_domain[c.domain] = merge_candidate(by_domain[c.domain], fresh) if c.domain in by_domain else fresh
        candidates = list(by_domain.values())
        wanted = {registrable_domain(d) for d in self.run.request.domains} if self.run.request.domains else None
        targets, skipped = eligible_search_targets(self.d.catalogue.competitors, self.d.review_store.list_decisions(),
                                                   self.s, candidates=candidates,
                                                   product_ids=[p.product_id for p in self.products],
                                                   only_domains=wanted)  # fmt: skip
        if wanted is not None:
            targets = [t for t in targets if t.domain in wanted]
        auto = sum(t.approval_status is DomainApprovalStatus.AUTO_APPROVED for t in targets)
        self.result.search_targets, self.result.skipped_targets = targets, skipped
        configured = sum(t.approval_status is DomainApprovalStatus.PRECONFIGURED for t in targets)
        approved = sum(t.approval_status is DomainApprovalStatus.APPROVED for t in targets)
        outcomes = domain_outcomes(disc.candidates if disc else [], targets, skipped,
                                   auto_search_enabled=self.s.pipeline_search_auto_approved,
                                   domain_subset=wanted)  # fmt: skip
        self.result.domain_outcomes = outcomes
        self.emit("known_domains_loaded", f"{len(targets)} domain(s) to search", stage=StageName.TARGETS,
                  configured=configured, approved=approved, auto_approved=auto,
                  domains=[t.domain for t in targets],
                  skipped=[{"domain": s.domain, "reason": s.reason} for s in skipped],
                  pending_not_searched=self.result.new_pending_domains,
                  discovered=[o.model_dump(mode="json") for o in outcomes])  # fmt: skip
        st = self.stage(StageName.TARGETS)
        st.done = st.total = len(targets)
        if not targets:
            self.warn("No eligible search targets: nothing will be searched")
        message = (f"{len(targets)} domain(s) to search: {configured} configured, {approved} reviewer-approved, "
                   f"{auto} automatic; {len(skipped)} skipped")  # fmt: skip
        if outcomes:
            message += f". Discovered in this run: {_outcome_breakdown(outcomes)}"
        self.end_stage(StageName.TARGETS, StageStatus.COMPLETED, message)
        return targets

    async def _products(self, targets: list[SearchTarget]) -> None:
        n = len(self.products)
        self.start_stage(StageName.PRODUCT_SEARCH, total=n, message=f"Searching {len(targets)} domain(s)")
        try:
            agent = await self.d.intelligence_factory(targets, self.result.skipped_targets)
        except Exception as e:
            msg = f"Product search could not start: {_err(e)}"
            self.stage(StageName.PRODUCT_SEARCH).errors.append(msg)
            for pr in self.result.products:
                pr.status, pr.error = ProductRunStatus.FAILED, msg
            for name in (StageName.PRODUCT_SEARCH, StageName.EXTRACTION, StageName.MATCHING):
                self.end_stage(name, StageStatus.FAILED, msg)
            return
        try:
            self.result.intelligence_mode = agent.mode
            self.result.llm2_disabled_reason = agent.llm_disabled_reason
            self.result.intelligence_usage = agent.usage
            no_llm = agent.llm is None
            if no_llm:
                self.end_stage(StageName.EXTRACTION, StageStatus.SKIPPED,
                               f"LLM 2 not used ({agent.llm_disabled_reason}): retrieved pages are SKIPPED_NO_LLM, "
                               "no product fields extracted")  # fmt: skip
            else:
                self.start_stage(StageName.EXTRACTION, message=f"Extracting with {agent.llm.model}")
            self.start_stage(StageName.MATCHING, total=n)
            for p, pr in zip(self.products, self.result.products):
                await self._product(agent, p, pr, targets)
            self._finish_product_stages(agent, no_llm)
        finally:
            for w in agent.warnings:
                self.warn(f"product search: {w}")
            await agent.aclose()

    async def _product(self, agent: ProductIntelligenceAgent, p: StompboxProduct, pr: ProductRunResult,
                       targets: list[SearchTarget]) -> None:  # fmt: skip
        pr.status = ProductRunStatus.RUNNING
        self.emit("search_started", f"{p.product_id}: searching {len(targets)} domain(s)", product_id=p.product_id,
                  stage=StageName.PRODUCT_SEARCH, domains=[t.domain for t in targets])  # fmt: skip
        self.save()
        try:
            intel = await asyncio.wait_for(agent.run_product(p), self.s.pipeline_product_timeout_seconds)
        except TimeoutError:
            self._product_failed(pr, f"Timed out after {self.s.pipeline_product_timeout_seconds:.0f} s")
            return
        except Exception as e:
            log.warning("product %s failed in run %s", p.product_id, self.run.run_id, exc_info=not _is_provider_error(e))
            self._product_failed(pr, f"Product search failed: {_err(e)}")
            return
        pr.intelligence = intel
        self._emit_intelligence(p, intel)
        self.stage(StageName.PRODUCT_SEARCH).done += 1

        summary = evaluate_product(intel, p, self.s.matching_price_max_age_hours, self.d.clock())
        pr.matching = summary
        for m in summary.candidates:
            self.emit("match_validated", f"{m.competitor.domain}: {m.match_status.value}", product_id=p.product_id,
                      stage=StageName.MATCHING, candidate_id=m.candidate_id, domain=m.competitor.domain,
                      url=m.product_url, match_status=m.match_status.value,
                      confidence=m.match_confidence.value,
                      proposed_match=m.proposed_match.value if m.proposed_match else None,
                      proposal_agreement=m.proposal_agreement.value,
                      availability_status=m.availability.status.value if m.availability else None)  # fmt: skip
            self.emit("price_validated", f"{m.competitor.domain}: price {m.price.status.value}",
                      product_id=p.product_id, stage=StageName.MATCHING, candidate_id=m.candidate_id,
                      price_status=m.price.status.value, comparison_eligible=m.comparison.eligible,
                      difference_gbp=str(m.comparison.difference_gbp) if m.comparison.difference_gbp is not None
                      else None, price_kind=m.price_kind.value,
                      alternative_price=str(m.alternative_price.price)
                      if m.alternative_price and m.alternative_price.price is not None else None)  # fmt: skip
        self.stage(StageName.MATCHING).done += 1
        pr.status = ProductRunStatus.COMPLETED
        self._update_summary()
        self.emit("product_completed", f"{p.product_id}: {len(intel.candidates)} candidate(s), "
                                       f"{summary.counts[MatchStatus.EXACT_MATCH]} exact match(es)",
                  product_id=p.product_id, counts={k.value: v for k, v in summary.counts.items()},
                  comparable_prices=summary.comparable_prices,
                  alternative_prices=summary.alternative_prices)  # fmt: skip
        self.save()

    def _emit_intelligence(self, p: StompboxProduct, intel: ProductIntelligence) -> None:
        st = lambda s: sum(c.page.status is s for c in intel.candidates)  # noqa: E731
        failed_domains = [d.domain for d in intel.domains if d.errors]
        self.emit("page_retrieved", f"{p.product_id}: {st(RetrievalStatus.OK)}/{len(intel.candidates)} page(s) "
                                    "retrieved", product_id=p.product_id, stage=StageName.PRODUCT_SEARCH,
                  ok=st(RetrievalStatus.OK), blocked=st(RetrievalStatus.BLOCKED), failed=st(RetrievalStatus.FAILED),
                  empty=st(RetrievalStatus.EMPTY), not_selected=len(intel.not_selected),
                  failed_domains=failed_domains)  # fmt: skip
        if failed_domains:
            self.stage(StageName.PRODUCT_SEARCH).errors.append(
                f"{p.product_id}: search failed on {', '.join(failed_domains)}")
        statuses = [c.extraction_status for c in intel.candidates]
        for c in intel.candidates:
            if c.extraction_status is ExtractionStatus.COMPLETED:
                self.emit("competitor_product_extracted", f"{c.competitor.domain}: extracted",
                          product_id=p.product_id, stage=StageName.EXTRACTION, candidate_id=c.candidate_id,
                          url=c.product_url, price=str(c.price) if c.price is not None else None,
                          currency=c.currency, availability_status=c.availability_status.value,
                          match_proposal_status=c.match_proposal_status.value if c.match_proposal_status else None,
                          proposed_match=c.match_proposal.classification.value if c.match_proposal else None,
                          issues=sum(i.verified for i in c.extraction_issues))  # fmt: skip
        skipped = statuses.count(ExtractionStatus.SKIPPED_NO_LLM)
        if skipped:
            self.emit("extraction_skipped", f"{p.product_id}: {skipped} page(s) SKIPPED_NO_LLM",
                      product_id=p.product_id, stage=StageName.EXTRACTION, pages=skipped)  # fmt: skip
        ext = self.stage(StageName.EXTRACTION)
        ext.done += statuses.count(ExtractionStatus.COMPLETED)
        ext.total += sum(s in (ExtractionStatus.COMPLETED, ExtractionStatus.FAILED) for s in statuses)

    def _product_failed(self, pr: ProductRunResult, message: str) -> None:
        pr.status, pr.error = ProductRunStatus.FAILED, message
        self.stage(StageName.PRODUCT_SEARCH).errors.append(f"{pr.product_id}: {message}")
        self._update_summary()
        self.emit("product_failed", f"{pr.product_id}: {message}", product_id=pr.product_id,
                  stage=StageName.PRODUCT_SEARCH)  # fmt: skip
        self.save()

    def _finish_product_stages(self, agent: ProductIntelligenceAgent, no_llm: bool) -> None:
        done = sum(pr.status is ProductRunStatus.COMPLETED for pr in self.result.products)
        n = len(self.result.products)
        u = agent.usage
        search = self.stage(StageName.PRODUCT_SEARCH)
        if u.search_errors or u.retrieval_errors:
            search.errors.append(f"{u.search_errors} search and {u.retrieval_errors} retrieval error(s)")
        status = (StageStatus.FAILED if done == 0 and n else
                  StageStatus.PARTIAL if done < n or u.search_errors or u.retrieval_errors else StageStatus.COMPLETED)  # fmt: skip
        self.end_stage(StageName.PRODUCT_SEARCH, status,
                       f"{done}/{n} product(s); {u.search_requests} search(es), {u.pages_ok}/{u.pages_requested} "
                       "page(s) retrieved")  # fmt: skip
        if not no_llm:
            ext = self.stage(StageName.EXTRACTION)
            if u.llm_failures - u.llm_match_failures:
                ext.errors.append(f"{u.llm_failures - u.llm_match_failures} page(s) failed extraction")
            if u.llm_match_failures:
                ext.errors.append(f"{u.llm_match_failures} match proposal(s) failed")
            ext_status = (StageStatus.FAILED if u.llm_calls and u.llm_failures >= u.llm_calls else
                          StageStatus.PARTIAL if u.llm_failures else StageStatus.COMPLETED)  # fmt: skip
            self.end_stage(StageName.EXTRACTION, ext_status,
                           f"{u.llm_calls} LLM 2 call(s) ({u.llm_match_calls} match proposal), "
                           f"{u.llm_failures} failed")  # fmt: skip
        self.end_stage(StageName.MATCHING, StageStatus.COMPLETED if done == n else
                       StageStatus.PARTIAL if done else StageStatus.FAILED,
                       f"{done}/{n} product(s) matched")  # fmt: skip

    def _update_summary(self) -> None:
        s = self.run.summary
        prs = self.result.products
        s.products_total = len(prs)
        s.products_completed = sum(p.status is ProductRunStatus.COMPLETED for p in prs)
        s.products_failed = sum(p.status is ProductRunStatus.FAILED for p in prs)
        ms = [p.matching for p in prs if p.matching]
        s.candidates = sum(len(m.candidates) for m in ms)
        s.exact_matches = sum(m.counts[MatchStatus.EXACT_MATCH] for m in ms)
        s.equivalent_variants = sum(m.counts[MatchStatus.EQUIVALENT_VARIANT] for m in ms)
        s.alternative_prices = sum(m.alternative_prices for m in ms)
        s.similar_matches = sum(m.counts[MatchStatus.SIMILAR_MATCH] for m in ms)
        s.review_required = sum(m.counts[MatchStatus.REVIEW_REQUIRED] for m in ms)
        s.no_match = sum(m.counts[MatchStatus.NO_MATCH] for m in ms)
        s.comparable_prices = sum(m.comparable_prices for m in ms)

    # ------------------------------------------------------------------ run

    async def execute(self) -> None:
        run = self.run
        run.status, run.started_at = RunStatus.RUNNING, self.d.clock()
        self.emit("run_started", f"Run started for {len(self.products)} product(s)",
                  product_ids=[p.product_id for p in self.products], run_discovery=run.request.run_discovery)  # fmt: skip
        val = self.start_stage(StageName.VALIDATE, total=len(self.products))
        for p in self.products:
            self.emit("product_loaded", f"{p.product_id} {p.product_name}", product_id=p.product_id,
                      stage=StageName.VALIDATE, brand=p.brand, model=p.manufacturer_model,
                      stompbox_price=str(p.displayed_price) if p.displayed_price is not None else None)  # fmt: skip
        val.done = len(self.products)
        self.end_stage(StageName.VALIDATE, StageStatus.COMPLETED, f"{len(self.products)} product(s) validated")
        try:
            async with asyncio.timeout(self.s.pipeline_run_timeout_seconds):
                await self._discovery()
                targets = self._targets()
                await self._products(targets)
            self._finalize()
        except asyncio.CancelledError:
            if (task := asyncio.current_task()) is not None:
                task.uncancel()
            self._stop(RunStatus.CANCELLED, StageStatus.CANCELLED, "Cancelled by request")
        except TimeoutError:
            msg = f"Run timed out after {self.s.pipeline_run_timeout_seconds:.0f} s"
            done = any(pr.status is ProductRunStatus.COMPLETED for pr in self.result.products)
            self._stop(RunStatus.PARTIALLY_COMPLETED if done else RunStatus.FAILED, StageStatus.FAILED, msg)
        except Exception as e:  # a bug, not a provider failure: keep partial results, report safely
            log.exception("run %s failed", run.run_id)
            done = any(pr.status is ProductRunStatus.COMPLETED for pr in self.result.products)
            self._stop(RunStatus.PARTIALLY_COMPLETED if done else RunStatus.FAILED, StageStatus.FAILED,
                       f"Unexpected error: {type(e).__name__}")  # fmt: skip

    def _stop(self, status: RunStatus, stage_status: StageStatus, message: str) -> None:
        for st in self.run.stages:
            if st.status in (StageStatus.RUNNING, StageStatus.PENDING):
                st.status = stage_status if st.status is StageStatus.RUNNING else (
                    StageStatus.CANCELLED if status is RunStatus.CANCELLED else StageStatus.SKIPPED)
                st.finished_at = self.d.clock()
                st.message = message
        for pr in self.result.products:
            if pr.status in (ProductRunStatus.PENDING, ProductRunStatus.RUNNING):
                pr.status = ProductRunStatus.CANCELLED if status is RunStatus.CANCELLED else ProductRunStatus.FAILED
                pr.error = message
        self._update_summary()
        if status is not RunStatus.CANCELLED:
            self.run.error = message
        self._terminal(status, message)

    def _finalize(self) -> None:
        self._update_summary()
        prs = self.result.products
        completed = sum(p.status is ProductRunStatus.COMPLETED for p in prs)
        degraded = any(st.status in (StageStatus.FAILED, StageStatus.PARTIAL) for st in self.run.stages)
        if completed == 0:
            self.run.error = "No product completed"
            status = RunStatus.FAILED
        elif degraded or completed < len(prs):
            status = RunStatus.PARTIALLY_COMPLETED
        else:
            status = RunStatus.COMPLETED
        skipped = [st.name.value for st in self.run.stages if st.status is StageStatus.SKIPPED]
        msg = f"{completed}/{len(prs)} product(s) completed" + (f"; skipped stages: {', '.join(skipped)}" if skipped
                                                                 else "")  # fmt: skip
        self._terminal(status, msg)

    def _terminal(self, status: RunStatus, message: str) -> None:
        self.run.status, self.run.finished_at = status, self.d.clock()
        kind = {RunStatus.CANCELLED: "run_cancelled", RunStatus.FAILED: "run_failed"}.get(status, "run_completed")
        self.emit(kind, message, status=status.value, summary=self.run.summary.model_dump(),
                  error=self.run.error)  # fmt: skip
        self.save()


def _is_provider_error(e: BaseException) -> bool:
    from app.services.search.http import ProviderError

    return isinstance(e, ProviderError)


# --------------------------------------------------------------------------- manager


class RunManager:
    """Validates and starts runs as background tasks; supports cancellation."""

    def __init__(self, store: RunStore, deps: PipelineDeps):
        self.store, self.d = store, deps
        self._tasks: dict[str, asyncio.Task] = {}

    def validate(self, req: RunRequest) -> tuple[list[StompboxProduct], RunRequest]:
        s = self.d.settings
        ids = list(dict.fromkeys(i.strip().upper() for i in req.product_ids))
        if len(ids) > s.pipeline_max_products_per_run:
            raise RunRequestError(422, f"At most {s.pipeline_max_products_per_run} products per run")
        products, missing = self.d.catalogue.lookup(ids)
        if missing:
            raise RunRequestError(404, {"message": "Unknown product IDs", "missing_ids": missing})
        if s.search_provider is None or s.search_provider not in configured_providers(s):
            raise RunRequestError(503, "Search is not configured: set SEARCH_PROVIDER and its API key")
        if req.domains is not None:
            try:
                wanted = {registrable_domain(d) for d in req.domains}
            except ValueError:
                raise RunRequestError(422, "Invalid domain in 'domains'") from None
            # Stored automatically eligible domains count (this run's discoveries are not known yet).
            targets, _ = eligible_search_targets(self.d.catalogue.competitors,
                                                 self.d.review_store.list_decisions(), s,
                                                 candidates=[c.candidate for c in self.d.review_store.list_candidates()],
                                                 product_ids=ids, only_domains=wanted)  # fmt: skip
            not_allowed = sorted(wanted - {t.domain for t in targets})
            if not_allowed:
                raise RunRequestError(422, {"message": "Only configured, reviewer-approved or automatically "
                                                       "search-eligible domains can be searched",
                                            "not_allowed": not_allowed})  # fmt: skip
        active = sum(r.status not in TERMINAL for r in self.store.list_runs())
        if active >= s.pipeline_max_active_runs:
            raise RunRequestError(429, f"{active} run(s) already active (limit {s.pipeline_max_active_runs})")
        return products, req.model_copy(update={"product_ids": ids})

    def start(self, req: RunRequest) -> ExecutionRun:
        products, req = self.validate(req)
        now = self.d.clock()
        self.store.prune(now)
        run = ExecutionRun(run_id=f"run-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}", request=req,
                           created_at=now, stages=[StageState(name=n) for n in StageName])  # fmt: skip
        result = PipelineResult(products=[ProductRunResult(product_id=p.product_id) for p in products])
        self.store.create(run, result)
        pipeline = PipelineRun(run, result, products, self.store, self.d)
        task = asyncio.create_task(pipeline.execute(), name=run.run_id)
        self._tasks[run.run_id] = task
        task.add_done_callback(lambda t, rid=run.run_id: self._tasks.pop(rid, None))
        return run

    def cancel(self, run_id: str) -> ExecutionRun:
        run = self.store.get(run_id)
        if run is None:
            raise RunRequestError(404, f"Run {run_id} not found")
        if run.status in TERMINAL:
            raise RunRequestError(409, f"Run {run_id} is already {run.status.value}")
        run.cancel_requested = True
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
        return run

    async def wait(self, run_id: str) -> None:
        """Wait for a run's task to finish (tests, shutdown)."""
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


# ---------------------------------------------------------------------- domain wording


def _queue_breakdown(candidates, pending: list[str]) -> str:
    """'2 pending approval (not searched until approved), 1 automatically eligible, ...' (non-zero parts)."""
    auto = sum(c.approval_status is DomainApprovalStatus.AUTO_APPROVED for c in candidates)
    approved = sum(c.approval_status is DomainApprovalStatus.APPROVED for c in candidates)
    rejected = sum(c.approval_status is DomainApprovalStatus.REJECTED for c in candidates)
    parts = [f"{len(pending)} pending approval (not searched until approved)"]
    if auto:
        parts.append(f"{auto} automatically eligible")
    if approved:
        parts.append(f"{approved} already reviewer-approved")
    if rejected:
        parts.append(f"{rejected} rejected (never searched)")
    return ", ".join(parts) + ". The targets stage decides which are searched in this run"


_OUTCOME_WORDS = {
    DomainRunStatus.SELECTED_FOR_SEARCH: "selected for search",
    DomainRunStatus.PENDING_APPROVAL: "pending approval (not searched)",
    DomainRunStatus.AUTO_ELIGIBLE_NOT_SEARCHED: "automatically eligible but not searched",
    DomainRunStatus.APPROVED_NOT_SEARCHED: "reviewer-approved but not searched",
    DomainRunStatus.REJECTED: "rejected (never searched)",
}


def _outcome_breakdown(outcomes: list[DomainRunOutcome]) -> str:
    counts = {st: sum(o.status is st for o in outcomes) for st in DomainRunStatus}
    return ", ".join(f"{n} {_OUTCOME_WORDS[st]}" for st, n in counts.items() if n)


def domain_outcomes(
    candidates, targets: list[SearchTarget], skipped: list[SkippedTarget], *, auto_search_enabled: bool,
    domain_subset: set[str] | None,
) -> list[DomainRunOutcome]:  # fmt: skip
    """Where each domain discovered in this run stands, from the run's actual target list.

    Being approved or automatically eligible never counts as selected: only membership of
    `targets` does. Reasons come from the target builder's skipped list or from the run's
    settings and request; nothing is guessed (an unknown reason stays null)."""
    target_domains = {t.domain for t in targets}
    skipped_why = {s.domain: s.reason for s in skipped}
    outside = "Outside the domains selected for this run"
    out: list[DomainRunOutcome] = []
    for c in candidates:
        st, reason = c.approval_status, None
        decided = c.review_decision.decision if c.review_decision is not None else None
        in_subset = domain_subset is None or c.domain in domain_subset
        if st is DomainApprovalStatus.REJECTED or decided is ReviewDecision.REJECTED:
            status = DomainRunStatus.REJECTED
            reason = skipped_why.get(c.domain) or ("Rejected in review" if decided else
                                                   "Rejected automatically: no discovery evidence")  # fmt: skip
        elif c.domain in target_domains:
            status = DomainRunStatus.SELECTED_FOR_SEARCH
        elif st is DomainApprovalStatus.APPROVED or decided is ReviewDecision.APPROVED:
            status = DomainRunStatus.APPROVED_NOT_SEARCHED
            reason = skipped_why.get(c.domain) or (None if in_subset else outside)
        elif st is DomainApprovalStatus.AUTO_APPROVED:
            status = DomainRunStatus.AUTO_ELIGIBLE_NOT_SEARCHED
            if not auto_search_enabled:
                reason = "Automatic search is switched off (PIPELINE_SEARCH_AUTO_APPROVED=false)"
            else:
                reason = skipped_why.get(c.domain) or (None if in_subset else outside)
        else:
            status = DomainRunStatus.PENDING_APPROVAL
            reason = "Needs a reviewer decision on Domain Review before it can be searched"
        out.append(DomainRunOutcome(domain=c.domain, status=status, reason=reason))
    return out
