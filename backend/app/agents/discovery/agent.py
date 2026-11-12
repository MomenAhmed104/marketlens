"""LLM 1: Competitor Discovery Agent.

Flow for a set of selected products:
  1. Known competitors come from the catalogue workbook (never re-decided here).
  2. LLM 1 plans global discovery queries and targeted query variants; a
     deterministic baseline query is always added.
  3. The search provider (Exa by default) runs the global queries.
  4. Results are grouped by registrable domain. Own site, known competitors and
     non-shop platforms are separated out deterministically.
  5. Each new candidate gets a domain-restricted "profile" search for more
     evidence about the business.
  6. LLM 1 classifies candidates from that evidence only.
  7. Python validates the classification against the evidence and decides:
     AUTO_APPROVED, APPROVAL_REQUIRED or REJECTED.
  8. Persisted reviewer decisions (Phase 4) override step 7: REJECTED domains
     are never searched; APPROVED ones are searched even if not rediscovered.
  9. Targeted queries are generated for search targets only: preconfigured
     competitors, reviewer-approved domains and (this run only) auto-approved
     ones. Pending and rejected domains never get targeted queries.

Two modes:
- LLM_ENABLED: steps 2 and 6 use LLM 1 (OpenRouter).
- LLM_DISABLED (no LLM client): step 2 uses the deterministic baseline query
  only and step 6 is skipped. Every new candidate is then APPROVAL_REQUIRED
  with its evidence and UK signals recorded, and no company name, type,
  relevance, confidence or UK-selling claim is filled in.

Run state is returned, not persisted (temporary execution state).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from app.agents.discovery import prompts
from app.agents.discovery.rules import Thresholds, apply_classification, apply_review_decision, decide, uk_signals
from app.agents.discovery.schemas import (
    ClassificationBatch,
    ClassificationStatus,
    DiscoveredCompetitor,
    DiscoveryMode,
    DiscoveryPlan,
    DiscoveryRunResult,
    DiscoveryThresholds,
    DiscoveryUsage,
    EvidenceItem,
    EvidenceKind,
    ExcludedDomain,
    KnownCompetitorEvidence,
    PlannedQuery,
    QuerySource,
    SearchTarget,
    TargetedQuery,
)
from app.core.config import Settings
from app.schemas.catalogue import StompboxProduct
from app.schemas.common import DomainApprovalStatus
from app.schemas.competitor import CompetitorDomain
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.schemas.search import SearchQuery, SearchResponse, SearchScope
from app.services.domains import registrable_domain
from app.services.llm import LLMClient, LLMResult
from app.services.search import SearchProvider
from app.services.search.http import ProviderError
from app.services.text_match import mentions_model, url_text

MAX_QUERY_CHARS = 200
PROFILE_QUERY = "music shop about us contact delivery returns"
APPROVED_FOR_SEARCH = (DomainApprovalStatus.PRECONFIGURED, DomainApprovalStatus.AUTO_APPROVED,
                       DomainApprovalStatus.APPROVED)  # fmt: skip


def _now() -> datetime:
    return datetime.now(UTC)


def baseline_global_query(p: StompboxProduct) -> str:
    return f"{p.brand} {p.manufacturer_model} buy UK"


def baseline_targeted_query(p: StompboxProduct) -> str:
    return f"{p.brand} {p.manufacturer_model}"


def _clean_query(q: str) -> str | None:
    q = " ".join(q.split())
    if not q or len(q) > MAX_QUERY_CHARS or "site:" in q.lower():
        return None
    return q


class CompetitorDiscoveryAgent:
    def __init__(
        self,
        search: SearchProvider,
        llm: LLMClient | None,
        settings: Settings,
        known_competitors: list[CompetitorDomain],
        allowed_categories: list[str],
        llm_disabled_reason: str | None = None,
        review_decisions: list[DomainDecisionRecord] | None = None,
    ):
        """`llm=None` runs in LLM_DISABLED mode; pass `llm_disabled_reason` to say why.

        `review_decisions` are persisted reviewer decisions (Phase 4). Decisions on
        preconfigured competitors are ignored: those stay exactly as configured.
        """
        self.search = search
        self.llm = llm
        self.mode = DiscoveryMode.LLM_ENABLED if llm else DiscoveryMode.LLM_DISABLED
        self.llm_disabled_reason = None if llm else (llm_disabled_reason or "No LLM client provided")
        self.s = settings
        self.known = known_competitors
        known_domains = {registrable_domain(c.domain) for c in known_competitors}
        self.review_decisions = {d.domain: d for d in review_decisions or [] if d.domain not in known_domains}
        self.allowed_categories = sorted(set(allowed_categories))
        self.thresholds = Thresholds(
            settings.discovery_auto_approve_min_confidence,
            settings.discovery_auto_approve_min_relevance,
            settings.discovery_auto_approve_min_evidence_urls,
            settings.discovery_reject_max_relevance,
        )
        self.usage = DiscoveryUsage(llm_model=llm.model if llm else None)
        self.warnings: list[str] = []
        self._sem = asyncio.Semaphore(settings.discovery_search_concurrency)

    async def aclose(self) -> None:
        await self.search.aclose()
        if self.llm:
            await self.llm.aclose()

    # ------------------------------------------------------------------ helpers

    def _record_llm(self, result: LLMResult) -> None:
        self.usage.llm_calls += len(result.request_ids)
        self.usage.llm_model = result.model
        self.usage.llm_prompt_tokens += result.usage.prompt_tokens
        self.usage.llm_completion_tokens += result.usage.completion_tokens
        if result.usage.cost_usd is not None:
            self.usage.llm_cost_usd = (self.usage.llm_cost_usd or Decimal(0)) + result.usage.cost_usd

    async def _search(self, query: SearchQuery) -> SearchResponse | None:
        async with self._sem:
            self.usage.search_requests += 1
            try:
                resp = await self.search.search(query)
            except ProviderError as e:
                self.usage.search_errors += 1
                self.warnings.append(f"Search failed for {query.text!r}: {e}")
                return None
        if resp.cost.usd is not None:
            self.usage.search_cost_usd += resp.cost.usd
        return resp

    def _evidence(self, resp: SearchResponse, kind: EvidenceKind, product: StompboxProduct | None) -> list[EvidenceItem]:
        items = []
        for r in resp.results:
            items.append(EvidenceItem(
                url=str(r.url), canonical_url=r.canonical_url, hostname=r.hostname, title=r.title,
                snippet=(r.snippet or "")[:500] or None, kind=kind, query_text=resp.query.text,
                product_id=product.product_id if product else None, provider=resp.provider,
                provider_request_id=resp.request_id, retrieved_at=resp.retrieved_at,
                mentions_product_model=bool(product and mentions_model(
                    product.manufacturer_model, r.title, url_text(str(r.url)))),
                mentions_brand=bool(product and mentions_model(product.brand, r.title, url_text(str(r.url)))),
            ))  # fmt: skip
        return items

    # ------------------------------------------------------------------ steps

    async def plan(self, products: list[StompboxProduct]) -> tuple[list[PlannedQuery], dict[str, list[str]]]:
        """Global queries per product plus validated targeted variants (by product id)."""
        max_global = self.s.discovery_max_global_queries_per_product
        max_targeted = self.s.discovery_max_targeted_variants
        llm_plan: dict[str, tuple[list[str], list[str]]] = {}
        if self.llm is None:
            self.warnings.append(
                f"LLM query planning skipped ({self.llm_disabled_reason}); using deterministic baseline queries only")
        else:
            try:
                result = await self.llm.complete_structured(
                    system=prompts.PLAN_SYSTEM.format(max_global=max_global, max_targeted=max_targeted),
                    user=prompts.plan_user(products, self.known),
                    output_model=DiscoveryPlan, schema_name="discovery_plan",
                )  # fmt: skip
                self._record_llm(result)
                for item in result.value.products:
                    llm_plan[item.product_id] = (item.global_queries, item.targeted_query_variants)
            except ProviderError as e:
                self.warnings.append(f"Planning LLM call failed; using baseline queries only: {e}")

        unknown = set(llm_plan) - {p.product_id for p in products}
        if unknown:
            self.warnings.append(f"LLM plan referenced unknown product ids {sorted(unknown)}; ignored")

        planned: list[PlannedQuery] = []
        variants: dict[str, list[str]] = {}
        for p in products:
            g_llm, t_llm = llm_plan.get(p.product_id, ([], []))
            texts = [(baseline_global_query(p), QuerySource.BASELINE)]
            texts += [(q, QuerySource.LLM) for q in g_llm]
            seen: set[str] = set()
            for text, source in texts:
                q = _clean_query(text)
                if not q or q.casefold() in seen or len(seen) >= max_global + 1:
                    continue
                seen.add(q.casefold())
                planned.append(PlannedQuery(product_id=p.product_id, source=source, query=SearchQuery(
                    text=q, product_id=p.product_id, max_results=self.s.discovery_results_per_query)))  # fmt: skip
            # Targeted variants must name the exact model; others are dropped.
            vs = [baseline_targeted_query(p)]
            for v in t_llm:
                q = _clean_query(v)
                if not q:
                    continue
                if not mentions_model(p.manufacturer_model, q):
                    self.warnings.append(f"{p.product_id}: dropped targeted variant without the model: {q!r}")
                    continue
                if q.casefold() not in {x.casefold() for x in vs}:
                    vs.append(q)
            variants[p.product_id] = vs[: 1 + max_targeted]
        return planned, variants

    def group(
        self, evidence: list[EvidenceItem]
    ) -> tuple[dict[str, list[EvidenceItem]], dict[str, list[EvidenceItem]], list[ExcludedDomain]]:
        """Split evidence into candidates, known-competitor hits and exclusions."""
        own = {registrable_domain(d) for d in self.s.own_domains}
        known = {registrable_domain(c.domain): c for c in self.known}
        blocked = {registrable_domain(d) for d in self.s.discovery_excluded_domains}
        by_domain: dict[str, list[EvidenceItem]] = defaultdict(list)
        for e in evidence:
            by_domain[registrable_domain(e.hostname)].append(e)
        candidates, known_hits, excluded = {}, {}, []
        for domain, items in by_domain.items():
            if domain in own:
                excluded.append(self._excluded(domain, "Own site (Stompbox)", items))
            elif domain in known:
                known_hits[domain] = items
            elif domain in blocked:
                excluded.append(self._excluded(domain, "Not a shop: excluded platform", items))
            else:
                candidates[domain] = items
        return candidates, known_hits, excluded

    @staticmethod
    def _excluded(domain: str, reason: str, items: list[EvidenceItem]) -> ExcludedDomain:
        return ExcludedDomain(domain=domain, reason=reason, evidence_count=len(items),
                              sample_urls=[e.url for e in items[:3]])  # fmt: skip

    def _rank_key(self, items: list[EvidenceItem]) -> tuple:
        products = {e.product_id for e in items if e.mentions_product_model}
        return (-len(products), -sum(e.mentions_product_model for e in items), -len(items))

    async def classify(self, candidates: list[DiscoveredCompetitor], products: list[StompboxProduct]) -> None:
        if self.llm is None:
            for c in candidates:
                c.classification_status = ClassificationStatus.SKIPPED_LLM_DISABLED
            if candidates:
                self.warnings.append(
                    f"LLM classification skipped ({self.llm_disabled_reason}): {len(candidates)} new "
                    "candidate(s) recorded with evidence and marked APPROVAL_REQUIRED")
            return
        size = self.s.discovery_classification_batch_size
        for i in range(0, len(candidates), size):
            batch = candidates[i: i + size]
            try:
                result = await self.llm.complete_structured(
                    system=prompts.CLASSIFY_SYSTEM,
                    user=prompts.classify_user(batch, products, self.allowed_categories),
                    output_model=ClassificationBatch, schema_name="company_classification",
                )  # fmt: skip
            except ProviderError as e:
                self.warnings.append(f"Classification failed for {[c.domain for c in batch]}: {e}")
                for c in batch:
                    c.classification_status = ClassificationStatus.FAILED
                continue
            self._record_llm(result)
            by_domain = {c.domain: c for c in batch}
            for cls in result.value.companies:
                target = by_domain.pop(cls.domain.strip().lower(), None)
                if target is None:
                    self.warnings.append(f"LLM classified a domain it was not given: {cls.domain!r}; ignored")
                    continue
                apply_classification(target, cls, set(self.allowed_categories))
            for missing in by_domain:
                self.warnings.append(f"LLM returned no classification for {missing}")

    def targeted_queries(
        self, products: list[StompboxProduct], variants: dict[str, list[str]], approved: list[SearchTarget]
    ) -> list[TargetedQuery]:
        out = []
        for c in approved:
            if c.approval_status not in APPROVED_FOR_SEARCH:
                continue
            for p in products:
                for i, text in enumerate(variants.get(p.product_id, [baseline_targeted_query(p)])):
                    out.append(TargetedQuery(
                        product_id=p.product_id, domain=c.domain, company_type=c.company_type,
                        approval_status=c.approval_status, price_benchmark=c.price_benchmark,
                        source=QuerySource.BASELINE if i == 0 else QuerySource.LLM,
                        query=SearchQuery(text=text, product_id=p.product_id, scope=SearchScope.DOMAIN,
                                          domains=(c.domain,), max_results=5),
                    ))  # fmt: skip
        return out

    def search_targets(self, candidates: list[DiscoveredCompetitor]) -> list[SearchTarget]:
        """Domains allowed into targeted searches. Pending and rejected never are."""
        targets = [SearchTarget.from_known(c) for c in self.known]
        seen = {t.domain for t in targets}
        for c in candidates:
            if c.approval_status in (DomainApprovalStatus.AUTO_APPROVED, DomainApprovalStatus.APPROVED):
                targets.append(c.as_search_target())
                seen.add(c.domain)
        # Reviewer-approved domains stay searchable even when this run did not rediscover them.
        for d in sorted(self.review_decisions.values(), key=lambda d: d.domain):
            if d.decision is ReviewDecision.APPROVED and d.domain not in seen:
                targets.append(SearchTarget.from_decision(d))
                seen.add(d.domain)
        return targets

    # ------------------------------------------------------------------ run

    async def run(self, products: list[StompboxProduct]) -> DiscoveryRunResult:
        if not products:
            raise ValueError("Select at least one product")
        started = _now()
        planned, variants = await self.plan(products)

        by_id = {p.product_id: p for p in products}
        responses = await asyncio.gather(*(self._search(pq.query) for pq in planned))
        evidence: list[EvidenceItem] = []
        seen: set[tuple[str, str | None]] = set()
        for pq, resp in zip(planned, responses):
            if resp is None:
                continue
            for e in self._evidence(resp, EvidenceKind.DISCOVERY_RESULT, by_id[pq.product_id]):
                key = (e.canonical_url, e.product_id)  # same page from several queries: keep the first
                if key not in seen:
                    seen.add(key)
                    evidence.append(e)

        cand_items, known_hits, excluded = self.group(evidence)
        ranked = sorted(cand_items.items(), key=lambda kv: (self._rank_key(kv[1]), kv[0]))
        selected = ranked[: self.s.discovery_max_candidates]
        for domain, items in ranked[self.s.discovery_max_candidates:]:
            excluded.append(self._excluded(domain, "Not evaluated: below the candidate limit for this run", items))

        candidates: list[DiscoveredCompetitor] = []
        for domain, items in selected:
            hostnames = sorted({e.hostname for e in items})
            candidates.append(DiscoveredCompetitor(
                domain=domain, hostnames=hostnames, homepage_url=f"https://{hostnames[0]}/",
                evidence=items, discovered_at=_now(),
                products_matched=sorted({e.product_id for e in items if e.mentions_product_model and e.product_id}),
                uk_signals=[],
            ))  # fmt: skip

        if self.s.discovery_profile_results:
            profiles = await asyncio.gather(*(self._search(SearchQuery(
                text=PROFILE_QUERY, scope=SearchScope.DOMAIN, domains=(c.domain,),
                max_results=self.s.discovery_profile_results)) for c in candidates))  # fmt: skip
            for c, resp in zip(candidates, profiles):
                if resp is not None:
                    c.evidence += self._evidence(resp, EvidenceKind.DOMAIN_PROFILE, None)
        for c in candidates:
            c.uk_signals = uk_signals(c.domain, c.evidence)

        await self.classify(candidates, products)
        for c in candidates:
            decide(c, self.thresholds, self.s.marketplace_domain_labels)
            if decision := self.review_decisions.get(c.domain):
                apply_review_decision(c, decision)

        known_evidence = [
            KnownCompetitorEvidence(
                domain=d, evidence=items,
                products_seen=sorted({e.product_id for e in items if e.mentions_product_model and e.product_id}),
            )
            for d, items in sorted(known_hits.items())
        ]  # fmt: skip
        targets = self.search_targets(candidates)
        status_order = {DomainApprovalStatus.APPROVED: 0, DomainApprovalStatus.AUTO_APPROVED: 0,
                        DomainApprovalStatus.APPROVAL_REQUIRED: 1, DomainApprovalStatus.REJECTED: 2}  # fmt: skip
        candidates.sort(key=lambda c: (status_order.get(c.approval_status, 3), -(c.confidence or 0), c.domain))
        return DiscoveryRunResult(
            run_id=f"discovery-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}",
            mode=self.mode, llm_disabled_reason=self.llm_disabled_reason,
            started_at=started, finished_at=_now(), product_ids=[p.product_id for p in products],
            search_provider=self.search.name, known_competitors=self.known,
            known_competitor_evidence=known_evidence, global_queries=planned, candidates=candidates,
            excluded=sorted(excluded, key=lambda x: x.domain),
            search_targets=targets, targeted_queries=self.targeted_queries(products, variants, targets),
            thresholds=DiscoveryThresholds(**self.thresholds.__dict__), usage=self.usage,
            warnings=self.warnings,
        )  # fmt: skip
