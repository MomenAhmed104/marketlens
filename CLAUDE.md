# CLAUDE.md --- Stompbox Competitor Intelligence Live Demo

**Purpose:** Implementation instructions for Claude Code.\
**Status:** Phases 1 and 2 complete and approved. Exa is the search and
retrieval provider. Phase 3 has not started and awaits approval. See
`docs/search-provider-evaluation.md`.\
**Catalogue baseline:** 100 Stompbox products.\
**Plan date:** 9 November 2026.

## 1. Your role

Act as the lead engineer implementing this project in the current
repository. Inspect, implement, test and report. Do not merely provide
suggestions.

### Mandatory rules

1.  Inspect the repository, Git status, existing instructions, code,
    dependencies and tests before editing.
2.  Preserve existing code and conventions. Do not replace an existing
    stack without a reason.
3.  Implement one phase at a time. Stop after Phase 1 and report results
    before proceeding.
4.  Run relevant tests after each meaningful change and report exact
    commands/results.
5.  Never invent competitor companies, domains, product pages, prices,
    SKUs, stock status, specifications or search results.
6.  Preserve source URLs and retrieval timestamps for all competitor
    evidence.
7.  LLMs may discover, extract, normalize, propose and explain.
    Deterministic Python must enforce validation, exact-match rules,
    calculations and state transitions.
8.  Keep secrets in environment variables. Never hardcode or expose API
    keys.
9.  Do not add sale-price analysis, discount calculations, historical
    price tracking, scheduled monitoring or default full-catalogue
    execution. These are outside the initial MVP.
10. Do not delete user data, overwrite uncommitted work, reset databases
    or perform destructive operations without approval.
11. Keep this file and project documentation updated when decisions
    change.
12. Avoid unnecessary dependencies and premature abstractions.

**First action:** inspect the repository and report its current state.
If it is empty, propose the scaffold using the defaults below. If the
catalogue workbook is missing, ask the user for its path or ask them to
place it in `data/catalogue/`; do not fabricate a replacement.

## 2. Product goal

Build a live AI-powered competitor intelligence platform for Stompbox.

A user selects one or more products from the prepared 100-product
catalogue. The system: 1. Loads the selected product context. 2. Reuses
known competitor domains relevant to the product category. 3. Discovers
additional relevant companies and official domains through global
search. 4. Automatically accepts high-confidence domains and flags
uncertain domains for user approval. 5. Searches globally and within
approved competitor domains. 6. Retrieves candidate product-page content
through search/content APIs. 7. Uses a second LLM to extract and
organize competitor product information. 8. Uses deterministic
validation to classify exact matches and similar alternatives. 9.
Validates displayed prices and supporting evidence. 10. Streams progress
and results to a live dashboard.

The initial demo is **selected-product execution**, not a full-catalogue
batch run.

## 3. Confirmed decisions

  -----------------------------------------------------------------------
  Area                                Decision
  ----------------------------------- -----------------------------------
  LLM architecture                    Two specialized LLMs

  LLM access                          OpenRouter

  Competitor discovery                Hybrid: reuse known competitors by
                                      category and discover additional
                                      companies

  Search scope                        Both global search and
                                      domain-targeted search

  New-domain approval                 Hybrid: auto-accept high-confidence
                                      domains; user approval for
                                      uncertain domains

  Product search                      Global candidate discovery plus
                                      targeted searches on approved
                                      domains

  Execution                           User selects one or more products

  Dashboard                           Live progress and progressive
                                      results

  Execution persistence               Temporary; no historical run
                                      storage in initial MVP

  Catalogue                           Prepared Stompbox 100-product Excel
                                      catalogue

  Pricing                             Standard displayed price only;
                                      ignore crossed-out prices and
                                      discounts
  -----------------------------------------------------------------------

### Existing competitor baseline

The initial 13 entries are: - Andertons --- `andertons.co.uk` -
GuitarGuitar --- `guitarguitar.co.uk` - Langley Guitar Centre ---
`langleyguitarcentre.co.uk` - The Music Dept --- `themusicdept.com` -
Gear4music --- `gear4music.com` - Thomann UK --- `thomann.co.uk` - Bax
Music --- `bax-shop.co.uk` - Project Music --- `projectmusic.co.uk` -
The 58 Sound --- `the58sound.com` - Perfect Pitch ---
`perfectpitch.co.uk` - South London Music Shop ---
`southlondonmusicshop.co.uk` - Yamaha Music London ---
`yamahamusiclondon.com` - Gibson --- `gibson.com`

Confirm canonical domains against the supplied workbook or existing
project data before using them. Gibson is a manufacturer reference, not
a conventional retailer. Preserve company type. PMT is excluded and
duplicate Andertons was removed.

## 4. Architecture

``` mermaid
flowchart TD
    A[User selects products] --> B[Product Context Builder]
    B --> C[Load known category domains]
    C --> D[LLM 1: Competitor Discovery]
    D --> E[Global company/domain discovery]
    E --> F[Domain validation and confidence]
    F --> G{Decision}
    G -->|High confidence| H[Auto-approve]
    G -->|Uncertain| I[User approval]
    H --> J[Approved domains]
    I --> J
    J --> K[Search API]
    B --> K
    K --> L[Global product search]
    K --> M[Domain-targeted search]
    L --> N[Retrieve candidate pages]
    M --> N
    N --> O[LLM 2: Product Intelligence]
    O --> P[Structured competitor products]
    P --> Q[Python matching and evidence validation]
    Q --> R[Temporary run state]
    R --> S[Live dashboard via SSE]
```

### LLM 1 --- Competitor Discovery Agent

**Purpose:** discover relevant companies, official domains and candidate
product-search queries.

Inputs: - Selected Stompbox product details. - Category/subcategory,
brand, model and specifications. - Existing category-relevant competitor
domains.

Responsibilities: 1. Reuse known competitors relevant to the selected
category. 2. Generate global queries to discover additional retailers
and companies. 3. Identify candidate official domains using search
evidence. 4. Classify company type: retailer, specialist retailer,
manufacturer, marketplace or other. 5. Assess relevance/confidence based
on evidence. 6. Recommend automatic approval or manual review. 7.
Generate targeted product queries for approved domains.

Output fields: - Company name and candidate domain. - Company type and
relevant categories. - Supporting evidence URLs. - Confidence score and
rationale. - Approval status. - Suggested product-search queries.

Do not invent companies/domains. Do not infer that a domain is official
from a similar name alone. Deduplicate normalized hostnames
deterministically in Python.

### Domain approval

-   High confidence: automatically approve for the current run.
-   Uncertain: mark `APPROVAL_REQUIRED` and wait for user input.
-   Rejected/irrelevant: exclude from product searches.

Thresholds must be configurable and calibrated through evaluation, not
presented as proven values without testing.

### Search API and retrieval

Support both: - **Global search:** find candidate products and
additional companies across the wider web. - **Domain-targeted search:**
search approved domains for exact brand/model combinations and relevant
variants.

Capture when available: - Page title, URL and hostname. - Retrieved main
content. - Product title and brand. - Model/SKU. - Standard displayed
price and currency. - Availability/stock text. - Specifications. -
Retrieval timestamp and provider metadata.

A search snippet alone is not definitive evidence of a current price.
Prefer retrieved product-page content. Preserve evidence and provider
metadata.

Benchmark Exa, Tavily and Brave before choosing. Compare
competitor-domain coverage, exact product-page discovery, price
visibility, content quality, freshness metadata, latency, cost and
domain-restricted search. Implement a provider interface so the provider
can be changed without rewriting agents.

### LLM 2 --- Product Intelligence Agent

**Purpose:** turn retrieved content into structured competitor product
information.

Responsibilities: 1. Extract product title, brand and model. 2. Extract
standard displayed price/currency only when explicit. 3. Extract
specifications and availability when present. 4. Normalize attributes
and terminology. 5. Produce structured competitor product records. 6.
Propose exact/similar candidates and explain evidence. 7. Identify
missing, ambiguous or contradictory fields.

Never invent product fields. Unknown values must remain null/unknown
with an explicit status. Preserve source URL and retrieval timestamp.

LLM 2 proposes matches; deterministic Python makes the final validation
decision.

### Python validation and matching

Use deterministic code for: - Product identity normalization. - Domain
normalization/deduplication. - Exact model/SKU checks. - Critical
variant checks. - Price parsing and currency validation. -
Price-difference calculations. - Evidence status assignment. - API
schema validation and state transitions.

Match statuses: - `EXACT`: same product identity and required variant. -
`SIMILAR`: related alternative, not the same product. - `REVIEW`:
potential match with unresolved identity/variant ambiguity. -
`NOT_MATCHED`: evidence does not support a match.

Exact-match checks must consider brand, manufacturer model/product
identifier and critical variants such as handedness, string count,
finish, generation and configuration. Use SKU/part number when
available. Similar products must never be mislabeled exact.

Evidence statuses: - `VERIFIED` - `UNVERIFIED` - `STALE` - `NOT_FOUND` -
`CONFLICT`

Retrieval time is not proof of the retailer's last update time.

### Price policy

This version ignores sale-price analysis: - Capture standard displayed
price and currency only. - Do not collect crossed-out original prices. -
Do not calculate savings or discount percentages. - Preserve source URL
and retrieval timestamp. - Do not invent missing prices. - Distinguish
missing price from zero. - Flag ambiguous/conditional prices. - Do not
treat snippets as verified current prices.

## 5. Temporary execution and live dashboard

The initial MVP does not retain historical runs or price observations: -
Catalogue and configured competitors remain available. - Active run
state/results are temporary. - No historical comparison or scheduled
monitoring. - Backend restart may interrupt runs and clear in-memory
state.

Encapsulate run state behind an interface so persistence can be added
later.

Use **Server-Sent Events (SSE)** initially. Suggested event names: -
`run_started` - `product_loaded` - `known_domains_loaded` -
`competitor_discovered` - `domain_auto_approved` -
`domain_approval_required` - `domain_approved` - `search_started` -
`search_result_found` - `page_retrieved` - `extraction_started` -
`competitor_product_extracted` - `match_validated` - `price_validated` -
`product_completed` - `run_completed` - `run_failed`

Each event should have a stable JSON envelope: event ID/sequence, run
ID, event type, timestamp, product ID when relevant, structured payload
and UI-safe message. Do not expose secrets or unnecessary raw provider
payloads.

Dashboard user flow: 1. Browse/search catalogue. 2. Select one or more
products. 3. Start a run. 4. Watch progress live. 5. Review discovered
companies/domains. 6. Approve uncertain domains. 7. View candidate
products and matches progressively. 8. Open source URLs and inspect
evidence.

## 6. Technology defaults and pending decisions

Use existing repository choices if present. If empty, proposed defaults:

  Component                 Default
  ------------------------- ------------------------------------
  Backend                   Python + FastAPI
  Frontend                  React + Vite
  LLM gateway               OpenRouter
  Search                    Benchmark Exa, Tavily and Brave
  Validation                Pydantic
  Matching/calculations     Python
  Initial execution state   In-memory
  Live updates              SSE
  Testing                   Pytest and suitable frontend tests

Resolved in Phase 1 (greenfield repository): new FastAPI backend in
`backend/`; React + Vite + TypeScript frontend in `frontend/`.

Resolved in Phase 2: search and retrieval provider is Exa
(`SEARCH_PROVIDER=exa`). Tavily is optional with no automatic fallback.
Brave is disabled.

Pending user choices: - Exact LLM 1 and LLM 2 models. - Domain
confidence thresholds.

Do not block foundational work on final model/provider selection. Build
interfaces/configuration first.

## 7. Suggested project structure

Adapt to existing repository conventions:

``` text
stompbox-intelligence/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── agents/
│   │   │   ├── discovery/
│   │   │   └── intelligence/
│   │   ├── orchestration/
│   │   └── main.py
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
├── frontend/
├── data/
│   ├── catalogue/
│   └── competitors/
├── docs/
├── .gitignore
└── README.md
```

Do not create unused placeholder folders/files.

## 8. Core data schemas

Define typed schemas before implementing agents:

  Schema                Purpose
  --------------------- ------------------------------------
  `StompboxProduct`     Source catalogue product
  `CompetitorDomain`    Existing/discovered company domain
  `SearchQuery`         Global or domain-targeted query
  `SearchResult`        Candidate URL and search evidence
  `RetrievedPage`       Retrieved content and metadata
  `CompetitorProduct`   Structured competitor product
  `ProductMatch`        Match relationship/status
  `ExecutionRun`        Temporary execution state
  `ExecutionEvent`      Live dashboard event

`StompboxProduct` fields: product ID, product name, brand, manufacturer
model, category/subcategory, standard displayed price, currency,
Stompbox SKU, product URL and specifications.

`CompetitorProduct` fields: competitor company/domain, product title,
brand, model/SKU when available, category/normalized attributes,
standard displayed price/currency when available, availability text when
available, product URL, retrieval timestamp, field-level evidence and
verification statuses.

Use Pydantic, explicit optional fields and enums. Avoid loose
unvalidated dictionaries between major components.

## 9. Implementation phases

### Phase 1 --- Project foundation

1.  Inspect repository and current stack.
2.  Confirm whether to create or integrate backend/frontend.
3.  Establish structure and environment configuration.
4.  Locate and load the 100-product Excel catalogue.
5.  Load the 13 competitor records.
6.  Define Pydantic schemas and API contracts.
7.  Add catalogue API and tests.

**Acceptance criteria** - Backend starts. - Exactly 100 unique product
IDs load. - All 100 product URLs are populated. - Every product passes
schema validation. - Competitor records load and are deduplicated. - No
secrets are hardcoded. - Catalogue API supports listing and
selected-product lookup. - Tests pass.

**Do not proceed to Phase 2 until Phase 1 passes.**

### Phase 2 --- Search API integration

1.  Benchmark Exa, Tavily and Brave.
2.  Select provider based on evidence.
3.  Implement provider abstraction.
4.  Implement global and domain-targeted search.
5.  Implement content retrieval.
6.  Normalize provider responses.
7.  Preserve URLs, timestamps and evidence.
8.  Add timeouts, retries, rate-limit handling and tests.

### Phase 3 --- LLM 1: Competitor discovery

1.  Define input/output schemas.
2.  Implement OpenRouter client abstraction.
3.  Configure LLM 1.
4.  Load known competitors by category.
5.  Generate global discovery queries.
6.  Discover candidate companies/domains.
7.  Classify company types.
8.  Score relevance/confidence with evidence.
9.  Apply hybrid approval.
10. Generate targeted product queries.

### Phase 4 --- Domain approval

1.  Define validation criteria.
2.  Validate domains against evidence.
3.  Implement auto-approval, approval-required and rejection paths.
4.  Expose decisions to dashboard.
5.  Ensure targeted searches use approved domains only.

### Phase 5 --- LLM 2: Product intelligence

1.  Define extraction schema.
2.  Configure LLM 2 independently.
3.  Extract product identity/specifications.
4.  Extract standard displayed price/currency.
5.  Normalize attributes.
6.  Preserve field-level evidence.
7.  Propose exact/similar candidates.
8.  Validate structured output.
9.  Test on retrieved pages.

### Phase 6 --- Matching and validation

1.  Implement identity normalization.
2.  Implement exact-match rules.
3.  Implement variant checks.
4.  Implement similar-product classification.
5.  Validate prices/currencies.
6.  Calculate price differences.
7.  Assign evidence/freshness statuses.
8.  Test ambiguous/contradictory cases.

### Phase 7 --- Workflow orchestration

1.  Implement selected-product run creation.
2.  Connect discovery, approval, search, extraction and matching.
3.  Implement temporary state interface.
4.  Emit structured events.
5.  Handle retries and partial failures.
6.  Deduplicate candidates within a run.
7.  Support cancellation and terminal states.

### Phase 8 --- Live dashboard

1.  Build catalogue selection.
2.  Build run controls.
3.  Connect SSE.
4.  Display live progress.
5.  Display discovered domains.
6.  Implement approval controls.
7.  Display extracted products and matches.
8.  Display price comparisons.
9.  Display source evidence/links.
10. Display completion/failure summaries.

### Phase 9 --- Testing and demo preparation

1.  Test multiple product categories.
2.  Evaluate discovery relevance and domain correctness.
3.  Evaluate extraction accuracy.
4.  Evaluate exact-match precision and variants.
5.  Verify prices against source pages.
6.  Measure latency and cost per run.
7.  Test missing pages, timeouts, rate limits and partial failures.
8.  Prepare repeatable demo scenarios.

## 10. Evaluation criteria

Measure: - Competitor discovery relevance. - Official-domain
correctness. - Search coverage and candidate-page yield. - Product
extraction accuracy. - Exact-match precision and variant error rate. -
Price extraction accuracy. - Evidence completeness. - Correct
stale/unverified handling. - End-to-end latency. - Search and LLM cost
per run.

Do not claim a product is unavailable merely because search did not find
it. Use `NOT_FOUND` or `UNVERIFIED`.

## 11. Definition of done

The live demo is ready when a user can: 1. Select one or more Stompbox
products. 2. Start a run and see progress live. 3. See known domains
loaded and new companies discovered. 4. See high-confidence domains
auto-approved and uncertain domains flagged. 5. See global and
approved-domain product searches. 6. See retrieved product pages and
source evidence. 7. See LLM 2 extract structured competitor products. 8.
See deterministic exact/similar validation. 9. See standard
displayed-price comparisons. 10. See clear missing, stale, unverified or
conflicting statuses.

All displayed competitor facts must be traceable to source evidence.

## 12. First task --- execute Phase 1 only

Start by inspecting the repository, not by implementing agents.

1.  Inspect repository files, Git status, existing instructions,
    backend/frontend code, tests and catalogue location.
2.  Report a concise assessment and identify whether this is a new or
    existing application.
3.  Locate the actual catalogue workbook. Do not guess its path or
    silently substitute another version.
4.  Inspect workbook sheets/columns and map them to `StompboxProduct`.
5.  Implement catalogue loading and Pydantic validation.
6.  Load and validate competitor records.
7.  Add a catalogue API endpoint and tests.
8.  Run tests and report exact results.
9.  Update project documentation with completed work and blockers.

If the workbook is absent, ask the user to place it in `data/catalogue/`
or provide its path.

### Phase 1 checklist

-   [x] Repository inspected.
-   [x] Existing user changes preserved.
-   [x] Backend starts.
-   [x] Catalogue workbook identified.
-   [x] Exactly 100 unique products loaded.
-   [x] All product URLs populated.
-   [x] All products pass Pydantic validation.
-   [x] Competitor records loaded and deduplicated.
-   [x] Environment configuration documented.
-   [x] Catalogue API works.
-   [x] Tests pass.

**Stop after Phase 1 and report results before proceeding to Phase 2.**

### Phase 1 outcome (10 Nov 2026)

-   Catalogue workbook:
    `data/catalogue/Stompbox_100_Product_Catalogue_No_Sale_Pricing.xlsx`
    (moved from the repo root). It is the single source for both
    products (`Products` sheet) and competitors (`Competitors` sheet).
    There is no separate `data/competitors/` file.
-   The catalogue is loaded and validated once at startup
    (`app/services/catalogue.py`). Any invalid row stops startup with
    row-numbered errors. Rows are never silently skipped.
-   Workbook audit columns are preserved on `StompboxProduct.provenance`.
-   Competitor `classification` text maps to `CompanyType` through an
    explicit table, and unmapped values are errors.
    `priority == "Reference only"` gives `price_benchmark=False`
    (Gibson).
-   Shared enums (`MatchStatus`, `EvidenceStatus`,
    `DomainApprovalStatus`) are in `app/schemas/common.py`. The other
    §8 schemas (`SearchQuery`, `SearchResult`, `RetrievedPage`,
    `CompetitorProduct`, `ProductMatch`, `ExecutionRun`,
    `ExecutionEvent`) are deferred to the phase that first uses them, so
    they are designed against real provider and LLM output.
-   Known gaps: the workbook has no competitor-to-category relevance
    (`relevant_categories` is empty, meaning unrestricted). Prices are a
    2026-11-09 snapshot. 39 products have listing-only evidence (see
    `provenance.source_type`).

### Phase 2 progress (10 Nov 2026)

-   Provider interface: `app/services/search/base.py` (`SearchProvider`
    with `search` and `retrieve`). Schemas are in `app/schemas/search.py`.
    Adapters: `exa.py`, `tavily.py`, `brave.py`. Factory and selection
    via `SEARCH_PROVIDER` are in `app/services/search/__init__.py`.
-   Domain scope is enforced deterministically in `finalize_results`
    after every provider call. Off-domain results are dropped and
    counted.
-   Retries cover 429 (honouring `Retry-After`), 5xx and timeouts. Auth
    errors and other 4xx errors are not retried. Error messages never
    include keys. Keys are `SecretStr`.
-   Cost comes from the provider response where reported (Exa; Tavily
    search). Otherwise it is estimated from list prices in settings
    (Brave; Tavily extract). The basis is recorded on each response.
-   Brave has no content-retrieval API, so `retrieve` raises
    `RetrievalNotSupportedError`.
-   Benchmark: `python -m app.benchmark.search_benchmark`. Raw output
    goes to `data/benchmark_runs/` (git-ignored). The summary JSON goes
    to `docs/benchmarks/`. Method and pricing are in
    `docs/search-provider-evaluation.md`.
-   URL canonicalisation is in `app/services/urls.py`: trailing slash,
    fragment, default port, and Gear4music `/<cc>/<lang>/` mapped to the
    UK store. `SearchResult` keeps the original `url` (evidence) and a
    separate `canonical_url`.
-   The Exa live-crawl wait is capped at `EXA_LIVECRAWL_TIMEOUT_MS`
    (default 15000).
-   `RetrievalStatus.BLOCKED`: short bot-check or access-denied pages
    (e.g. Bax "Client Challenge") are never treated as page content.
-   Benchmark identity specs (base model plus variant, brand, condition
    and accessory rules) are in `app/benchmark/products.py` and
    `metrics.py`. They are benchmark fixtures; the catalogue is
    unchanged. `--rescore RUN_ID` re-applies the rules to saved raw data
    without API calls.
-   Evidence: SB-028 is the "Les Paul Standard 60s **Figured**" (Stompbox
    page text), although the catalogue name omits "Figured". The
    Stompbox page shows "was £599 / now £579" and the catalogue stores
    579, which is a sale-price question for the Phase 5/6 price policy.
-   **Decision (10 Nov 2026, user-approved):** Exa is the default search
    and retrieval provider (`SEARCH_PROVIDER=exa`). Tavily stays
    implemented as an optional provider with **no automatic fallback**.
    Brave stays disabled.
