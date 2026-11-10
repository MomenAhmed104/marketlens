"""Benchmark search providers against a representative Stompbox product sample.

    python -m app.benchmark.search_benchmark --dry-run
    python -m app.benchmark.search_benchmark --providers exa tavily

For each provider:
  1. Global search per product.
  2. Domain-targeted search per product x retailer domain.
  3. Content retrieval of one shared pool of candidate pages (found by any
     provider), so retrieval is compared on identical URLs.

Identity is scored with base model + variant rules (app/benchmark/products.py),
not keyword overlap. Outputs (relative to the repo root):
  data/benchmark_runs/<run>/   raw records incl. page content (git-ignored)
  docs/benchmarks/<run>.json   metrics summary
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from app.benchmark.metrics import (
    Identity,
    attribute_price,
    classify_identity,
    gbp_prices,
    is_uk_host,
    mentions_model,
    page_type,
    percentile,
)
from app.benchmark.products import SAMPLE, SAMPLE_BY_ID, BenchmarkProduct
from app.core.config import REPO_ROOT, Settings
from app.schemas.catalogue import StompboxProduct
from app.schemas.competitor import CompetitorDomain
from app.schemas.search import RetrievalStatus, SearchQuery, SearchScope
from app.services.catalogue import Catalogue, load_catalogue
from app.services.domains import normalize_hostname
from app.services.search import PROVIDER_NAMES, SearchProvider, build_provider, configured_providers
from app.services.search.base import content_status, host_in_domains
from app.services.search.http import SearchProviderError

DEFAULT_CONCURRENCY = {"exa": 3, "tavily": 4, "brave": 1}
SNIPPET_KEEP = 400
GOOD_IDENTITIES = (Identity.EXACT, Identity.REVIEW)
NEGATIVE_IDENTITIES = (Identity.VARIANT_MISMATCH, Identity.CONDITION_MISMATCH, Identity.ACCESSORY)


@dataclass
class ResultRow:
    rank: int
    url: str  # original, as returned (evidence)
    canonical_url: str
    hostname: str
    title: str | None
    snippet: str | None
    published_raw: str | None
    identity: str
    page_type: str
    known_retailer: bool
    uk_host: bool
    snippet_gbp_prices: list[str]


@dataclass
class SearchRecord:
    provider: str
    phase: str  # "global" | "domain"
    product_id: str
    domain: str | None
    query_text: str = ""
    ok: bool = True
    error: str | None = None
    latency_ms: float | None = None
    cost_usd: str | None = None
    cost_basis: str | None = None
    cost_detail: str | None = None
    request_id: str | None = None
    retrieved_at: str | None = None
    out_of_scope_dropped: int = 0
    results: list[ResultRow] = field(default_factory=list)


@dataclass
class PoolEntry:
    canonical_url: str
    original_urls: list[str]
    found_by: list[str]
    search_identity: str
    page_type: str
    role: str  # "candidate" | "negative_control"


@dataclass
class RetrievalRecord:
    provider: str
    product_id: str
    canonical_url: str
    hostname: str
    found_by: list[str]
    search_identity: str
    role: str
    status: str
    error: str | None = None
    batch_latency_ms: float | None = None
    content_chars: int = 0
    page_title: str | None = None
    content_identity: str | None = None
    model_in_content: bool = False
    distinct_gbp: int = 0
    attributed_price: str | None = None
    price_context: str | None = None
    price_candidates: int = 0
    availability: str | None = None
    cache_source: str | None = None
    retrieved_at: str | None = None
    content: str | None = None  # raw output only


def query_text(p: StompboxProduct, spec: BenchmarkProduct) -> str:
    return " ".join(x for x in (p.brand, spec.base_model, spec.query_extra) if x)


def _row(spec: BenchmarkProduct, brand: str, r, retailer_domains: tuple[str, ...]) -> ResultRow:
    return ResultRow(
        rank=r.rank, url=str(r.url), canonical_url=r.canonical_url, hostname=r.hostname,
        title=r.title, snippet=(r.snippet or "")[:SNIPPET_KEEP] or None, published_raw=r.published_raw,
        identity=classify_identity(spec, r.title, r.canonical_url, brand),
        page_type=page_type(r.hostname, r.canonical_url),
        known_retailer=host_in_domains(r.hostname, retailer_domains),
        uk_host=is_uk_host(r.hostname),
        snippet_gbp_prices=sorted({str(x) for x in gbp_prices(r.snippet)}),
    )  # fmt: skip


async def run_search(provider, sem, p, spec, domain, max_results, retailer_domains) -> SearchRecord:
    phase = "domain" if domain else "global"
    text = query_text(p, spec)
    query = SearchQuery(
        text=text, product_id=p.product_id, max_results=max_results,
        scope=SearchScope.DOMAIN if domain else SearchScope.GLOBAL,
        domains=(domain,) if domain else (),
    )  # fmt: skip
    rec = SearchRecord(provider.name, phase, p.product_id, domain, query_text=text)
    async with sem:
        try:
            resp = await provider.search(query)
        except SearchProviderError as e:
            rec.ok, rec.error = False, f"{type(e).__name__}: {e}"
            return rec
    rec.latency_ms = resp.latency_ms
    rec.cost_usd = str(resp.cost.usd) if resp.cost.usd is not None else None
    rec.cost_basis, rec.cost_detail = resp.cost.basis.value, resp.cost.detail
    rec.request_id, rec.retrieved_at = resp.request_id, resp.retrieved_at.isoformat()
    rec.out_of_scope_dropped = resp.out_of_scope_dropped
    rec.results = [_row(spec, p.brand, r, retailer_domains) for r in resp.results]
    return rec


def build_pool(searches: list[SearchRecord], per_provider: int) -> dict[str, dict[str, PoolEntry]]:
    """Shared retrieval pool per product.

    Candidates: each provider's best-ranked EXACT/REVIEW retailer product pages
    (targeted searches first), up to `per_provider` each. Plus one variant or
    condition mismatch page per product as a negative control.
    """
    pool: dict[str, dict[str, PoolEntry]] = defaultdict(dict)
    picked: Counter = Counter()
    negatives: set[str] = set()
    for s in sorted(searches, key=lambda s: (s.phase != "domain", s.provider, s.domain or "")):
        for r in s.results:
            if not r.known_retailer or r.page_type not in ("product", "unknown"):
                continue
            entry = pool[s.product_id].get(r.canonical_url)
            if entry is not None:
                if s.provider not in entry.found_by:
                    entry.found_by.append(s.provider)
                if r.url not in entry.original_urls:
                    entry.original_urls.append(r.url)
                continue
            if r.identity in GOOD_IDENTITIES and picked[(s.product_id, s.provider)] < per_provider:
                picked[(s.product_id, s.provider)] += 1
                role = "candidate"
            elif r.identity in NEGATIVE_IDENTITIES and s.product_id not in negatives:
                negatives.add(s.product_id)
                role = "negative_control"
            else:
                continue
            pool[s.product_id][r.canonical_url] = PoolEntry(
                r.canonical_url, [r.url], [s.provider], r.identity, r.page_type, role
            )
    return pool


def _first_heading(content: str | None, base_model: str) -> str | None:
    for line in (content or "").splitlines()[:200]:
        line = line.strip()
        if line.startswith("#") and mentions_model(base_model, line):
            return line.lstrip("#").strip()
    return None


async def run_retrieval(provider, sem, p, spec, entries: list[PoolEntry]):
    urls = [e.canonical_url for e in entries]
    by_url = {e.canonical_url: e for e in entries}
    async with sem:
        try:
            resp = await provider.retrieve(urls)
        except SearchProviderError as e:
            err = f"{type(e).__name__}: {e}"
            recs = [RetrievalRecord(provider.name, p.product_id, u, normalize_hostname(u), by_url[u].found_by,
                                    by_url[u].search_identity, by_url[u].role, "FAILED", err) for u in urls]  # fmt: skip
            return recs, {"ok": False, "error": err}
    recs = []
    for page in resp.pages:
        e = by_url[page.requested_url]
        title = page.title or _first_heading(page.content, spec.base_model)
        ev = attribute_price(page.content, spec.base_model)
        recs.append(RetrievalRecord(
            provider=provider.name, product_id=p.product_id, canonical_url=e.canonical_url,
            hostname=normalize_hostname(e.canonical_url), found_by=e.found_by,
            search_identity=e.search_identity, role=e.role, status=page.status.value, error=page.error,
            batch_latency_ms=resp.latency_ms, content_chars=page.content_chars, page_title=title,
            content_identity=(classify_identity(spec, title, e.canonical_url, p.brand)
                              if page.status is RetrievalStatus.OK else None),
            model_in_content=mentions_model(spec.base_model, page.content),
            distinct_gbp=len(set(gbp_prices(page.content))),
            attributed_price=str(ev.price) if ev.price is not None else None,
            price_context=ev.context, price_candidates=ev.candidates, availability=ev.availability,
            cache_source=page.cache_source, retrieved_at=page.retrieved_at.isoformat(),
            content=page.content,
        ))  # fmt: skip
    return recs, {"ok": True, "latency_ms": resp.latency_ms, "request_id": resp.request_id,
                  "cost_usd": str(resp.cost.usd) if resp.cost.usd is not None else None,
                  "cost_basis": resp.cost.basis.value, "cost_detail": resp.cost.detail}  # fmt: skip


# --------------------------------------------------------------------------- summary


PRODUCTISH = ("product", "unknown")  # unknown = retailer URL pattern not modelled


def _exact_page(r: ResultRow) -> bool:
    return r.identity == Identity.EXACT and r.page_type in PRODUCTISH


def _dsum(values) -> str:
    return str(sum((Decimal(v) for v in values if v is not None), Decimal(0)))


def _pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


def _lat(values: list[float]) -> dict:
    return {"n": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95),
            "max": max(values) if values else None}  # fmt: skip


def _credits(details) -> str:
    total = Decimal(0)
    for d in details:
        m = re.match(r"([\d.]+) credits", d or "")
        if m:
            total += Decimal(m.group(1))
    return str(total)


def summarise(providers, products, retailers, searches, retrievals, batches) -> dict:
    retailer_domains = [c.domain for c in retailers]
    exact_pairs: dict[str, set] = {}
    for prov in providers:
        exact_pairs[prov] = {
            (s.product_id, s.domain) for s in searches
            if s.provider == prov and s.phase == "domain" and s.ok
            and any(_exact_page(r) for r in s.results)
        }  # fmt: skip
    union = set().union(*exact_pairs.values()) if exact_pairs else set()

    out: dict = {}
    for prov in providers:
        mine = [s for s in searches if s.provider == prov]
        ok = [s for s in mine if s.ok]
        glob = [s for s in ok if s.phase == "global"]
        dom = [s for s in ok if s.phase == "domain"]
        grow = [r for s in glob for r in s.results]
        first_exact = [min(r.rank for r in s.results if r.identity == Identity.EXACT and r.known_retailer)
                       for s in glob if any(r.identity == Identity.EXACT and r.known_retailer for r in s.results)]
        uk_exact_hosts = sorted({r.hostname for r in grow if r.identity == Identity.EXACT and r.uk_host})
        top1 = Counter(s.results[0].identity + ("/product" if s.results[0].page_type in PRODUCTISH else "/other")
                       for s in dom if s.results)  # fmt: skip
        per_retailer = {d: {"searched": sum(1 for s in dom if s.domain == d),
                            "exact_product_page": sum(1 for (_, dd) in exact_pairs[prov] if dd == d)}
                        for d in retailer_domains}  # fmt: skip
        per_product = {p.product_id: sum(1 for (pid, _) in exact_pairs[prov] if pid == p.product_id)
                       for p in products}  # fmt: skip
        only_mismatch = sum(
            1 for s in dom
            if not any(r.identity == Identity.EXACT for r in s.results)
            and any(r.identity in NEGATIVE_IDENTITIES for r in s.results)
        )  # fmt: skip
        rets = [r for r in retrievals if r.provider == prov]
        rok = [r for r in rets if r.status == RetrievalStatus.OK.value]
        cands = [r for r in rok if r.role == "candidate"]
        my_batches = [b for b in batches if b["provider"] == prov and b["ok"]]
        out[prov] = {
            "requests": {"search": len(mine), "search_errors": dict(Counter(s.error.split(":")[0] for s in mine if not s.ok)),
                         "retrieval_batches": len([b for b in batches if b["provider"] == prov]),
                         "retrieval_batch_errors": len([b for b in batches if b["provider"] == prov and not b["ok"]])},
            "latency_ms": {"global_search": _lat([s.latency_ms for s in glob]),
                           "domain_search": _lat([s.latency_ms for s in dom]),
                           "retrieval_batch": _lat([b["latency_ms"] for b in my_batches])},
            "cost": {
                "search_usd": _dsum(s.cost_usd for s in mine),
                "retrieval_usd": _dsum(b["cost_usd"] for b in my_batches),
                "search_basis": sorted({s.cost_basis for s in ok if s.cost_basis}),
                "retrieval_basis": sorted({b["cost_basis"] for b in my_batches}),
                "credits_reported_search": _credits(s.cost_detail for s in ok) if prov == "tavily" else None,
                "credits_retrieval": _credits(b.get("cost_detail") for b in my_batches) if prov == "tavily" else None,
            },
            "global": {
                "queries": len(glob),
                "results": len(grow),
                "uk_host_results_pct": _pct(sum(r.uk_host for r in grow), len(grow)),
                "known_retailer_results_pct": _pct(sum(r.known_retailer for r in grow), len(grow)),
                "identity_counts": dict(Counter(r.identity for r in grow)),
                "products_with_exact_known_retailer_page_pct": _pct(len(first_exact), len(glob)),
                "mean_rank_first_exact_known_retailer": round(statistics.fmean(first_exact), 2) if first_exact else None,
                "uk_hosts_with_exact_match": uk_exact_hosts,
            },
            "targeted": {
                "pairs_searched": len(dom),
                "empty_responses": sum(1 for s in dom if not s.results),
                "pairs_with_exact_product_page": len(exact_pairs[prov]),
                "coverage_pct": _pct(len(exact_pairs[prov]), len(dom)),
                "recall_vs_union_pct": _pct(len(exact_pairs[prov]), len(union)),
                "pairs_only_variant_or_condition_mismatch": only_mismatch,
                "top1_identity": dict(top1),
                "top1_exact_product_page_pct": _pct(top1.get("EXACT/product", 0), sum(top1.values())),
                "out_of_scope_dropped": sum(s.out_of_scope_dropped for s in dom),
                "per_retailer": per_retailer,
                "per_product_exact_retailers": per_product,
            },
            "retrieval": {
                "pages": len(rets),
                "ok_pct": _pct(len(rok), len(rets)),
                "status_counts": dict(Counter(r.status for r in rets)),
                "failures_by_host": dict(Counter(r.hostname for r in rets if r.status != "OK")),
                "median_chars": statistics.median([r.content_chars for r in rok]) if rok else None,
                "candidates_ok": len(cands),
                "candidate_content_identity": dict(Counter(r.content_identity for r in cands)),
                "candidate_with_attributed_price_pct": _pct(sum(r.attributed_price is not None for r in cands), len(cands)),
                "candidate_with_availability_pct": _pct(sum(r.availability is not None for r in cands), len(cands)),
                "median_distinct_gbp": statistics.median([r.distinct_gbp for r in rok]) if rok else None,
                "cache_source": dict(Counter(r.cache_source or "n/a" for r in rok)),
            },
        }

    # Cross-provider price agreement on identical pages.
    by_url = defaultdict(dict)
    for r in retrievals:
        if r.status == "OK":
            by_url[(r.product_id, r.canonical_url)][r.provider] = r.attributed_price
    both = {k: v for k, v in by_url.items() if len(v) == len(providers) and len(providers) > 1}
    agree = [k for k, v in both.items() if len(set(v.values())) == 1 and None not in v.values()]
    differ = {f"{k[0]} {k[1]}": v for k, v in both.items() if k not in agree}
    return {
        "products": [p.product_id for p in products],
        "retailer_domains": retailer_domains,
        "targeted_union_exact_pairs": len(union),
        "providers": out,
        "price_agreement": {"pages_ok_for_all": len(both), "same_attributed_price": len(agree),
                            "different_or_missing": differ},
    }


# --------------------------------------------------------------------------- main


async def run(args, settings: Settings, catalogue: Catalogue) -> int:
    specs = [SAMPLE_BY_ID[pid] for pid in args.products]
    products = [catalogue.products[s.product_id] for s in specs]
    retailers = [c for c in catalogue.competitors if c.price_benchmark]
    retailer_domains = tuple(c.domain for c in retailers)
    n_glob, n_dom = len(products), len(products) * len(retailers)
    print(f"Products: {len(products)}  Retailer domains: {len(retailers)}  "
          f"Searches/provider: {n_glob + n_dom} ({n_glob} global + {n_dom} domain)")  # fmt: skip
    for s, p in zip(specs, products):
        print(f"  {s.product_id}: {query_text(p, s)!r}")
    if args.dry_run:
        print("Configured providers:", configured_providers(settings) or "none")
        return 0

    available = configured_providers(settings)
    providers = [p for p in args.providers if p in available]
    for p in [p for p in args.providers if p not in available]:
        print(f"Skipping {p}: API key not set", file=sys.stderr)
    if not providers:
        print("No requested provider is configured.", file=sys.stderr)
        return 2

    started = datetime.now(UTC)
    clients = {n: build_provider(n, settings) for n in providers}
    try:
        sems = {n: asyncio.Semaphore(DEFAULT_CONCURRENCY[n]) for n in providers}
        tasks = []
        for n, prov in clients.items():
            for s, p in zip(specs, products):
                tasks.append(run_search(prov, sems[n], p, s, None, args.global_results, retailer_domains))
                for d in retailer_domains:
                    tasks.append(run_search(prov, sems[n], p, s, d, args.domain_results, retailer_domains))
        print(f"Running {len(tasks)} searches...")
        searches: list[SearchRecord] = await asyncio.gather(*tasks)

        pool = build_pool(searches, args.retrieve_per_product)
        retrievers = [n for n in providers if clients[n].supports_retrieval]
        print(f"Retrieving {sum(len(v) for v in pool.values())} pooled pages with {retrievers}...")
        jobs = [(n, pid, list(entries.values())) for n in retrievers for pid, entries in pool.items() if entries]
        outcomes = await asyncio.gather(*(
            run_retrieval(clients[n], sems[n], catalogue.products[pid], SAMPLE_BY_ID[pid], es)
            for n, pid, es in jobs
        ))  # fmt: skip
        retrievals: list[RetrievalRecord] = []
        batches: list[dict] = []
        for (n, pid, _), (recs, batch) in zip(jobs, outcomes):
            retrievals += recs
            batches.append({"provider": n, "product_id": pid, **batch})
    finally:
        for prov in clients.values():
            await prov.aclose()
    finished = datetime.now(UTC)

    run_id = started.strftime("search-benchmark-%Y%m%dT%H%M%SZ")
    raw_dir = REPO_ROOT / "data" / "benchmark_runs" / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    dump = lambda name, obj: (raw_dir / name).write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")  # noqa: E731
    dump("searches.json", [asdict(s) for s in searches])
    dump("pool.json", {pid: [asdict(e) for e in es.values()] for pid, es in pool.items()})
    dump("retrievals.json", [asdict(r) for r in retrievals])
    dump("batches.json", batches)

    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "providers_run": providers,
        "settings": {
            "exa_search_type": settings.exa_search_type,
            "exa_content_max_age_hours": settings.exa_content_max_age_hours,
            "exa_livecrawl_timeout_ms": settings.exa_livecrawl_timeout_ms,
            "tavily_search_depth": settings.tavily_search_depth,
            "tavily_extract_depth": settings.tavily_extract_depth,
            "global_results": args.global_results,
            "domain_results": args.domain_results,
            "retrieve_per_product_per_provider": args.retrieve_per_product,
            "country": "GB",
            "concurrency": {n: DEFAULT_CONCURRENCY[n] for n in providers},
        },
        "queries": {s.product_id: query_text(p, s) for s, p in zip(specs, products)},
        "identity_specs": {s.product_id: asdict(s) for s in specs},
        **summarise(providers, products, retailers, searches, retrievals, batches),
    }
    out = REPO_ROOT / "docs" / "benchmarks" / f"{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"Raw records: {raw_dir}\nSummary:     {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--providers", nargs="+", choices=PROVIDER_NAMES, default=["exa", "tavily"])
    parser.add_argument("--products", nargs="+", default=[s.product_id for s in SAMPLE],
                        choices=[s.product_id for s in SAMPLE])  # fmt: skip
    parser.add_argument("--global-results", type=int, default=10)
    parser.add_argument("--domain-results", type=int, default=5)
    parser.add_argument("--retrieve-per-product", type=int, default=2,
                        help="Max candidate pages each provider contributes per product")  # fmt: skip
    parser.add_argument("--dry-run", action="store_true", help="Print the plan only")
    parser.add_argument("--rescore", metavar="RUN_ID",
                        help="Re-apply the current identity rules to a saved run (no API calls)")
    args = parser.parse_args(argv)
    settings = Settings()
    catalogue = load_catalogue(settings.catalogue_path)
    if args.rescore:
        return rescore(args.rescore, catalogue)
    return asyncio.run(run(args, settings, catalogue))


def rescore(run_id: str, catalogue: Catalogue) -> int:
    """Recompute identities and the summary from saved raw records.

    Searches and retrievals are not repeated. The retrieval pool keeps the
    composition chosen during the run; only its classifications change.
    """
    raw_dir = REPO_ROOT / "data" / "benchmark_runs" / run_id
    out = REPO_ROOT / "docs" / "benchmarks" / f"{run_id}.json"
    previous = json.loads(out.read_text(encoding="utf-8"))
    load = lambda name: json.loads((raw_dir / name).read_text(encoding="utf-8"))  # noqa: E731

    searches = []
    for d in load("searches.json"):
        spec, prod = SAMPLE_BY_ID[d["product_id"]], catalogue.products[d["product_id"]]
        rows = [ResultRow(**r) for r in d.pop("results")]
        for r in rows:
            r.identity = classify_identity(spec, r.title, r.canonical_url, prod.brand)
        searches.append(SearchRecord(**d, results=rows))
    retrievals = []
    for d in load("retrievals.json"):
        rec = RetrievalRecord(**d)
        spec, prod = SAMPLE_BY_ID[rec.product_id], catalogue.products[rec.product_id]
        rec.search_identity = next(
            (r.identity for s in searches if s.product_id == rec.product_id
             for r in s.results if r.canonical_url == rec.canonical_url), rec.search_identity)  # fmt: skip
        if rec.status in (RetrievalStatus.OK.value, RetrievalStatus.BLOCKED.value):
            rec.status = content_status(rec.content).value
        if rec.status == RetrievalStatus.OK.value:
            rec.content_identity = classify_identity(spec, rec.page_title, rec.canonical_url, prod.brand)
            ev = attribute_price(rec.content, spec.base_model)
            rec.attributed_price = str(ev.price) if ev.price is not None else None
            rec.price_context, rec.price_candidates, rec.availability = ev.context, ev.candidates, ev.availability
            rec.model_in_content = mentions_model(spec.base_model, rec.content)
        else:
            rec.content_identity = rec.attributed_price = rec.price_context = rec.availability = None
            rec.price_candidates = 0
        retrievals.append(rec)
    batches = load("batches.json")

    providers = previous["providers_run"]
    products = [catalogue.products[pid] for pid in previous["products"]]
    retailers = [c for c in catalogue.competitors if c.domain in previous["retailer_domains"]]
    summary = {k: v for k, v in previous.items() if k in ("run_id", "started_at", "finished_at",
                                                          "providers_run", "settings", "queries")}
    summary["rescored_at"] = datetime.now(UTC).isoformat()
    summary["identity_specs"] = {pid: asdict(SAMPLE_BY_ID[pid]) for pid in previous["products"]}
    summary.update(summarise(providers, products, retailers, searches, retrievals, batches))

    (raw_dir / "searches.rescored.json").write_text(json.dumps([asdict(s) for s in searches], indent=1), encoding="utf-8")
    (raw_dir / "retrievals.rescored.json").write_text(json.dumps([asdict(r) for r in retrievals], indent=1), encoding="utf-8")
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"Rescored summary: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
