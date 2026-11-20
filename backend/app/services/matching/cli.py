"""Match a saved Phase 5 run (no API calls, no key needed).

    python -m app.services.matching.cli data/intelligence_runs/<run_id>.json [--review-db PATH] [--out-dir DIR]

Prints a summary and writes the full result to data/matching_runs/<run_id>.json
(git-ignored).

The run file is never trusted for who a competitor is. Exactly as in
POST /api/matching/evaluate, every candidate's competitor (benchmark flag,
approval and type) is rebuilt from the catalogue and the review database
(REVIEW_DB_PATH, or --review-db), which is only read, never written. If that
database is missing or cannot be read safely, matching still runs but fails
closed: no domain is a price benchmark.

Availability is taken as recorded at extraction time; a run file from before a
rule change keeps its recorded status.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from app.agents.intelligence.schemas import IntelligenceRunResult
from app.core.config import REPO_ROOT, Settings
from app.services.catalogue import load_catalogue
from app.services.matching import evaluate_run
from app.services.matching.trusted import (
    REPLACED_WARNING,
    TrustedStateUnavailable,
    load_review_state,
    trusted_competitors,
)


def summarise(result) -> str:
    lines = [f"Matching for {result.source_run_id} (source mode {result.source_mode.value}), "
             f"price max age {result.price_max_age_hours} h"]  # fmt: skip
    lines += [f"  warning: {w}" for w in result.warnings]
    for pr in result.products:
        counts = ", ".join(f"{k.value}={v}" for k, v in pr.counts.items())
        lines += ["", f"{pr.product_id} {pr.product_name} (Stompbox £{pr.stompbox_price})",
                  f"  {counts}; comparable prices: {pr.comparable_prices}; "
                  f"alternative prices: {pr.alternative_prices}"]  # fmt: skip
        for m in pr.candidates:
            price = f"{m.price.currency} {m.price.price}" if m.price.price is not None else "-"
            diff = f" diff £{m.comparison.difference_gbp}" if m.comparison.eligible else ""
            if m.price_kind.value == "ALTERNATIVE":
                diff = f" alternative ({m.alternative_price.condition_label})"
            lines.append(f"  [{m.match_status.value:18} {m.match_confidence.value:6}] {m.competitor.domain:24} "
                         f"cond={m.condition.condition.value:11} price={m.price.status.value}:{price}{diff}")  # fmt: skip
            lines.append(f"      {m.product_url}")
            for r in m.match_reasons + [f"review: {r}" for r in m.review_reasons]:
                lines.append(f"      - {r}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_file", type=Path)
    parser.add_argument("--review-db", type=Path, default=None,
                        help="review database to read trusted reviewer decisions from "
                             "(default: REVIEW_DB_PATH); opened read-only")  # fmt: skip
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data" / "matching_runs")
    args = parser.parse_args(argv)
    settings = Settings()
    catalogue = load_catalogue(settings.catalogue_path)
    try:
        run = IntelligenceRunResult.model_validate_json(args.run_file.read_text(encoding="utf-8"))
    except ValidationError as e:
        print(f"{args.run_file} is not a valid Phase 5 run result ({e.error_count()} error(s), first: "
              f"{e.errors()[0]['loc']}: {e.errors()[0]['msg']})", file=sys.stderr)
        return 2
    trust_warning = None
    try:
        decisions, trusted = load_review_state(args.review_db or settings.review_db_path), True
    except TrustedStateUnavailable as e:
        decisions, trusted = [], False  # fail closed: nothing is a benchmark
        trust_warning = f"Trusted review data unavailable ({e}); no candidate can be a price benchmark"
        print(f"warning: {trust_warning}", file=sys.stderr)
    run, replaced = trusted_competitors(run, catalogue.competitors, decisions, settings, benchmarks_allowed=trusted)
    try:
        result = evaluate_run(run, catalogue.products, settings.matching_price_max_age_hours)
    except KeyError as e:
        print(f"Unknown product id in run: {e}", file=sys.stderr)
        return 2
    if trust_warning:
        result.warnings.append(trust_warning)
    if replaced:
        result.warnings.append(REPLACED_WARNING.format(n=replaced))
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run.run_id}.json"
    path.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    print(summarise(result))
    print(f"\nFull result: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
