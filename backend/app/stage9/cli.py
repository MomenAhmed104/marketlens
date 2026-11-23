"""Stage 9 budget ledger commands. None of them call a paid provider.

    python -m app.stage9.cli init --ledger PATH --ceiling 3.00 [--note TEXT]
    python -m app.stage9.cli status --ledger PATH
    python -m app.stage9.cli show --ledger PATH RUN_ID
    python -m app.stage9.cli estimate --products 2 --targets 15 [--no-discovery]   (uses STAGE9_* prices)
    python -m app.stage9.cli resolve --ledger PATH RUN_ID --exa USD --openrouter USD --note TEXT

`estimate` reads unit prices and pipeline limits from the environment / backend/.env and prints
the worst case and the reservation a run would take; it reads no database.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from app.stage9.budget import BudgetLedger, LedgerError, UnitPrices, estimate_run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage 9 budget ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--ceiling", type=Decimal, required=True)
    p.add_argument("--note", default="")
    p = sub.add_parser("status")
    p.add_argument("--ledger", type=Path, required=True)
    p = sub.add_parser("show")
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("run_id")
    p = sub.add_parser("estimate")
    p.add_argument("--products", type=int, required=True)
    p.add_argument("--targets", type=int, required=True, help="most domains the run can search")
    p.add_argument("--no-discovery", action="store_true")
    p.add_argument("--no-llm", action="store_true")
    p = sub.add_parser("resolve")
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("run_id")
    p.add_argument("--exa", type=Decimal, required=True)
    p.add_argument("--openrouter", type=Decimal, required=True)
    p.add_argument("--note", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "init":
            led = BudgetLedger.create(args.ledger, args.ceiling, args.note)
            print(f"Created {led.path} with a ceiling of {led.ceiling_usd} USD")
        elif args.cmd == "status":
            s = BudgetLedger(args.ledger).summary()
            print(f"Ceiling {s.ceiling_usd} USD, committed {s.committed_usd} USD, remaining {s.remaining_usd} USD")
            print(f"Runs by status: {s.runs or 'none'}")
            for label, ids in (("Open reservations", s.open_reservations), ("Needs review", s.needs_review)):
                if ids:
                    print(f"{label}: {', '.join(ids)}")
        elif args.cmd == "show":
            led = BudgetLedger(args.ledger)
            run = led.run(args.run_id)
            if run is None:
                print(f"No ledger entry for {args.run_id}", file=sys.stderr)
                return 1
            print(json.dumps({**run, "attempts": led.attempts(args.run_id)}, indent=1, default=str))
        elif args.cmd == "estimate":
            from app.core.config import Settings

            s = Settings()
            missing = UnitPrices.missing(s)
            if missing:
                print(f"Unit prices not configured: {', '.join(missing)}", file=sys.stderr)
                return 2
            est = estimate_run(s, UnitPrices.from_settings(s), products=args.products,
                               run_discovery=not args.no_discovery, max_targets=args.targets,
                               llm1=not args.no_llm, llm2=not args.no_llm)  # fmt: skip
            cap = s.stage9_max_reservation_per_run_usd
            print(json.dumps(json.loads(est.to_json()), indent=1))
            print(f"Reservation: {est.worst_case_usd if cap is None else min(est.worst_case_usd, cap)} USD"
                  + ("" if cap is None else f" (capped by STAGE9_MAX_RESERVATION_PER_RUN_USD={cap})"))  # fmt: skip
        elif args.cmd == "resolve":
            BudgetLedger(args.ledger).resolve(args.run_id, args.exa, args.openrouter, args.note)
            print(f"Resolved {args.run_id}")
    except LedgerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
