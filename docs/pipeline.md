# Pipeline orchestration, Phase 7

Implemented in `backend/app/orchestration/`. The API is in `app/api/runs.py`.
One run takes 1–5 selected Stompbox products through every phase, reusing the
existing agents and services. Orchestration only sequences them, isolates
failures, tracks progress and emits events. No phase logic is duplicated.

## Stages

| # | Stage | Reuses | Notes |
|---|---|---|---|
| 1 | `validate` | `catalogue.lookup`, `configured_providers`, `eligible_search_targets` | Runs **before** the run exists. Product IDs are trimmed, upper-cased and de-duplicated. Unknown ID → 404. Too many → 422. `domains` not configured/approved → 422. No search provider → 503. Too many active runs → 429 |
| 2 | `discovery` | Phase 3 `build_discovery_agent` (with saved review decisions) | LLM 1 when configured, otherwise the no-LLM baseline. Can be turned off per run (`run_discovery: false` → SKIPPED) |
| 3 | `review_queue` | Phase 4 `ReviewStore.record_run` | New candidates are saved as **PENDING**. This is the run's only durable side effect, identical to a standalone discovery run |
| 4 | `targets` | Phase 5 `eligible_search_targets` | See "Which domains are searched" below |
| 5 | `product_search` | Phase 5 `ProductIntelligenceAgent.run_product` | Product by product: domain-restricted search and page retrieval |
| 6 | `extraction` | Same agent (LLM 2) | **SKIPPED** without LLM 2: retrieved pages are `SKIPPED_NO_LLM` |
| 7 | `matching` | Phase 6 `evaluate_product` | Runs right after each product's search, so results arrive progressively |

### Which domains are searched

- **Searched:**
  - the 13 configured competitors;
  - reviewer-`APPROVED` domains (Phase 4);
  - domains LLM 1 auto-approved **in this run** (LLM_ENABLED only). These are never persisted, and `PIPELINE_SEARCH_AUTO_APPROVED=false` turns them off.
- **Never searched:**
  - pending domains (including those just discovered);
  - rejected domains;
  - approved `directory` domains.
- **Reviewer decisions win:** a reviewer rejection overrides an LLM
  auto-approval, because discovery applies saved decisions.
- **Benchmark eligibility is unchanged:** Gibson and approved marketplaces are
  searched but never used as price benchmarks.
- **Narrowing a run:** a `domains` subset limits a run to some of the eligible
  targets.

## Status model

- **Run:** `PENDING`, `RUNNING`, `COMPLETED`, `PARTIALLY_COMPLETED`, `FAILED`, `CANCELLED`.
  - `COMPLETED`: every stage completed or was deliberately skipped (e.g. no LLM 2).
  - `PARTIALLY_COMPLETED`: at least one product completed, but a stage was
    `FAILED`/`PARTIAL` or a product failed.
  - `FAILED`: no product completed (`error` explains why).
- **Stage:** `PENDING`, `RUNNING`, `COMPLETED`, `PARTIAL`, `SKIPPED`,
  `FAILED`, `CANCELLED`. Each stage records `done`/`total`, a UI-safe
  `message` (including why it was skipped) and `errors`.
- **Product:** `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, plus
  its Phase 5 `intelligence` and Phase 6 `matching` results.

## Reliability

| Failure | Effect |
|---|---|
| Discovery fails or times out (`PIPELINE_DISCOVERY_TIMEOUT_SECONDS`, 300) | Discovery `FAILED`, review queue `SKIPPED`. Product search continues on configured and approved domains. Run `PARTIALLY_COMPLETED` |
| One competitor's search fails | Recorded by the Phase 5 agent for that domain. Other domains continue. Product search `PARTIAL` |
| Page blocked or failed | That candidate is `SKIPPED_NOT_RETRIEVED`. Matching needs review or finds no match |
| LLM 2 fails on some pages | Extraction `PARTIAL`/`FAILED`. Matching still runs on title and URL evidence |
| One product fails or times out (`PIPELINE_PRODUCT_TIMEOUT_SECONDS`, 600) | That product `FAILED`. The next product continues |
| The product search agent cannot start | All products `FAILED`. Run `FAILED`. Discovery results are kept |
| Whole run exceeds `PIPELINE_RUN_TIMEOUT_SECONDS` (1800) | The running stage `FAILED`. Partial results kept |
| Review queue write fails | Review queue `FAILED`. The run continues |

**Retries:** transient 429, 5xx and timeout errors are retried by the shared
provider HTTP client (`SEARCH_MAX_RETRIES`, `LLM_MAX_RETRIES`, honouring
`Retry-After`). The pipeline does not add another retry layer, so a failing
provider is not hammered. Auth and credit errors are not retried. LLM 2 stops
after an account-level error (Phase 5).

**Unexpected exceptions:** these are logged server-side. The run reports only
the exception type, never a traceback or secrets. Agents' HTTP clients are
always closed, including on cancellation.

## Progress and cancellation

- **Events:** every state change appends an `ExecutionEvent`. The envelope
  holds `seq` (1-based, contiguous), `event_id` (`<run>:<seq>`), `run_id`,
  `type`, `timestamp`, `product_id`, `stage`, a UI-safe `message` and a small
  `payload`. Payloads never contain secrets or page text.
- **Event types:**
  - lifecycle: `run_started`, `run_completed`, `run_failed`, `run_cancelled`;
  - stages: `stage_started`, `stage_completed`;
  - discovery: `product_loaded`, `competitor_discovered`, `domain_auto_approved`, `domain_approval_required`, `known_domains_loaded`;
  - product search: `search_started`, `page_retrieved` (with the failed domains), `extraction_skipped`, `competitor_product_extracted`;
  - matching: `match_validated`, `price_validated`;
  - products: `product_completed`, `product_failed`.
- **Product Intelligence payload fields** (additive):
  - `competitor_product_extracted`: `availability_status`,
    `match_proposal_status`, `proposed_match`, `issues` (verified issue count);
  - `match_validated`: `proposed_match`, `proposal_agreement`,
    `availability_status`;
  - `price_validated`: `price_kind` (`DIRECT`, `ALTERNATIVE`, `NONE`) and
    `alternative_price` (never a difference);
  - `product_completed`: `counts` includes `EQUIVALENT_VARIANT`, plus
    `alternative_prices`. The run summary has `equivalent_variants` and
    `alternative_prices`; `comparable_prices` counts direct prices only.
  - The extraction stage message counts both LLM 2 calls, for example
    "7 LLM 2 call(s) (3 match proposal), 1 failed".
- **Event cap:** `PIPELINE_MAX_EVENTS_PER_RUN` (5000) caps the detail events.
  Lifecycle, stage and product events are always kept.
- **SSE:** `GET /api/runs/{id}/stream` (`text/event-stream`).
  - It replays from `Last-Event-ID` or `?after=`, then streams live.
  - It sends `: keep-alive` every `PIPELINE_SSE_HEARTBEAT_SECONDS` (15).
  - It ends after the terminal event.
- **Polling:** `GET /api/runs/{id}/events?after=<seq>` returns the same events as JSON.
- **Cancellation:** `POST /api/runs/{id}/cancel` cancels the run's asyncio
  task.
  - This interrupts in-flight provider calls immediately.
  - Running stages and unfinished products become `CANCELLED`.
  - Finished stages and products keep their results.
  - `run_cancelled` is emitted. A finished run gives 409, an unknown run 404.

## Temporary state

- **Storage:** `InMemoryRunStore`, behind the `RunStore` interface so
  persistence can be added later.
- **Retention:** finished runs are pruned after `PIPELINE_RUN_TTL_MINUTES`
  (120) or beyond `PIPELINE_MAX_RUNS_RETAINED` (50).
- **Restart:** a restart clears all runs and cancels active ones.
- **Limits:** at most `PIPELINE_MAX_ACTIVE_RUNS` (2) run at once.
- **No run files:** nothing about a run is written to disk, apart from Phase 4
  review-queue candidates.

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/runs` | Body `{"product_ids": [...] (1–5), "run_discovery": true, "domains": [...]?}`. Returns 202 with the run |
| GET | `/api/runs` | Recent runs, newest first |
| GET | `/api/runs/{id}` | Status, stages, progress, summary counts, warnings |
| GET | `/api/runs/{id}/result` | Consolidated result: discovery, new pending domains, targets and skipped targets, LLM 2 mode and usage, and per product the Phase 5 candidates and Phase 6 matches. Filled progressively |
| GET | `/api/runs/{id}/events?after=` | Events as JSON |
| GET | `/api/runs/{id}/stream` | Events as SSE |
| POST | `/api/runs/{id}/cancel` | Cancel (202) |

## No-LLM behaviour

No OpenRouter key is required for any stage:

- **Discovery:** runs the baseline query. Every new domain stays pending.
  The stage message reads "LLM 1 skipped (…)".
- **Product search:** real search and retrieval run as normal.
- **Extraction:** `SKIPPED`, with the reason. Pages are `SKIPPED_NO_LLM`.
- **Matching:** uses page titles and URLs only. Every price is
  `NOT_EXTRACTED`, nothing is invented, and nothing is comparable.
- **Run status:** `COMPLETED`, with "skipped stages: extraction".

## Live check (12 Nov 2026, Exa, no LLM, scratch review database)

`POST /api/runs` for SB-060 (Boss FZ-5) with domains Andertons, Gear4music,
BopDJ (reviewer-approved) and Gibson, followed over SSE:

- **Events:** 52, from `run_started` through `run_completed`. Status
  `COMPLETED`, with extraction skipped.
- **Discovery:** 9 searches ($0.063), 8 new candidates, 7 saved as pending
  and not searched.
- **Product search:** 4 searches ($0.028) and 8 of 8 pages retrieved ($0.008).
- **Matching:** 2 `EXACT_MATCH` (Andertons, BopDJ), 1 `SIMILAR_MATCH`
  (Gear4music secondhand) and 5 `NO_MATCH` (blog, brand, category and forum
  pages). Comparable prices: 0, because there is no LLM 2.

## Limitations

- **Runs are synchronous within the process:** a run uses the API server's
  event loop. There is no worker queue, and a restart loses active runs (by
  design for the MVP).
- **Discovery is one opaque step:** its internal progress (individual
  searches) is not streamed. Within product search, progress is reported per
  product, not per page.
- **No pipeline-level retries:** retries happen only at the transport level.
- **Not yet run with real LLMs:** the LLM-enabled path is tested with mocked
  LLM 1 and LLM 2 only.
- **Frontend:** run controls and live views are in Phase 8 (`docs/dashboard.md`).

## UK evidence in events (Stage 6.3)

`competitor_discovered` also carries `uk_mixed_location` (additive). The event
carries flags only (`uk_assessed`, `uk_evidence_sufficient`,
`uk_evidence_contradicted`, `uk_mixed_location`, `uk_independent_sources`);
the quotes are in the run result (`discovery.candidates[].uk_review`). The
whole workflow (discover, review through the API, re-run, extract, match,
stream) is covered by `tests/test_workflow_integration.py` with fakes and a
temporary database.
