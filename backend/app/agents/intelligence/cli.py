"""Run product intelligence from the command line.

    python -m app.agents.intelligence.cli SB-001 --domains andertons.co.uk gear4music.com

Searches the approved search targets (preconfigured + reviewer-approved),
or the --domains subset, prints a summary and writes the full result to
data/intelligence_runs/<run_id>.json (git-ignored: third-party content).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.agents.intelligence import build_intelligence_agent, eligible_search_targets
from app.core.config import REPO_ROOT, Settings
from app.services.catalogue import load_catalogue
from app.services.domains import registrable_domain
from app.services.review_store import ReviewStore
from app.services.search.http import ProviderError


def summarise(result) -> str:
    u = result.usage
    lines = [
        f"Run {result.run_id}  provider={result.search_provider}  targets={len(result.search_targets)}",
        f"Mode: {result.mode.value}" + (f" ({result.llm_disabled_reason})" if result.llm_disabled_reason else ""),
        f"Search: {u.search_requests} request(s), {u.search_errors} error(s), ${u.search_cost_usd}  |  "
        f"Retrieval: {u.pages_ok}/{u.pages_requested} page(s) OK, ${u.retrieval_cost_usd}  |  "
        f"LLM {u.llm_model}: {u.llm_calls} call(s), {u.llm_failures} failed, "
        f"{u.llm_prompt_tokens}+{u.llm_completion_tokens} tokens, ${u.llm_cost_usd}",
    ]
    for t in result.skipped_targets:
        lines.append(f"  skipped target {t.domain}: {t.reason}")
    for pr in result.products:
        lines += ["", f"{pr.product.product_id} {pr.product.product_name}", f"  queries: {pr.queries}"]
        for c in pr.candidates:
            price = f"{c.currency} {c.price}" if c.price is not None else "-"
            pe = next((e for e in c.field_evidence if e.field.value == "price"), None)
            lines.append(
                f"  [{c.extraction_status.value:22}] {c.competitor.domain:26} page={c.page.status and c.page.status.value} "
                f"type={c.page_type and c.page_type.value} price={price}"
                f"{f' ({pe.status.value})' if pe else ''} model={c.model!r} benchmark={c.competitor.price_benchmark}"
            )
            lines.append(f"      {c.product_url}")
            if c.extraction_error:
                lines.append(f"      error: {c.extraction_error}")
        lines.append(f"  not selected: {len(pr.not_selected)}")
    lines += [f"  warning: {w}" for w in result.warnings]
    return "\n".join(lines)


async def main_async(product_ids: list[str], domains: list[str] | None) -> int:
    settings = Settings()
    catalogue = load_catalogue(settings.catalogue_path)
    products, missing = catalogue.lookup(product_ids)
    if missing:
        print(f"Unknown product IDs: {missing}", file=sys.stderr)
        return 2
    store = ReviewStore(settings.review_db_path)
    targets, skipped = eligible_search_targets(catalogue.competitors, store.list_decisions(), settings)
    if domains:
        wanted = {registrable_domain(d) for d in domains}
        not_allowed = sorted(wanted - {t.domain for t in targets})
        if not_allowed:
            print(f"Not approved search targets: {not_allowed}", file=sys.stderr)
            for k in skipped:
                if k.domain in not_allowed:
                    print(f"  {k.domain}: {k.reason}", file=sys.stderr)
            return 2
        targets = [t for t in targets if t.domain in wanted]
    try:
        agent = await build_intelligence_agent(settings, targets, skipped)
    except ProviderError as e:
        print(f"Not configured: {e}", file=sys.stderr)
        return 3
    try:
        result = await agent.run(products)
    finally:
        await agent.aclose()
    out_dir = REPO_ROOT / "data" / "intelligence_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result.run_id}.json"
    path.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    print(summarise(result))
    print(f"\nFull result: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("product_ids", nargs="+")
    parser.add_argument("--domains", nargs="+", help="subset of approved search targets")
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args.product_ids, args.domains))


if __name__ == "__main__":
    raise SystemExit(main())
