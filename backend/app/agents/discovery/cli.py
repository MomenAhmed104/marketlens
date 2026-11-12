"""Run competitor discovery from the command line.

    python -m app.agents.discovery.cli SB-001 SB-028

Prints a summary and writes the full result (with evidence) to
data/discovery_runs/<run_id>.json, which is git-ignored.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.agents.discovery import build_discovery_agent
from app.core.config import REPO_ROOT, Settings
from app.services.catalogue import load_catalogue
from app.services.search.http import ProviderError


def summarise(result) -> str:
    lines = [
        f"Run {result.run_id}  products={result.product_ids}  provider={result.search_provider}",
        f"Mode: {result.mode.value}" + (f" ({result.llm_disabled_reason})" if result.llm_disabled_reason else ""),
        f"Global queries: {len(result.global_queries)}  "
        f"search requests: {result.usage.search_requests} (errors {result.usage.search_errors})  "
        f"search cost: ${result.usage.search_cost_usd}",
        f"LLM {result.usage.llm_model}: {result.usage.llm_calls} call(s), "
        f"{result.usage.llm_prompt_tokens}+{result.usage.llm_completion_tokens} tokens, cost ${result.usage.llm_cost_usd}",
        "",
        "Known competitors seen in discovery results:",
    ]
    for k in result.known_competitor_evidence:
        lines.append(f"  {k.domain:28} products with model on page: {k.products_seen}")
    lines += ["", "Candidates:"]
    for c in result.candidates:
        lines.append(
            f"  [{c.approval_status.value:17}] {c.domain:30} classification={c.classification_status.value} "
            f"name={c.company_name!r} type={c.company_type and c.company_type.value} "
            f"conf={c.confidence} rel={c.relevance} uk={c.uk_signals} products={c.products_matched}"
        )
        for r in c.decision_reasons:
            lines.append(f"      - {r}")
        for u in c.cited_evidence_urls[:3]:
            lines.append(f"      evidence: {u}")
    lines += ["", f"Excluded: {len(result.excluded)}"]
    for x in result.excluded:
        lines.append(f"  {x.domain:30} {x.reason}")
    by_domain: dict[str, int] = {}
    for q in result.targeted_queries:
        by_domain[q.domain] = by_domain.get(q.domain, 0) + 1
    lines += ["", f"Targeted queries: {len(result.targeted_queries)} across {len(by_domain)} approved domains"]
    lines += [f"  warning: {w}" for w in result.warnings]
    return "\n".join(lines)


async def main_async(product_ids: list[str]) -> int:
    settings = Settings()
    catalogue = load_catalogue(settings.catalogue_path)
    products, missing = catalogue.lookup(product_ids)
    if missing:
        print(f"Unknown product IDs: {missing}", file=sys.stderr)
        return 2
    try:
        agent = await build_discovery_agent(settings, catalogue)
    except ProviderError as e:
        print(f"Not configured: {e}", file=sys.stderr)
        return 3
    try:
        result = await agent.run(products)
    finally:
        await agent.aclose()
    out_dir = REPO_ROOT / "data" / "discovery_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result.run_id}.json"
    path.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    print(summarise(result))
    print(f"\nFull result: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("product_ids", nargs="+")
    return asyncio.run(main_async(parser.parse_args(argv).product_ids))


if __name__ == "__main__":
    raise SystemExit(main())
