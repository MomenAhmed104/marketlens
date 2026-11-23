"""Stage 9 preparation: spending control for live end-to-end validation.

- `budget`: unit prices (configured, no defaults), a run's worst case, the persistent ledger;
- `meter`: the per-run HTTP meter that caps every Exa / OpenRouter attempt at the reservation;
- `context`: how paid-client builders find the active run's meter (and refuse without one);
- `manager`: `BudgetedRunManager`, used instead of RunManager when STAGE9_LEDGER_PATH is set;
- `cli`: ledger commands (init, status, show, estimate, resolve);
- `evaluation`: scenario and answer-key schema; `isolated_db`: evaluation review databases;
- `harness`: one scenario through POST /api/runs with artifacts; `scoring`: scorecards.

See docs/stage9-budget.md and docs/stage9/README.md (evaluation: answer key, harness, scoring,
isolated databases).
"""
