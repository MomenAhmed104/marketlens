# Stage 9 spending control

Stage 9 runs the real pipeline against paid providers (Exa, OpenRouter). This mechanism makes
sure no Stage 9 run can spend more than it reserved, and no set of runs more than the Stage 9
ceiling. It is off unless `STAGE9_LEDGER_PATH` is set; with it off, nothing changes.

## Architecture (audited 23 Nov 2026, commit `c3a56fc`)

- `POST /api/runs` → `RunManager.start` validates the request and starts `PipelineRun.execute`
  as an asyncio task. Stages: discovery (LLM 1 + Exa), review queue, targets, product search
  (Exa search + Exa contents), extraction (LLM 2), matching (no paid calls).
- The agents get their paid clients from exactly two builders: `services.search.build_provider`
  (Exa, Tavily, Brave) and `services.llm._build` (OpenRouter). Both wrap an `httpx.AsyncClient`
  in `ProviderHttpClient`, which does the transport retries (`SEARCH_MAX_RETRIES`,
  `LLM_MAX_RETRIES`, default 2 each). `OpenRouterClient` adds one repair attempt per call.
- Cost reporting: Exa returns `costDollars.total` per response; OpenRouter returns `usage.cost`
  per response. Either can be missing. The agents add them up into `DiscoveryUsage` and
  `IntelligenceUsage` on the run result. Failed attempts (timeouts, 5xx) report nothing.
- Run state is in memory (`InMemoryRunStore`), lost on restart. Saved run files are written
  only by the CLIs and by hand (`data/intelligence_runs/`).
- Images use their own free HTTP client (`services/images/net.py`), not the paid builders; they
  are not metered and cost nothing.

**Integration point:** the two paid-client builders (every paid HTTP attempt passes through
`ProviderHttpClient`), plus `RunManager.start` (the only way a dashboard run starts).

## How it works

1. **Ledger** (`app/stage9/budget.py`, `BudgetLedger`): its own SQLite file at
   `STAGE9_LEDGER_PATH`, created only by `python -m app.stage9.cli init`. Never the review
   database. Tables: `meta` (ceiling), `runs` (reservation, status, reported / charged / pipeline
   costs per provider, unreported and refused attempt counts, notes), `attempts` (every HTTP
   attempt: provider, endpoint, bound, reported cost, charged cost, status, outcome) and
   `history`. Every write is an `IMMEDIATE` transaction, so concurrent runs or processes cannot
   reserve the same budget. It survives restarts.
2. **Reservation before start** (`app/stage9/manager.py`, `BudgetedRunManager`, used instead of
   `RunManager` when the ledger path is set): normal validation, then refuse (503) if the ledger
   is missing, a unit price is unset, or the search provider is not Exa; compute the run's worst
   case (`estimate_run`) and reserve `min(worst case, STAGE9_MAX_RESERVATION_PER_RUN_USD)`. If
   that does not fit the remaining budget: 402, recorded as `REFUSED`, nothing starts. The
   reservation is committed before the run's task exists.
3. **Hard cap during the run** (`app/stage9/meter.py`, `RunMeter`): attached to every paid client
   the run creates (through a ContextVar the run's task inherits). Before each HTTP attempt,
   retries included, it computes that attempt's worst case and refuses it (nothing is sent) if
   charges so far + attempts in flight + this one would exceed the reservation, or if the cost
   cannot be bounded. A refusal is a `ProviderError` with status 402: the agents record a failed
   call and the run ends normally with partial results (LLM 2 stops after the first one).
4. **Reconciliation after the run**: when the task ends (any terminal state), attempts still
   without a response are charged at their bound, then the ledger compares metered charges with
   the reservation and with the costs the pipeline recorded. `RECONCILED` if everything was
   reported and agrees; otherwise `NEEDS_REVIEW` with notes.
5. **Fail closed outside runs**: in budget mode, any paid client created outside a budgeted run
   (standalone `/api/discovery/runs`, `/api/intelligence/runs`, the CLIs, the benchmark, the
   classification backfill) is refused when it is built.

## Worst-case bounds (no invented prices)

The unit prices have **no defaults**. They must be the providers' maximum prices, confirmed by
the user; while any is unset, every run is refused.

| Attempt | Bound |
|---|---|
| Exa `/search` | `STAGE9_EXA_USD_PER_SEARCH_MAX`; refused if `numResults` > `STAGE9_EXA_SEARCH_MAX_RESULTS` (default 10) |
| Exa `/contents` | `STAGE9_EXA_USD_PER_PAGE_MAX` × number of URLs |
| OpenRouter `/chat/completions` | `STAGE9_LLM_USD_PER_INPUT_TOKEN_MAX` × (request bytes + `STAGE9_LLM_TOKEN_OVERHEAD`) + `STAGE9_LLM_USD_PER_OUTPUT_TOKEN_MAX` × `max_completion_tokens`; refused above `STAGE9_LLM_MAX_REQUEST_BYTES` (default 160,000) or without `max_completion_tokens` |
| Any other host or endpoint | refused |

- Input tokens ≤ request bytes holds for byte-level tokenizers (each token covers at least one
  byte); `STAGE9_LLM_TOKEN_OVERHEAD` (default 1,000) covers chat framing. Output tokens,
  reasoning included, are capped by `max_completion_tokens`. **Assumption to confirm:** that the
  routed model's tokenizer is byte-level and that OpenRouter applies `max_completion_tokens` to
  reasoning tokens (the existing truncation handling already relies on the latter).
- OpenRouter may route to several upstream providers: the per-token maxima must cover the most
  expensive one allowed for the model.
- Charges: the provider-reported cost when a 2xx response reports one; otherwise (error
  response, missing field, timeout, connection error) the full bound. Failed attempts are
  therefore always counted as if billed.
- A run's worst case (`estimate_run`): discovery `products × (max_global + 1)` searches + one
  profile search per candidate (`DISCOVERY_MAX_CANDIDATES`); LLM 1 `1 + ceil(candidates / batch)`
  calls; product search `products × max_targets × queries_per_domain` searches; pages
  `products × min(max_pages_per_product, max_targets × max_pages_per_domain)`; LLM 2 two calls
  per page (extraction + match proposal). Each × the attempts a call can take: Exa
  `SEARCH_MAX_RETRIES + 1`; OpenRouter `(LLM_MAX_RETRIES + 1) × 2`. `max_targets` = configured +
  reviewer-approved + `MAX_AUTO_SEARCH_TARGETS` when automatic search is on (or the requested
  domain subset, if smaller).
- The full worst case is deliberately pessimistic (every call retried, every LLM request at the
  maximum size). `STAGE9_MAX_RESERVATION_PER_RUN_USD` reserves less; the meter then stops paid
  calls when the reservation is used up.

## Remaining limits

- **Price correctness is the operator's responsibility.** If a provider bills more than the
  configured maximum, the meter cannot know in advance; reconciliation flags it (`NEEDS_REVIEW`,
  "cost more than their configured maximum").
- **Provider billing visibility:** whether Exa or OpenRouter bill timed-out requests is unknown;
  such attempts are charged at their bound. Reconcile against the provider dashboards before
  closing Stage 9 (`cli resolve` for interrupted or NEEDS_REVIEW runs).
- **Interrupted runs** (process stopped mid-run) stay `RESERVED` and count in full until
  resolved by hand.
- **Not covered:** spending through any process that does not set `STAGE9_LEDGER_PATH` (for
  example, a dev server already running without it). Every Stage 9 run must go through a backend
  started with budget mode on.
- Ledger writes are synchronous SQLite calls inside the HTTP hooks (milliseconds; acceptable for
  Stage 9 volumes).
- The ledger file location is the operator's choice; a `data/stage9/` folder is not git-ignored
  yet (proposed, not changed).

## Observed costs (reference only, not prices)

From saved runs (read-only): Exa about $0.007 per search ($0.175 for 25 discovery searches,
$0.028 for 4 product searches) and $0.001 per retrieved page ($0.008 for 8); LLM 2 $0.0098 for
16 calls (58,850 prompt + 7,609 completion tokens); LLM 1 $0.0037 for 3 calls. These are
averages, not maxima, and must not be used as `STAGE9_*` prices without checking the providers'
price lists.

## Commands

```
cd backend
python -m app.stage9.cli init --ledger <path> --ceiling 3.00 --note "Stage 9"
python -m app.stage9.cli estimate --products 2 --targets 15          # uses STAGE9_* prices; no DB
python -m app.stage9.cli status --ledger <path>
python -m app.stage9.cli show --ledger <path> <run_id>
python -m app.stage9.cli resolve --ledger <path> <run_id> --exa 0.12 --openrouter 0.01 --note "dashboards"
```

Tests: `tests/test_stage9_budget.py` (mocked HTTP, test-only prices, no network).
