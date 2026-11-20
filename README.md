# Stompbox Competitor Intelligence

Live AI-assisted competitor intelligence demo for Stompbox. See [CLAUDE.md](CLAUDE.md)
for the full plan, rules and phase breakdown.

**Status:** Phases 1–7 complete (catalogue, search, competitor discovery with
LLM 1, competitor domain review, product intelligence with LLM 2, deterministic
matching and price validation, end-to-end pipeline runs with progress events and
cancellation). Phase 8 (live dashboard) is implemented. UK relevance uses verified
evidence quotes (Option B; the separate UK eligibility gate is retired). The
three-task Product Intelligence Agent was evaluated live twice on 19 Nov 2026
(Stage 6 and 6.2, two products on two retailers), and Stage 6.3 pre-demo hardening
(20 Nov 2026) is awaiting review before the next checkpoint. Phase 9 (testing and
demo preparation) has not started. Every stage runs without OpenRouter credentials
(no-LLM mode); LLM 1/LLM 2 activate when `OPENROUTER_API_KEY` and
`LLM1_MODEL`/`LLM2_MODEL` are set (accuracy and cost at scale not yet evaluated).
SB-028 is the approved Les Paul Standard '60s Figured LH; its price baseline
(£579, recorded as a sale price) still awaits confirmation (see
[docs/product-matching.md](docs/product-matching.md)). Search and retrieval provider: **Exa** (Tavily
optional, no automatic fallback; Brave disabled). See
[docs/search-provider-evaluation.md](docs/search-provider-evaluation.md) and
[docs/competitor-discovery.md](docs/competitor-discovery.md),
[docs/competitor-review.md](docs/competitor-review.md),
[docs/product-intelligence.md](docs/product-intelligence.md),
[docs/product-matching.md](docs/product-matching.md) and
[docs/pipeline.md](docs/pipeline.md).

## Layout

```text
backend/            FastAPI + Pydantic service
  app/
    api/            HTTP routes
    core/           settings (environment-driven)
    schemas/        Pydantic models and shared status enums
    services/       catalogue/competitor loading, domain + URL normalisation, text matching,
                    review_store (SQLite review decisions), prices (price-text parsing)
      matching/     Phase 6 deterministic matching + price validation (+ CLI)
    orchestration/  Phase 7 pipeline runs: stages, in-memory run store, events
      search/       provider interface + Exa, Tavily, Brave adapters
      llm/          LLM interface + OpenRouter client (strict structured output)
    agents/
      discovery/    LLM 1 competitor discovery agent, rules, prompts, CLI
      intelligence/ LLM 2 product intelligence agent, evidence checks, prompts, CLI
    benchmark/      search-provider benchmark (CLI)
    main.py         app factory; loads and validates the catalogue at startup
  tests/            pytest suite (no network; HTTP is mocked)
frontend/           React + Vite + TypeScript live dashboard (Phase 8) and domain review
data/catalogue/     Stompbox 100-product workbook (source of products AND competitors)
data/benchmark_runs/  raw benchmark output (git-ignored)
data/discovery_runs/  discovery CLI output with evidence (git-ignored)
data/review/        competitor review decisions, SQLite (git-ignored)
data/intelligence_runs/  product intelligence CLI output (git-ignored)
data/matching_runs/ matching CLI output (git-ignored)
docs/               evaluation reports and benchmark summaries
```

## Requirements

- Python 3.13
- Node.js 24 / npm 11

## Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows; use `source .venv/bin/activate` elsewhere
pip install -r requirements-dev.txt
cp .env.example .env            # add EXA_API_KEY; OPENROUTER_API_KEY + LLM1_MODEL are optional
uvicorn app.main:app --reload   # http://127.0.0.1:8000, docs at /docs
pytest
```

The app refuses to start if the workbook is missing or any row fails validation;
the error lists each offending row.

### Configuration

All settings come from environment variables or `backend/.env` (git-ignored). See
[backend/.env.example](backend/.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `CATALOGUE_PATH` | `data/catalogue/Stompbox_100_Product_Catalogue_No_Sale_Pricing.xlsx` | Catalogue workbook |
| `CATALOGUE_PRODUCTS_SHEET` | `Products` | Product sheet name |
| `CATALOGUE_COMPETITORS_SHEET` | `Competitors` | Competitor sheet name |
| `SEARCH_PROVIDER` | unset (set `exa`) | Active provider. `exa` is the chosen provider; `tavily` is optional; `brave` is disabled |
| `EXA_API_KEY`, `TAVILY_API_KEY`, `BRAVE_SEARCH_API_KEY` | unset | Provider keys (held as `SecretStr`, never logged) |
| `SEARCH_TIMEOUT_SECONDS` / `CONTENT_TIMEOUT_SECONDS` | `20` / `45` | Per-request timeouts |
| `SEARCH_MAX_RETRIES` | `2` | Retries on 429/5xx/timeouts (honours `Retry-After`) |
| `OPENROUTER_API_KEY` | unset | OpenRouter key (`SecretStr`) |
| `LLM1_MODEL` | unset | LLM 1 model id on OpenRouter; must support structured outputs. With the key, enables `LLM_ENABLED` discovery. Chosen: `openai/gpt-6-luna` |
| `LLM1_TEMPERATURE` / `LLM2_TEMPERATURE` | unset | Sent only when set (an explicit `0` is sent). Leave unset for models that reject it, such as reasoning models |
| `LLM1_REASONING_EFFORT` / `LLM2_REASONING_EFFORT` | unset | `none`, `minimal`, `low`, `medium`, `high` or `xhigh`; sent as OpenRouter `reasoning.effort` (reasoning text excluded). Configured: `low` |
| `LLM1_MAX_COMPLETION_TOKENS` / `LLM2_MAX_COMPLETION_TOKENS` | `6000` / `3000` | Output budget, including reasoning tokens. Configured: `12000` / `8000` |
| `DISCOVERY_USE_LLM` | `true` | `false` forces `LLM_DISABLED` discovery even when the LLM is configured |
| `DISCOVERY_AUTO_APPROVE_MIN_CONFIDENCE` / `_MIN_RELEVANCE` | `0.85` / `0.75` | Auto-approval thresholds (uncalibrated starting values) |
| `REVIEW_DB_PATH` | `data/review/competitor_review.sqlite3` | Persistent competitor review decisions |
| `LLM2_MODEL` | unset | LLM 2 (extraction) model id on OpenRouter; independent of `LLM1_MODEL`. With the key, enables `LLM_ENABLED` product intelligence. Chosen: `openai/gpt-6-luna` |
| `INTELLIGENCE_USE_LLM` | `true` | `false` forces `LLM_DISABLED` product intelligence |
| `INTELLIGENCE_MAX_PAGES_PER_DOMAIN` / `_PER_PRODUCT` | `2` / `20` | Page retrieval and extraction caps |
| `MATCHING_PRICE_MAX_AGE_HOURS` | `24` | Older price evidence is `STALE` and never compared |
| `PIPELINE_MAX_PRODUCTS_PER_RUN` / `PIPELINE_MAX_ACTIVE_RUNS` | `5` / `2` | Run limits |
| `PIPELINE_RUN_TIMEOUT_SECONDS` / `_DISCOVERY_` / `_PRODUCT_` | `1800` / `300` / `600` | Timeouts (run, discovery stage, per product) |
| `PIPELINE_RUN_TTL_MINUTES` / `PIPELINE_MAX_RUNS_RETAINED` | `120` / `50` | In-memory retention of finished runs |
| `PIPELINE_SEARCH_AUTO_APPROVED` | `true` | Search domains LLM 1 auto-approved in the same run (never persisted) |
| `DISCOVERY_EXCLUDED_COMPETITOR_DOMAINS` | `["pmtonline.co.uk"]` | Competitors removed by the user (PMT): excluded from discovery and never written to the review queue |
| `MARKETPLACE_DOMAIN_LABELS` | `["amazon","ebay","reverb","etsy","gumtree"]` | Never auto-approved; can only be approved as `marketplace` |

Provider and discovery tuning options are listed in `.env.example`. Never commit keys.

### Search providers

`app/services/search` defines `SearchProvider` (`search(query)`, `retrieve(urls)`)
and the provider-neutral schemas in `app/schemas/search.py`. Use
`get_configured_provider(settings)` to get the active one. Domain scope is
enforced deterministically after every search, whatever the provider's own
filter returns. Brave cannot retrieve page content.

```bash
python -m app.benchmark.search_benchmark --dry-run   # plan only
python -m app.benchmark.search_benchmark             # Exa + Tavily (default)
python -m app.benchmark.search_benchmark --rescore <run_id>   # re-score saved raw data, no API calls
```

### Competitor discovery (Phase 3)

```bash
python -m app.agents.discovery.cli SB-001 SB-044   # summary + full result in data/discovery_runs/
```

See [docs/competitor-discovery.md](docs/competitor-discovery.md) for the flow and approval rules.

### Product intelligence (Phase 5)

```bash
python -m app.agents.intelligence.cli SB-001 --domains andertons.co.uk gear4music.com
```

See [docs/product-intelligence.md](docs/product-intelligence.md).

### Product matching (Phase 6)

```bash
python -m app.services.matching.cli data/intelligence_runs/<run_id>.json   # no API calls
```

See [docs/product-matching.md](docs/product-matching.md).

### API

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Status plus loaded product/competitor counts |
| GET | `/api/catalogue/products` | List products. Optional `q` (all terms must match name/brand/model/SKU/keywords), `category`, `subcategory`, `brand` (case-insensitive exact) |
| GET | `/api/catalogue/products/{product_id}` | Single product, 404 if unknown |
| POST | `/api/catalogue/products/lookup` | Body `{"product_ids": [...]}` (1–100). Returns products in request order, de-duplicated. Any unknown ID returns 404 with `missing_ids` |
| GET | `/api/catalogue/categories` | Category/subcategory product counts |
| GET | `/api/competitors` | Configured competitor domains |
| GET | `/api/discovery/config` | Discovery mode (`LLM_ENABLED`/`LLM_DISABLED`), reason when disabled, thresholds. No secrets |
| POST | `/api/discovery/runs` | Body `{"product_ids": [...]}` (1–10). Runs competitor discovery and returns mode, candidates, evidence, decisions and targeted queries. The run is not stored; its candidates enter the review queue and saved decisions are applied. 503 only if the search provider is not configured |
| GET | `/api/review/candidates?status=` | Review queue (`PENDING`/`APPROVED`/`REJECTED`) |
| GET | `/api/review/candidates/{domain}` | Evidence, automatic reasons and decision history |
| POST | `/api/review/candidates/{domain}/approve`, `.../reject` | Record a decision (201; 409 if one exists) |
| PUT | `/api/review/decisions/{domain}` | Explicitly change a decision (versioned) |
| GET | `/api/review/decisions?decision=` | Current decisions |
| GET | `/api/intelligence/config` | Product intelligence mode, LLM 2 model, search targets and skipped targets. No secrets |
| POST | `/api/runs` | Start a pipeline run: body `{"product_ids": [...] (1–5), "run_discovery": true, "domains": [...]?}`; 202 |
| GET | `/api/runs`, `/api/runs/{id}` | Run list / state: status, stages, progress, summary (in memory, temporary) |
| GET | `/api/runs/{id}/result` | Consolidated results (filled progressively) |
| GET | `/api/runs/{id}/events?after=` / `/stream` | Progress events as JSON / Server-Sent Events (resumable via `Last-Event-ID`) |
| POST | `/api/runs/{id}/cancel` | Cancel an active run (202; 409 if finished) |
| POST | `/api/matching/evaluate` | Body: a Phase 5 `IntelligenceRunResult`. Returns match status, variant/condition checks, price validation and comparison eligibility per candidate. Deterministic, no key, not stored |
| POST | `/api/intelligence/runs` | Body `{"product_ids": [...] (1–5), "domains": [...]?}`. Searches approved domains, retrieves pages, extracts evidence-checked product fields. Not stored. No match decisions (Phase 6) |

Prices are serialised as decimal strings with two decimal places (`"189.99"`) to
avoid float rounding.

## Frontend

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, proxies /api to the backend on :8000
npm run build
npm run lint
npm test         # Vitest + Testing Library (jsdom); no backend needed
```

Four pages, in a sidebar:

- **Overview:** KPIs, match status, competitor coverage, eligible price
  comparisons and products without an exact match, for the selected run.
- **Product Intelligence:** the product reference and a competitor comparison
  table with expandable source, identity and price evidence. Differences are
  shown only when the backend marks the comparison eligible.
- **Live Runs:** select up to 5 products, start a run, and follow the 7 pipeline
  stages, per-product progress and the event timeline over SSE. Cancel needs
  confirmation.
- **Domain Review:** pending, approved and rejected domains (the Phase 4
  workflow), the configured competitors, and excluded or skipped domains.
  Approvals apply to the next run.

See [docs/dashboard.md](docs/dashboard.md). Start the backend first. The dev
server proxies `/api` to `127.0.0.1:8000`.

## Data notes

- **Products:** 100 rows from the workbook's `Products` sheet, mapped 1:1 onto
  `StompboxProduct`. The research and audit columns (`verification_status`,
  `source_type`, `evidence_url`, `last_checked`, price audit notes, etc.) are kept
  verbatim under `provenance`.
- **Prices:** observed Stompbox snapshot from 2026-11-09, not live. Missing prices
  stay `null`; they are never set to 0.
- **`stompbox_sku`:** may hold WooCommerce internal IDs (`child-6768`). These are
  not manufacturer model numbers and must not be used for exact matching.
- **Competitors:** 13 rows from the workbook's `Competitors` sheet, deduplicated by
  normalised hostname (lowercase, `www.` stripped). The free-text `classification`
  is kept as-is and mapped to a `company_type` enum through an explicit table, so an
  unknown classification is a load error, not a guess. `Reference only` entries
  (Gibson) get `price_benchmark=false`.
