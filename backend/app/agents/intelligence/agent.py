"""LLM 2: Product Intelligence Agent.

For each selected Stompbox product:
  1. Build search texts from brand, model, name (variant details) and, when
     present, the first specification segment.
  2. Search each approved search target, restricted to that domain (Exa by
     default). Scope is enforced again in Python by the provider layer.
  3. Select candidate pages deterministically: results whose title or URL names
     the model first, then the brand; others are recorded as not selected.
     Caps per domain and per product bound cost.
  4. Retrieve page text through the provider (one batched call per product),
     fetching the canonical URL (UK storefront for locale-prefixed URLs).
  5. LLM 2 extracts the page's product fields with verbatim quotes.
  6. Python checks every quote against the page text and the price against
     sale/conditional context (`rules.check_extraction`).

Two modes, as in discovery:
- LLM_ENABLED: steps 1-6.
- LLM_DISABLED (no LLM client): steps 1-4 only. Every candidate is
  SKIPPED_NO_LLM and no product field is filled in.

Results are returned, not persisted (temporary execution state). Nothing here
decides whether a page is the same product or compares prices (Phase 6).
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.agents.discovery.schemas import SearchTarget
from app.agents.intelligence import prompts
from app.agents.intelligence.rules import check_extraction, product_queries
from app.agents.intelligence.schemas import (
    CompetitorProduct,
    DomainSearchSummary,
    ExtractedField,
    ExtractionStatus,
    IntelligenceMode,
    IntelligenceRunResult,
    IntelligenceUsage,
    PageExtraction,
    PageRecord,
    ProductIntelligence,
    ProductSummary,
    SearchHit,
    SkippedHit,
    SkippedTarget,
)
from app.core.config import Settings
from app.schemas.catalogue import StompboxProduct
from app.schemas.search import RetrievalStatus, RetrievedPage, SearchQuery, SearchResponse, SearchScope
from app.services.llm import LLMClient
from app.services.search import SearchProvider
from app.services.search.http import ProviderAuthError, ProviderError
from app.services.text_match import mentions_model, url_text

MAX_ERROR_CHARS = 300
# 401/403 auth, 402 insufficient credits: every further call would fail the same way.
ACCOUNT_ERROR_STATUS = {401, 402, 403}


def _now() -> datetime:
    return datetime.now(UTC)


def _err(e: Exception) -> str:
    return str(e)[:MAX_ERROR_CHARS]


class ProductIntelligenceAgent:
    def __init__(
        self,
        search: SearchProvider,
        llm: LLMClient | None,
        settings: Settings,
        targets: list[SearchTarget],
        skipped_targets: list[SkippedTarget] | None = None,
        llm_disabled_reason: str | None = None,
    ):
        """`targets` must already be filtered to approved, searchable domains
        (see `eligible_search_targets`). `llm=None` runs in LLM_DISABLED mode."""
        self.search = search
        self.llm = llm
        self.mode = IntelligenceMode.LLM_ENABLED if llm else IntelligenceMode.LLM_DISABLED
        self.llm_disabled_reason = None if llm else (llm_disabled_reason or "No LLM client provided")
        self.s = settings
        self.targets = targets
        self.skipped_targets = skipped_targets or []
        self.usage = IntelligenceUsage(llm_model=llm.model if llm else None)
        self.warnings: list[str] = []
        self._search_sem = asyncio.Semaphore(settings.intelligence_search_concurrency)
        self._llm_sem = asyncio.Semaphore(settings.intelligence_llm_concurrency)
        self._llm_stopped: str | None = None

    async def aclose(self) -> None:
        await self.search.aclose()
        if self.llm:
            await self.llm.aclose()

    # ------------------------------------------------------------------ search

    async def _search(self, query: SearchQuery) -> SearchResponse | ProviderError:
        async with self._search_sem:
            self.usage.search_requests += 1
            try:
                resp = await self.search.search(query)
            except ProviderError as e:
                self.usage.search_errors += 1
                return e
        if resp.cost.usd is not None:
            self.usage.search_cost_usd += resp.cost.usd
        return resp

    async def _search_product(
        self, p: StompboxProduct, queries: list[str]
    ) -> tuple[list[DomainSearchSummary], list[SearchHit]]:
        jobs = [(t, q) for t in self.targets for q in queries]
        responses = await asyncio.gather(*(self._search(SearchQuery(
            text=q, scope=SearchScope.DOMAIN, domains=(t.domain,), product_id=p.product_id,
            max_results=self.s.intelligence_results_per_query)) for t, q in jobs))  # fmt: skip
        summaries = {t.domain: DomainSearchSummary(domain=t.domain, queries=queries) for t in self.targets}
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for (t, q), resp in zip(jobs, responses):
            summary = summaries[t.domain]
            if isinstance(resp, ProviderError):
                summary.errors.append(_err(resp))
                self.warnings.append(f"{p.product_id}: search failed on {t.domain} for {q!r}: {_err(resp)}")
                continue
            summary.results += len(resp.results)
            summary.out_of_scope_dropped += resp.out_of_scope_dropped
            for r in resp.results:
                if r.canonical_url in seen:  # same page from both queries: keep the first
                    continue
                seen.add(r.canonical_url)
                path = url_text(str(r.url))
                hits.append(SearchHit(
                    domain=t.domain, query_text=q, provider=resp.provider, provider_request_id=resp.request_id,
                    rank=r.rank, url=str(r.url), canonical_url=r.canonical_url, hostname=r.hostname,
                    title=r.title, snippet=r.snippet, published_raw=r.published_raw,
                    retrieved_at=resp.retrieved_at,
                    mentions_model=mentions_model(p.manufacturer_model, r.title, path),
                    mentions_brand=mentions_model(p.brand, r.title, path, r.snippet),
                ))  # fmt: skip
        return list(summaries.values()), hits

    def _select(self, hits: list[SearchHit], summaries: list[DomainSearchSummary]) -> tuple[list[SearchHit], list[SkippedHit]]:
        """Deterministic, cost-bounding page selection. Not an identity decision."""
        per_domain = self.s.intelligence_max_pages_per_domain
        selected: list[SearchHit] = []
        skipped: list[SkippedHit] = []
        count: dict[str, int] = {}
        ordered = sorted(hits, key=lambda h: (not h.mentions_model, not h.mentions_brand, h.rank, h.query_text))
        for h in ordered:
            reason = None
            if not (h.mentions_model or h.mentions_brand):
                reason = "Neither brand nor model in the result title/URL"
            elif count.get(h.domain, 0) >= per_domain:
                reason = f"Over the per-domain page limit ({per_domain})"
            elif len(selected) >= self.s.intelligence_max_pages_per_product:
                reason = f"Over the per-product page limit ({self.s.intelligence_max_pages_per_product})"
            if reason:
                skipped.append(SkippedHit(domain=h.domain, url=h.url, title=h.title, reason=reason))
                continue
            count[h.domain] = count.get(h.domain, 0) + 1
            selected.append(h)
        by_domain = {s.domain: s for s in summaries}
        for d, n in count.items():
            by_domain[d].pages_selected = n
        return selected, skipped

    # ------------------------------------------------------------------ retrieval

    async def _retrieve(self, urls: list[str]) -> dict[str, tuple[RetrievedPage | None, str | None, str | None]]:
        """url -> (page, provider request id, error). page None = not fetched."""
        out: dict[str, tuple[RetrievedPage | None, str | None, str | None]] = {}
        if not urls:
            return out
        if not self.search.supports_retrieval:
            msg = f"Provider {self.search.name} has no content retrieval"
            self.warnings.append(msg)
            return {u: (None, None, msg) for u in urls}
        size = self.s.intelligence_retrieval_batch_size
        for i in range(0, len(urls), size):
            batch = urls[i: i + size]
            self.usage.retrieval_requests += 1
            self.usage.pages_requested += len(batch)
            try:
                resp = await self.search.retrieve(batch)
            except ProviderError as e:
                self.usage.retrieval_errors += 1
                self.warnings.append(f"Retrieval failed for {len(batch)} page(s): {_err(e)}")
                for u in batch:
                    out[u] = (None, None, _err(e))
                continue
            if resp.cost.usd is not None:
                self.usage.retrieval_cost_usd += resp.cost.usd
            by_url = {pg.requested_url: pg for pg in resp.pages}
            for u in batch:
                pg = by_url.get(u)
                out[u] = (pg, resp.request_id, None if pg else "page missing from the provider response")
                if pg and pg.status is RetrievalStatus.OK:
                    self.usage.pages_ok += 1
        return out

    # ------------------------------------------------------------------ extraction

    async def _extract(self, cand: CompetitorProduct, product: StompboxProduct, page: RetrievedPage) -> None:
        text = (page.content or "")[: self.s.intelligence_page_chars]
        async with self._llm_sem:
            if self._llm_stopped:  # an earlier call hit an account-level error: don't repeat it
                self.usage.llm_failures += 1
                cand.extraction_status = ExtractionStatus.FAILED
                cand.extraction_error = f"Not attempted: {self._llm_stopped}"
                return
            cand.page.chars_sent_to_llm = len(text)
            self.usage.llm_calls += 1
            try:
                result = await self.llm.complete_structured(
                    system=prompts.EXTRACT_SYSTEM,
                    user=prompts.extract_user(product, cand.competitor, cand.product_url, page.title, text),
                    output_model=PageExtraction, schema_name="page_extraction",
                )  # fmt: skip
            except ProviderError as e:
                self.usage.llm_failures += 1
                cand.extraction_status = ExtractionStatus.FAILED
                cand.extraction_error = _err(e)
                if isinstance(e, ProviderAuthError) or e.status_code in ACCOUNT_ERROR_STATUS:
                    self._llm_stopped = f"LLM 2 stopped after an account-level error ({_err(e)})"
                    self.warnings.append(f"{self._llm_stopped}; remaining pages were not sent")
                return
        self.usage.llm_model = result.model
        self.usage.llm_prompt_tokens += result.usage.prompt_tokens
        self.usage.llm_completion_tokens += result.usage.completion_tokens
        if result.usage.cost_usd is not None:
            self.usage.llm_cost_usd = (self.usage.llm_cost_usd or Decimal(0)) + result.usage.cost_usd
        ext = result.value
        checked = check_extraction(ext, text, page.title)
        v = checked.values
        cand.extraction_status = ExtractionStatus.COMPLETED
        cand.llm_request_ids = result.request_ids
        cand.page_type = ext.page_type
        cand.product_title = v.get(ExtractedField.PRODUCT_TITLE)
        cand.brand = v.get(ExtractedField.BRAND)
        cand.model = v.get(ExtractedField.MODEL)
        cand.sku = v.get(ExtractedField.SKU)
        cand.availability = v.get(ExtractedField.AVAILABILITY)
        cand.price, cand.currency, cand.price_text = checked.price, checked.currency, checked.price_text
        cand.specifications = checked.specifications
        cand.sale_indicated = checked.sale_indicated
        cand.ignored_prices = [t.strip()[:200] for t in ext.ignored_prices[:10]]
        cand.field_evidence = checked.evidence
        cand.llm_confidence = ext.confidence
        cand.llm_notes = (ext.notes or "").strip()[:500] or None

    # ------------------------------------------------------------------ run

    async def run_product(self, p: StompboxProduct) -> ProductIntelligence:
        queries = product_queries(p.brand, p.manufacturer_model, p.product_name, p.specifications,
                                  self.s.intelligence_queries_per_domain)  # fmt: skip
        summaries, hits = await self._search_product(p, queries)
        selected, skipped = self._select(hits, summaries)
        # Fetch the canonical URL: it maps locale storefronts (e.g. Gear4music /us/en/) to
        # the UK store. The provider's original URL stays on the search hit as evidence.
        pages = await self._retrieve([h.canonical_url for h in selected])
        targets = {t.domain: t for t in self.targets}
        candidates: list[CompetitorProduct] = []
        jobs = []
        for h in selected:
            page, request_id, error = pages.get(h.canonical_url, (None, None, "not retrieved"))
            record = PageRecord(
                status=page.status if page else None, provider=self.search.name, provider_request_id=request_id,
                requested_url=h.canonical_url, final_url=str(page.url) if page and page.url else None,
                title=page.title if page else None, content_chars=page.content_chars if page else 0,
                retrieved_at=page.retrieved_at if page else None, cache_source=page.cache_source if page else None,
                error=(page.error if page else None) or error,
            )  # fmt: skip
            cand = CompetitorProduct(
                candidate_id=f"{p.product_id}:{hashlib.sha1(h.canonical_url.encode()).hexdigest()[:12]}",
                stompbox_product_id=p.product_id, competitor=targets[h.domain],
                product_url=record.final_url or h.canonical_url, search=h, page=record,
                extraction_status=ExtractionStatus.SKIPPED_NOT_RETRIEVED,
            )  # fmt: skip
            candidates.append(cand)
            if page is None or page.status is not RetrievalStatus.OK:
                why = page.status.value if page else "not retrieved"
                cand.extraction_error = f"Page {why}" + (f": {record.error}" if record.error else "")
            elif self.llm is None:
                cand.extraction_status = ExtractionStatus.SKIPPED_NO_LLM
                cand.extraction_error = f"LLM 2 not used ({self.llm_disabled_reason}); page retrieved, nothing extracted"
            else:
                jobs.append(self._extract(cand, p, page))
        await asyncio.gather(*jobs)
        return ProductIntelligence(
            product=ProductSummary(product_id=p.product_id, product_name=p.product_name, brand=p.brand,
                                   manufacturer_model=p.manufacturer_model),  # fmt: skip
            queries=queries, domains=summaries, candidates=candidates, not_selected=skipped,
        )

    async def run(self, products: list[StompboxProduct]) -> IntelligenceRunResult:
        if not products:
            raise ValueError("Select at least one product")
        started = _now()
        if self.llm is None:
            self.warnings.append(f"LLM 2 extraction skipped ({self.llm_disabled_reason}); "
                                 "pages are searched and retrieved but no product fields are extracted")  # fmt: skip
        if not self.targets:
            self.warnings.append("No approved search targets; nothing was searched")
        results = [await self.run_product(p) for p in products]
        failed = self.usage.llm_failures
        if failed:
            self.warnings.append(f"LLM 2 extraction failed for {failed} page(s); see extraction_error")
        return IntelligenceRunResult(
            run_id=f"intelligence-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}",
            mode=self.mode, llm_disabled_reason=self.llm_disabled_reason, started_at=started, finished_at=_now(),
            search_provider=self.search.name, search_targets=self.targets, skipped_targets=self.skipped_targets,
            products=results, usage=self.usage, warnings=self.warnings,
        )  # fmt: skip
