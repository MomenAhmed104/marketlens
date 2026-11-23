"""Stage 9.1 smoke test: SB-001, discovery off, Andertons and Gear4music only, $0.25 cap.

    python -m app.stage9.smoke setup --dir DIR             # creates DIR/ledger.sqlite3 ($3.00) + DIR/review.sqlite3
    python -m app.stage9.smoke check --dir DIR PRICES      # preflight: no network, no paid call
    python -m app.stage9.smoke run   --dir DIR PRICES      # the paid run (only when authorised)

PRICES = --exa-search-usd --exa-page-usd --llm-input-usd-per-token --llm-output-usd-per-token

The scope and limits are fixed here, not taken from the environment: one product, no discovery,
two configured domains, a $0.25 per-run reservation cap inside a $3.00 ledger ceiling. `run`
refuses unless every preflight check passes: the ledger exists with exactly the $3.00 ceiling,
it has no unresolved run, enough budget remains for the full $0.25 reservation, the evaluation
database is isolated, and the given prices are not below the maxima verified on 23 Nov 2026
(docs/stage9/README.md). The run itself goes through the harness: POST /api/runs on the real
app in budget mode, so the BudgetedRunManager reserves before start and the meter checks the
worst case of every HTTP attempt (retries included) against the reservation before sending it.
Nothing here changes .env; API keys come from the environment as usual and are never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.stage9.budget import BudgetLedger, LedgerError, UnitPrices, estimate_run
from app.stage9.isolated_db import check_destination, create_empty

PRODUCT_IDS = ["SB-001"]
DOMAINS = ["andertons.co.uk", "gear4music.com"]
RUN_DISCOVERY = False
SMOKE_CAP_USD = Decimal("0.25")
STAGE9_CEILING_USD = Decimal("3.00")
# Maximum prices verified on 23 Nov 2026 (docs/stage9/README.md, "Pricing"). Configured prices
# may be higher (more conservative), never lower.
VERIFIED_MIN_PRICES = {
    "exa_search": Decimal("0.007"),  # Exa /search auto, <= 10 results, contents included
    "exa_page": Decimal("0.001"),  # Exa /contents, per page per content type (text only)
    "llm_input": Decimal("0.00000025"),  # OpenRouter gpt-6-luna, highest endpoint incl. cache write ($0.25/M)
    "llm_output": Decimal("0.000001"),  # OpenRouter gpt-6-luna, highest endpoint ($1.00/M)
}


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def ledger(self) -> Path:
        return self.root / "ledger.sqlite3"

    @property
    def review_db(self) -> Path:
        return self.root / "review.sqlite3"

    @property
    def runs(self) -> Path:
        return self.root / "runs"


def smoke_settings(paths: Paths, prices: dict[str, Decimal], **overrides):
    """Settings for the smoke process: the environment as usual, plus the fixed Stage 9 values."""
    from app.stage9.harness import ScenarioRun, build_settings

    cfg = ScenarioRun(label="smoke", product_ids=PRODUCT_IDS, review_db=paths.review_db, ledger=paths.ledger,
                      output_dir=paths.runs)  # fmt: skip
    return build_settings(cfg, stage9_max_reservation_per_run_usd=SMOKE_CAP_USD,
                          stage9_exa_usd_per_search_max=prices["exa_search"],
                          stage9_exa_usd_per_page_max=prices["exa_page"],
                          stage9_llm_usd_per_input_token_max=prices["llm_input"],
                          stage9_llm_usd_per_output_token_max=prices["llm_output"], **overrides)  # fmt: skip


def preflight(paths: Paths, prices: dict[str, Decimal], settings, cap: Decimal = SMOKE_CAP_USD) -> list[str]:
    """Every reason a Stage 9 run with reservation cap `cap` must not start (empty = ready). No network, no writes."""
    problems = []
    for name, minimum in VERIFIED_MIN_PRICES.items():
        if prices.get(name) is None or prices[name] < minimum:
            problems.append(f"price {name}={prices.get(name)} is below the verified maximum {minimum}")
    try:
        check_destination(paths.review_db)
    except Exception as e:
        problems.append(str(e))
    if not paths.review_db.is_file():
        problems.append(f"evaluation database {paths.review_db} does not exist (run `setup`)")
    try:
        ledger = BudgetLedger(paths.ledger)
    except LedgerError as e:
        problems.append(str(e))
        ledger = None
    if ledger is not None:
        s = ledger.summary()
        if s.ceiling_usd != STAGE9_CEILING_USD:
            problems.append(f"ledger ceiling is {s.ceiling_usd}, expected exactly {STAGE9_CEILING_USD}")
        if s.open_reservations or s.needs_review:
            problems.append(f"ledger has unresolved runs: {s.open_reservations + s.needs_review}")
        if s.remaining_usd < cap:
            problems.append(f"only {s.remaining_usd} USD remains; this run reserves {cap}")
    if settings.stage9_max_reservation_per_run_usd != cap:
        problems.append(f"per-run cap is not {cap}")
    if settings.search_provider != "exa":
        problems.append("SEARCH_PROVIDER is not exa")
    if not settings.exa_api_key:
        problems.append("EXA_API_KEY is not set")
    if not settings.openrouter_api_key or not settings.llm2_model:
        problems.append("LLM 2 is not configured (OPENROUTER_API_KEY / LLM2_MODEL)")
    if settings.product_images_enabled:
        problems.append("product images must be off for the smoke run")
    return problems


def worst_case(settings, prices_obj: UnitPrices) -> dict:
    from app.agents.intelligence import llm2_disabled_reason

    est = estimate_run(settings, prices_obj, products=len(PRODUCT_IDS), run_discovery=RUN_DISCOVERY,
                       max_targets=len(DOMAINS), llm1=False, llm2=llm2_disabled_reason(settings) is None)  # fmt: skip
    return {"exa_searches": est.exa_searches, "exa_pages": est.exa_pages, "llm2_calls": est.llm2_calls,
            "attempts_per_call": {"exa": est.exa_attempts_per_call, "llm": est.llm_attempts_per_call},
            "worst_case_usd": str(est.worst_case_usd), "reservation_usd": str(min(est.worst_case_usd, SMOKE_CAP_USD))}


def _prices(args) -> dict[str, Decimal]:
    return {"exa_search": args.exa_search_usd, "exa_page": args.exa_page_usd, "llm_input": args.llm_input_usd_per_token,
            "llm_output": args.llm_output_usd_per_token}  # fmt: skip


def main(argv: list[str] | None = None, *, settings_overrides: dict | None = None, prepare_app=None) -> int:
    ap = argparse.ArgumentParser(description="Stage 9.1 smoke test (SB-001, discovery off, 2 domains, $0.25 cap)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("setup", "check", "run"):
        p = sub.add_parser(name)
        p.add_argument("--dir", type=Path, required=True, help="Stage 9 folder (not inside data/review/)")
        if name != "setup":
            for flag in ("--exa-search-usd", "--exa-page-usd", "--llm-input-usd-per-token", "--llm-output-usd-per-token"):
                p.add_argument(flag, type=Decimal, required=True)
    args = ap.parse_args(argv)
    paths = Paths(args.dir)
    if args.cmd == "setup":
        try:
            check_destination(paths.review_db)
            BudgetLedger.create(paths.ledger, STAGE9_CEILING_USD, "Stage 9 (smoke test first, $0.25 cap)")
            create_empty(paths.review_db)
        except Exception as e:
            print(f"setup refused: {e}", file=sys.stderr)
            return 1
        print(f"Created {paths.ledger} (ceiling {STAGE9_CEILING_USD} USD) and {paths.review_db} (empty)")
        return 0
    prices = _prices(args)
    settings = smoke_settings(paths, prices, **(settings_overrides or {}))
    problems = preflight(paths, prices, settings)
    report = {"scope": {"product_ids": PRODUCT_IDS, "run_discovery": RUN_DISCOVERY, "domains": DOMAINS},
              "cap_usd": str(SMOKE_CAP_USD), "problems": problems}  # fmt: skip
    if not [p for p in problems if p.startswith("price ")]:
        report["worst_case"] = worst_case(settings, UnitPrices.from_settings(settings))
    print(json.dumps(report, indent=1))
    if problems:
        print("NOT READY: the smoke run will not start.", file=sys.stderr)
        return 2
    if args.cmd == "check":
        print("Preflight passed (nothing was sent).")
        return 0
    from app.stage9.harness import ScenarioRun, run_scenario

    cfg = ScenarioRun(label="smoke", product_ids=PRODUCT_IDS, review_db=paths.review_db, ledger=paths.ledger,
                      output_dir=paths.runs, run_discovery=RUN_DISCOVERY, domains=DOMAINS)  # fmt: skip
    res = run_scenario(cfg, settings=settings, prepare_app=prepare_app)
    entry = BudgetLedger(paths.ledger).run(res.run_id) if res.run_id else None
    print(json.dumps({"run_id": res.run_id, "status": res.status, "http_status": res.http_status,
                      "artifacts": str(res.artifact_dir), "ledger_status": entry and entry["status"],
                      "charged_exa_usd": entry and entry["charged_exa_usd"],
                      "charged_openrouter_usd": entry and entry["charged_openrouter_usd"]}, indent=1, default=str))  # fmt: skip
    if entry is None or entry["status"] != "RECONCILED":
        print("STOP: the run did not reconcile cleanly; resolve it before any further Stage 9 run.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
