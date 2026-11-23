"""Stage 9.2 initial runs A and B (approved 23 Nov 2026).

    python -m app.stage9.run_ab check --run A --dir DIR PRICES    # preflight: no network, no paid call
    python -m app.stage9.run_ab run   --run A --dir DIR PRICES    # the paid run (only when authorised)

PRICES = --exa-search-usd --exa-page-usd --llm-input-usd-per-token --llm-output-usd-per-token

Fixed here, not taken from the environment:
  - Run A = SB-001, SB-028, SB-017; Run B = SB-030, SB-090, SB-055 (must equal the answer key's
    planned runs A and B, and the answer key must be APPROVED);
  - discovery on (LLM 1 must be configured);
  - searched: the 13 configured retailers only (passed explicitly as the run's domain list);
  - automatic search forced off: discovered domains are queued as pending and never searched;
  - $0.75 reservation cap per run, inside the existing $3.00 ledger (never re-created);
  - the existing isolated evaluation database, never reset, with NO reviewer decisions in it
    (decisions are applied only after both initial runs);
  - each run name runs once: an existing artifact for that run refuses a repeat.

Everything else is the smoke-test preflight (prices >= verified maxima, ledger ceiling exactly
$3.00, nothing unresolved, enough remaining, isolated database, keys configured, images off) and
the same execution path: the harness drives POST /api/runs on the real app in budget mode.
After the run it checks the searched domains, writes a human-verification template next to the
artifacts and exits non-zero unless the ledger reconciled cleanly.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from app.services.review_store import ReviewStore
from app.stage9 import smoke
from app.stage9.budget import BudgetLedger
from app.stage9.evaluation import DEFAULT_ANSWER_KEY, load_answer_key
from app.stage9.scoring import CandidateVerification, Relevance, Verifications

RUNS: dict[str, list[str]] = {"A": ["SB-001", "SB-028", "SB-017"], "B": ["SB-030", "SB-090", "SB-055"]}
RUN_CAP_USD = Decimal("0.75")
RUN_DISCOVERY = True


def configured_domains(settings) -> list[str]:
    from app.services.catalogue import load_catalogue
    from app.services.domains import registrable_domain

    cat = load_catalogue(settings.catalogue_path, settings.catalogue_products_sheet, settings.catalogue_competitors_sheet)
    return sorted({registrable_domain(c.domain) for c in cat.competitors})


def run_settings(paths: smoke.Paths, prices: dict[str, Decimal], **overrides):
    values = {**overrides, "pipeline_search_auto_approved": False}  # forced last: discovered domains stay unsearched
    return smoke.smoke_settings(paths, prices, **values).model_copy(
        update={"stage9_max_reservation_per_run_usd": RUN_CAP_USD})


def existing_artifacts(paths: smoke.Paths, run_name: str) -> list[Path]:
    if not paths.runs.is_dir():
        return []
    return sorted(p for p in paths.runs.iterdir() if p.is_dir() and f"-run{run_name}-" in p.name)


def preflight(run_name: str, paths: smoke.Paths, prices: dict[str, Decimal], settings,
              answer_key: Path = DEFAULT_ANSWER_KEY) -> list[str]:  # fmt: skip
    problems = smoke.preflight(paths, prices, settings, cap=RUN_CAP_USD)
    if run_name not in RUNS:
        return problems + [f"unknown run {run_name!r}; approved runs are {sorted(RUNS)}"]
    key = load_answer_key(answer_key)
    if not key.status.startswith("APPROVED"):
        problems.append("the answer key is not approved")
    planned = next((r for r in key.planned_runs if r.name == run_name), None)
    if planned is None or planned.product_ids != RUNS[run_name] or planned.run_discovery is not RUN_DISCOVERY:
        problems.append(f"run {run_name} does not match the answer key's planned run")
    if settings.pipeline_search_auto_approved:
        problems.append("automatic search must be off")
    if not settings.llm1_model or not settings.discovery_use_llm:
        problems.append("LLM 1 is not configured for discovery (LLM1_MODEL / DISCOVERY_USE_LLM)")
    if len(RUNS[run_name]) > settings.pipeline_max_products_per_run:
        problems.append("more products than PIPELINE_MAX_PRODUCTS_PER_RUN")
    if paths.review_db.is_file():
        decided = ReviewStore(paths.review_db, settings.discovery_excluded_competitor_domains).list_decisions()
        if decided:
            problems.append(f"the evaluation database already has {len(decided)} reviewer decision(s); decisions are "
                            "applied only after both initial runs")  # fmt: skip
    if existing_artifacts(paths, run_name):
        problems.append(f"run {run_name} already has artifacts ({existing_artifacts(paths, run_name)[0].name}); "
                        "it is not repeated")  # fmt: skip
    domains = configured_domains(settings)
    if len(domains) != 13:
        problems.append(f"expected the 13 configured retailers, found {len(domains)}")
    return problems


def verification_template(result: dict) -> Verifications:
    """A Verifications file for the reviewer: every discovered domain and every candidate, all UNKNOWN."""
    domains = {c["domain"]: Relevance.UNKNOWN for c in ((result.get("discovery") or {}).get("candidates") or [])}
    cands = [CandidateVerification(product_url=c["product_url"])
             for p in result.get("products") or [] for c in ((p.get("matching") or {}).get("candidates") or [])]  # fmt: skip
    return Verifications(domain_relevance=domains, candidates=cands)


def post_run_checks(result: dict, domains: list[str]) -> list[str]:
    problems = []
    targets = sorted(t["domain"] for t in result.get("search_targets") or [])
    if targets != domains:
        problems.append(f"search targets were {targets}, not the 13 configured retailers")
    searched = {d["domain"] for p in result.get("products") or [] for d in ((p.get("intelligence") or {}).get("domains") or [])
                if d.get("queries")}  # fmt: skip
    if searched - set(domains):
        problems.append(f"searched outside the configured retailers: {sorted(searched - set(domains))}")
    return problems


def main(argv: list[str] | None = None, *, settings_overrides: dict | None = None, prepare_app=None,
         answer_key: Path = DEFAULT_ANSWER_KEY) -> int:  # fmt: skip
    ap = argparse.ArgumentParser(description="Stage 9.2 initial run A or B (discovery on, 13 configured retailers, $0.75 cap)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "run"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True, choices=sorted(RUNS))
        p.add_argument("--dir", type=Path, required=True)
        for flag in ("--exa-search-usd", "--exa-page-usd", "--llm-input-usd-per-token", "--llm-output-usd-per-token"):
            p.add_argument(flag, type=Decimal, required=True)
    args = ap.parse_args(argv)
    paths = smoke.Paths(args.dir)
    prices = smoke._prices(args)
    settings = run_settings(paths, prices, **(settings_overrides or {}))
    problems = preflight(args.run, paths, prices, settings, answer_key)
    domains = configured_domains(settings)
    report = {"run": args.run, "scope": {"product_ids": RUNS[args.run], "run_discovery": RUN_DISCOVERY,
                                         "domains": domains, "automatic_search": False},
              "cap_usd": str(RUN_CAP_USD), "problems": problems}  # fmt: skip
    if not [p for p in problems if p.startswith("price ")]:
        from app.stage9.budget import UnitPrices, estimate_run

        est = estimate_run(settings, UnitPrices.from_settings(settings), products=len(RUNS[args.run]),
                           run_discovery=RUN_DISCOVERY, max_targets=len(domains), llm1=True, llm2=True)  # fmt: skip
        report["worst_case"] = {"exa_searches": est.exa_searches, "exa_pages": est.exa_pages,
                                "llm1_calls": est.llm1_calls, "llm2_calls": est.llm2_calls,
                                "worst_case_usd": str(est.worst_case_usd),
                                "reservation_usd": str(min(est.worst_case_usd, RUN_CAP_USD))}  # fmt: skip
    print(json.dumps(report, indent=1))
    if problems:
        print("NOT READY: the run will not start.", file=sys.stderr)
        return 2
    if args.cmd == "check":
        print("Preflight passed (nothing was sent).")
        return 0
    from app.stage9.harness import ScenarioRun, run_scenario

    cfg = ScenarioRun(label=f"run{args.run}", product_ids=RUNS[args.run], review_db=paths.review_db,
                      ledger=paths.ledger, output_dir=paths.runs, run_discovery=RUN_DISCOVERY, domains=domains,
                      answer_key=answer_key)  # fmt: skip
    res = run_scenario(cfg, settings=settings, prepare_app=prepare_app)
    entry = BudgetLedger(paths.ledger).run(res.run_id) if res.run_id else None
    result = json.loads((Path(res.artifact_dir) / "result.json").read_text(encoding="utf-8")) or {}
    after = post_run_checks(result, domains) if res.run_id else []
    template = verification_template(result)
    (Path(res.artifact_dir) / "verifications.template.json").write_text(
        template.model_dump_json(indent=1), encoding="utf-8")  # fmt: skip
    print(json.dumps({"run_id": res.run_id, "status": res.status, "http_status": res.http_status,
                      "artifacts": str(res.artifact_dir), "ledger_status": entry and entry["status"],
                      "charged_exa_usd": entry and entry["charged_exa_usd"],
                      "charged_openrouter_usd": entry and entry["charged_openrouter_usd"],
                      "post_run_problems": after}, indent=1, default=str))  # fmt: skip
    if entry is None or entry["status"] != "RECONCILED" or after:
        print("STOP: resolve this before any further Stage 9 run.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
