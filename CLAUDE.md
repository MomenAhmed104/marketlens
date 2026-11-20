# CLAUDE.md --- Stompbox Competitor Intelligence Live Demo

**Purpose:** Implementation instructions for Claude Code.\
**Status:** Phases 1 and 2 complete and approved (commit b2a4c70). Phase 3
(competitor discovery, LLM 1) is complete and approved (commit 05290be). Phase 4
(competitor domain review with persistent decisions) is complete (commit
7371a44). Phase 5 (product intelligence, LLM 2) is complete (commit 9089770). Phase 6
(deterministic matching and price validation) is complete (commit 2118cec).
Phase 7 (pipeline orchestration) is complete (commit e295c17). Phase 8 (live
dashboard) and the classification work are in the WIP checkpoint `970c7be` on
branch `wip/uk-gate-2026-11-17`, awaiting review; see `docs/dashboard.md`. UK
relevance follows the approved Option B simplification (19 Nov 2026, uncommitted):
verified UK evidence in discovery and review, no four-category assessment, and no
separate UK eligibility gate (retired). See "UK relevance evidence, Option B" below and
`docs/stompbox_uk_competitor_intelligence_implementation_plan.md` (the authoritative
baseline). LLM 2 is now the three-task Product Intelligence Agent (plan §8.6
stages 1-5 implemented 19 Nov 2026, uncommitted). Stage 6 controlled live evaluation was
authorized and run twice on 19 Nov 2026 (Stage 6 and Stage 6.2: SB-001 and SB-028 on Andertons
and Gear4music, about $0.04 each); Stages 6.1, 6.1.1 and 6.3 corrected what they found, offline
only. See "Product Intelligence Agent, stages 1-5" and "Live evaluation runs" below. Stage 6.3
(pre-demo hardening, 20 Nov 2026, uncommitted) awaits review before the Git checkpoint; its P-5
refinement and the SB-028 Figured identity were approved on 20 Nov 2026, and the SB-028 price
baseline (£579, recorded as a sale price) is still unconfirmed. Do not
start Phase 9 or any further live (paid) run until the user approves. Both models are configured
as `openai/gpt-6-luna` (14 Nov 2026; see "OpenRouter configuration" below); without
`OPENROUTER_API_KEY` both agents run in `LLM_DISABLED` mode. See
`docs/competitor-discovery.md`.\
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

**Purpose (approved 19 Nov 2026, plan §3.6 and §8.6, decisions D-20 to
D-34):** for every selected catalogue product, help answer: which
competitor sells it, how it matches (exact match, equivalent variant,
similar alternative or not a match), its current selling price, and
whether it is available.

LLM 2 is **one logical agent with three tasks**, each with its own prompt
and structured output, on the existing OpenRouter client and `LLM2_MODEL`
(no other model or provider):

-   **Task B, extraction** (1st call per retrieved page): title, brand,
    model, SKU, specifications, current price and currency, availability,
    options, per-option prices and issues (ambiguous variant, conflicting
    information, multiple prices, missing information), each with a
    verbatim quote.
-   **Task A, match proposal** (2nd call, only on a retrieved page that
    Task B classified as a single-product page): `EXACT_MATCH`,
    `EQUIVALENT_VARIANT`, `SIMILAR_ALTERNATIVE` or `NOT_A_MATCH`, with
    reasons and differing attributes that quote the page.
-   **Task C, combined output**: assembled in Python with no LLM call
    (`ProductIntelligenceView`): seller, match, price, availability and
    explicit uncertainties.

Never invent product fields. Unknown values must remain null/unknown
with an explicit status. Preserve source URL and retrieval timestamp.

LLM 2 proposes; Python validates and decides. Python keeps domain
approval and search permissions, search and retrieval, quote
verification, price validation and sale-price exclusion, currency and
evidence validation, the final match decision, benchmark eligibility and
orchestration. An LLM 2 proposal or verified issue can never upgrade,
downgrade or replace a Python classification: its only possible effect
is turning `EXACT_MATCH` / `EQUIVALENT_VARIANT` into `REVIEW_REQUIRED`
(U-4).

### Python validation and matching

Use deterministic code for: - Product identity normalization. - Domain
normalization/deduplication. - Exact model/SKU checks. - Critical
variant checks. - Price parsing and currency validation. -
Price-difference calculations. - Evidence status assignment. - API
schema validation and state transitions.

Match statuses (names updated in Phase 6 to the user's brief;
`EQUIVALENT_VARIANT` added 19 Nov 2026, P-2/P-3): - `EXACT_MATCH`: same
product identity and required variant. - `EQUIVALENT_VARIANT`: otherwise
the same product, only the cosmetic finish or colour differs (U-1;
decided by Python alone). - `SIMILAR_MATCH`: related alternative, not the
same product. - `REVIEW_REQUIRED`: potential match with unresolved
identity/variant ambiguity. - `NO_MATCH`: evidence does not support a
match. LLM 2's `SIMILAR_ALTERNATIVE` maps to `SIMILAR_MATCH` and
`NOT_A_MATCH` to `NO_MATCH`, only to compare the proposal with Python.

Exact-match checks must consider brand, manufacturer model/product
identifier and critical variants such as handedness, string count,
finish, generation and configuration. Use SKU/part number when
available. Similar products must never be mislabeled exact. Handedness,
size, configuration, electronics, functional specifications and model
differences never qualify as an equivalent variant.

**Direct vs alternative prices (D-28, D-32):** only `EXACT_MATCH`
prices are direct competitor prices (benchmark gate unchanged, difference
competitor − Stompbox). `EQUIVALENT_VARIANT` and `SIMILAR_MATCH` prices
are alternative-product prices: same price validation, a search-approved
domain but no benchmark flag needed, an explicit condition label for
used/refurbished items, no difference, never in `comparable_prices`, and
displayed separately. Several current prices that cannot be tied to the
product or variant make the price `AMBIGUOUS`; the lowest is never
chosen (P-6). Availability statuses (P-5): `IN_STOCK`, `OUT_OF_STOCK`,
`PREORDER`, `BACKORDER`, `DISCONTINUED`, `UNKNOWN` (missing, ambiguous,
conflicting or unverifiable evidence; low-stock wording is `IN_STOCK`
only on a page that shows the product can be bought now, U-3).

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

### Phase 3 progress (10 Nov 2026)

-   Scope boundary: Phase 3 makes the approval *decision*
    (AUTO_APPROVED, APPROVAL_REQUIRED or REJECTED). Phase 4 adds the
    user approval workflow and endpoints, the dashboard exposure, and
    enforcement in live product searches.
-   LLM gateway: `app/services/llm/`. `LLMClient` returns validated
    Pydantic objects. `OpenRouterClient` uses strict `json_schema`
    output, `provider.require_parameters`, one repair retry, and
    records usage and cost. `build_llm1` needs `OPENROUTER_API_KEY` and
    `LLM1_MODEL`; there is deliberately no default model (a user
    decision).
-   Agent: `app/agents/discovery/` (agent, rules, prompts, schemas,
    CLI). API: `POST /api/discovery/runs`. Design and rules:
    `docs/competitor-discovery.md`.
-   All decisions are deterministic (`rules.py`). The LLM proposes the
    name, type, relevance, confidence and cited URLs. The name must
    appear in the evidence text, and cited URLs must be in the
    evidence. Marketplaces and manufacturers always need approval.
-   Known competitors are never re-decided. Their discovery hits are
    stored as relevance evidence only. Gibson keeps
    `price_benchmark=false` on its targeted queries.
-   Text-matching helpers moved to `app/services/text_match.py` (the
    benchmark re-exports them). `registrable_domain()` is in
    `app/services/domains.py`.
-   A live Exa run without the LLM (SB-001, SB-044, SB-091) found 23
    new domains. All degraded safely to APPROVAL_REQUIRED.
-   **Decision (11 Nov 2026, user):** OpenRouter configuration is
    postponed. Discovery supports two modes: `LLM_ENABLED` (OpenRouter
    planning and classification) and `LLM_DISABLED` (baseline queries,
    real Exa searches, evidence and UK signals recorded, no
    classification). In `LLM_DISABLED` every new company is
    `APPROVAL_REQUIRED` and nothing is invented: name, type, relevance,
    confidence and UK selling stay null or unknown. Missing LLM config is
    never an error; only a missing search provider is (503). The
    `DISCOVERY_USE_LLM=false` switch forces the disabled mode. A
    candidate's `classification_status` records COMPLETED,
    SKIPPED_LLM_DISABLED, FAILED or MISSING.

### Phase 4 progress (12 Nov 2026)

-   Persistence: SQLite through the standard-library `sqlite3` module, in
    `app/services/review_store.py`. The file is
    `data/review/competitor_review.sqlite3` (`REVIEW_DB_PATH`, git-ignored).
    It has three tables: the review queue (an evidence snapshot per discovered
    domain), current decisions (the domain is the primary key) and an
    append-only decision history. Discovery run results stay temporary. This
    is the first persistent store; the "temporary execution state" rule still
    applies to runs.
-   Schemas: `app/schemas/review.py` (`DomainDecisionRecord`, `ReviewStatus`
    PENDING/APPROVED/REJECTED). `CompanyType.DIRECTORY` was added (also in
    the LLM 1 prompt). `BENCHMARK_ELIGIBLE_TYPES` = retailer, specialist
    retailer and (since 18 Nov 2026) manufacturer.
-   API: `app/api/review.py` under `/api/review`. Rules and the full
    endpoint list are in `docs/competitor-review.md`.
-   Approval and benchmark eligibility are separate. Approval needs an
    explicit company type, where null means "reviewed, unknown". Only
    retailer and manufacturer types can be benchmarks, and anything else is
    refused with 422, never coerced. **Decision (18 Nov 2026, user):**
    manufacturers join the main price benchmark alongside retailers. A
    manufacturer still needs its own benchmark flag (Gibson stays
    `price_benchmark=false`; Yamaha Music London keeps `manufacturer` and
    `true`). (The UK gate clearance this also required was retired on 19 Nov 2026,
    Option B.) The frontend mirror of the type list
    (`frontend/src/api.ts`) matches, and a non-benchmark competitor's reason
    now reads "… is not marked as a price benchmark (type)". Known marketplaces (`MARKETPLACE_DOMAIN_LABELS`) can only
    be approved as `marketplace` and are never auto-approved by
    `rules.decide`. Preconfigured competitors cannot be reviewed (409), and
    decisions about them are ignored.
-   **Interpretation (followed by the Phase 5 brief: "search only domains
    approved for searching; respect types and benchmark eligibility"):**
    "approved, eligible domains" in targeted searches means reviewer-APPROVED domains (plus the 13
    preconfigured competitors and run-scoped auto-approvals). Benchmark
    eligibility rides on each `TargetedQuery.price_benchmark`, as Gibson's
    already did, so an approved marketplace is searched but never used as a
    benchmark. Pending and rejected domains are never searched.
-   Discovery integration: `CompetitorDiscoveryAgent(review_decisions=...)`
    and `DiscoveryRunResult.search_targets`. The API and CLI load decisions
    and record candidates after each run.
-   Frontend: `frontend/src/Review.tsx`, a review section on the main page.
    It is the dashboard's "Domain review" tab since Phase 8.
-   Live check (12 Nov 2026, Exa, LLM_DISABLED, scratch database): SB-001 gave
    7 pending candidates, including `amazon.co.uk` (flagged as a known
    marketplace) and `idealo.co.uk` (a price-comparison directory). Approving
    Amazon as a retailer was refused with 422; approving it as a marketplace
    succeeded. Decisions survived an app restart. The next run searched 14
    domains: Amazon with `price_benchmark=false`, and the rejected domain
    not at all.

### Phase 5 progress (12 Nov 2026)

-   Agent: `app/agents/intelligence/` (agent, rules, prompts, schemas, CLI).
    API: `GET /api/intelligence/config` and `POST /api/intelligence/runs`
    (1-5 products, optional `domains` subset). Design, statuses and limits:
    `docs/product-intelligence.md`.
-   LLM 2 is configured independently (`LLM2_MODEL`, `LLM2_TEMPERATURE`,
    `LLM2_MAX_COMPLETION_TOKENS`) through `build_llm2`. There is no default
    model. Without it, the mode is `LLM_DISABLED`: real search and retrieval,
    nothing extracted (`SKIPPED_NO_LLM`). Missing LLM configuration is
    never an error.
-   Search targets come from `eligible_search_targets`: the 13 preconfigured
    competitors plus reviewer-APPROVED domains. Pending and rejected domains
    are never searched. Approved `directory` domains are skipped
    (`INTELLIGENCE_EXCLUDED_COMPANY_TYPES`). Benchmark eligibility is carried
    unchanged on every candidate.
-   `CompetitorProduct` (§8) is defined here. Every value needs a verbatim
    quote that code finds in the page text, with the value inside the quote.
    Otherwise the value stays null and `FieldEvidence` gives the status:
    EXTRACTED, NOT_FOUND, UNSUPPORTED, EXCLUDED_SALE_PRICE, AMBIGUOUS,
    CONFLICT or INVALID.
-   Price policy as implemented: only the current displayed price is
    extracted. Was/RRP/original/saving/"off" amounts are excluded
    deterministically (`app/services/prices.py`) and through LLM 2's
    `ignored_prices`. Conditional prices (from, per month, deposit) and
    prices without an explicit currency are AMBIGUOUS. Zero is INVALID.
    `sale_indicated` is informational only, and no discount is computed.
-   Retrieval fetches the canonical URL, so Gear4music `/us/en/` maps to the
    UK store. A live check showed the US storefront page had no GBP prices
    and the UK page did.
-   Live check (12 Nov 2026, Exa, LLM_DISABLED, SB-001 on Andertons,
    Gear4music and Thomann): 3 searches ($0.021) and 6 of 6 pages retrieved
    ($0.006). Selected pages included product pages, a secondhand Gear4music
    listing and an Andertons guide page. LLM 2 extraction has not run
    against a real model.
-   No matching, price validation or price comparison was added (Phase 6).

### Phase 5 finalisation (12 Nov 2026, no-LLM)

-   **No-LLM mode is supported and is the current default.** Real Exa search
    and retrieval have been tested end to end. Real LLM 2 extraction,
    accuracy and cost evaluation are deferred until `OPENROUTER_API_KEY` and
    `LLM2_MODEL` are configured; no code change is needed to activate them.
-   The extraction status for retrieved pages without LLM 2 is
    `SKIPPED_NO_LLM`, with the reason in `extraction_error`. Phase 3's
    discovery status `SKIPPED_LLM_DISABLED` is unchanged.
-   OpenRouter account errors (401, 402, 403) stop further LLM 2 calls within
    the run instead of failing every page. A blank `LLM2_MODEL` or key counts
    as unset.
-   The live real-page check found Magento "Regular Price £219 Special Price
    £197" (BopDJ). "Regular/normal price" is now treated as the original
    price whenever a sale, special, offer or now price label is also shown.
-   Live no-LLM API run: SB-001 and SB-060 on 5 domains, 10 searches
    ($0.07) and 20 of 20 pages ($0.02). Approved directory and rejected
    domains were skipped and pending ones excluded (422 when requested). See
    `docs/product-intelligence.md`.

### Phase 6 progress (12 Nov 2026)

-   Code: `app/services/matching/` (attributes, matcher, price, schemas,
    CLI). API: `POST /api/matching/evaluate` (body: a Phase 5
    `IntelligenceRunResult`). Deterministic only; no LLM and no key. Rules:
    `docs/product-matching.md`.
-   `MatchStatus` in `app/schemas/common.py` was renamed to the brief's names
    (`EXACT_MATCH`, `SIMILAR_MATCH`, `REVIEW_REQUIRED`, `NO_MATCH`). It was an
    unused Phase 1 placeholder.
-   Identity needs the exact model identifier in at least two independent
    evidence groups (title, URL, LLM 2 model/SKU field); title similarity
    alone is never enough. Snippets are never used. Stompbox SKUs are
    internal and are not compared.
-   Critical variant conflicts (handedness, finish, strings, size, wattage,
    configuration, bundle, generation, edition) give SIMILAR_MATCH. A
    required variant that is unconfirmed gives REVIEW_REQUIRED. USED or
    REFURBISHED condition is never equivalent to new.
-   Price validation is separate from matching. An EXTRACTED price is
    re-verified from scratch: the parse, the quote, sale and conditional
    context, currency, and page type. Only VALID GBP prices with VERIFIED
    (not STALE, 24 h) evidence, on EXACT matches with benchmark-eligible
    competitors and comparable condition, are compared.
    `difference_gbp` = competitor − Stompbox; no percentages or savings.
-   Assumption: the catalogue `condition` is unverified, so Stompbox items
    are treated as new retail products for comparison.
-   Real-data check: the live no-LLM Phase 5 run (fixture
    `tests/fixtures/phase5_no_llm_run.json`, snippets removed) gives 6 EXACT
    (MEDIUM), 3 SIMILAR (secondhand) and 11 NO_MATCH (forum, guide and
    category pages). There are 0 comparable prices because nothing is
    extracted without LLM 2. Two bugs found on real data were fixed:
    Gear4music product code "7X8" read as a cabinet, and a category page
    rated SIMILAR without a page-type classification.

### Phase 7 progress (12 Nov 2026)

-   Code: `app/orchestration/` (schemas, store, pipeline) and `app/api/runs.py`
    (`/api/runs`, including `/stream` SSE and `/cancel`). Stages: validate,
    discovery, review_queue, targets, product_search, extraction, matching.
    Design: `docs/pipeline.md`.
-   Reuse only: the Phase 3, 4, 5 and 6 agents and services are called as
    they are. The one refactor is `matching.evaluate_product` (per-product
    matching, used by `evaluate_run`), so matches stream product by product.
-   Run state is temporary: `InMemoryRunStore` behind the `RunStore`
    interface, pruned by TTL and count, and cleared on restart. A run's only
    durable effect is Phase 4's: new candidates are added to the review
    queue as pending.
-   Searched: the 13 configured, reviewer-approved, and (LLM_ENABLED only)
    this run's auto-approved domains (`PIPELINE_SEARCH_AUTO_APPROVED`).
    Pending, rejected and directory domains are never searched.
-   Statuses: run PENDING/RUNNING/COMPLETED/PARTIALLY_COMPLETED/FAILED/
    CANCELLED. Stage COMPLETED/PARTIAL/SKIPPED/FAILED/CANCELLED. Failures are
    isolated per stage, domain and product, with timeouts at run, discovery
    and product level. Retries stay at the transport level.
-   Cancellation cancels the run's asyncio task; partial results are kept
    and clients are closed.
-   Live check (Exa, no LLM, scratch review database): SB-060 on 4 domains,
    52 SSE events, COMPLETED with extraction skipped, about $0.10. Results: 2
    EXACT, 1 SIMILAR, 5 NO_MATCH; 7 new pending domains queued and not
    searched.

### Phase 8 progress (13 Nov 2026)

-   Code (after the 13 Nov redesign): `frontend/src/app/` (shell, sidebar,
    top bar, run selector, hash router, and `RunProvider` holding the single
    `useRunStream`), `pages/` (Overview, ProductIntelligence, LiveRuns,
    DomainReview), `dashboard/` (CataloguePicker, RunControls,
    PipelineStepper, EventTimeline, RunPanels, ComparisonTable, EvidencePanel,
    RunDomains), and `components/` (StatusPill, KpiCard, EmptyState).
    Also `status.ts` (status → tone, icon, label), `selectors.ts` (Overview
    counts), `runStream.ts`, `format.ts` and `reviewer.ts`. `Review.tsx`
    (Phase 4) is reused with optional `status` and `onChanged` props. Design:
    `docs/dashboard.md`. No backend behaviour changed.
-   **Redesign (13 Nov 2026, user-approved plan):** a dark navy sidebar, a
    light workspace and four pages (Overview, Product Intelligence, Live Runs,
    Domain Review). User decisions: the Overview summarises **one selected
    run** (the active one, else the most recent in memory), and there are **no
    charts** (KPI cards, tables and insights instead). No new dependencies:
    hash routing, inline SVG icons and CSS tokens in `index.css`, with
    contrast checked at ≥ 4.5:1.
-   **Design v2 (13 Nov 2026, user-approved after a screenshot review):**
    navy hero bands, a cobalt accent (cyan on navy only), grouped sidebar
    navigation with a System card, a run switcher (keyboard listbox) and a
    connection badge shown only when it adds information.
    -   Overview: hero with the key finding (`keyFinding`), a match distribution
        bar (counts only), varied stat tiles, price cards or a price explainer
        (`priceGate`), and a folded coverage table.
    -   Product Intelligence: product header, comparison groups with status
        rails, and evidence in a focus-trapped drawer with tabs.
    -   Live Runs: vertical pipeline timeline plus a compact track, an outcome
        banner, an event feed grouped by stage, and a sticky selection tray.
    -   Domain Review: restyled domain cards with approval and benchmark
        eligibility shown separately, and configured-competitor tiles.
    -   The full labelled sidebar is kept down to 1024 px. No new dependencies.
        The three match colours pass the dataviz palette validator. Details:
        `docs/dashboard.md`.
-   **Decision (13 Nov 2026, user):** the pipeline does not pause for
    approval. Domains discovered in a run stay pending and unsearched in that
    run. Approving one on the dashboard makes it eligible for the next run,
    and a finished run offers "Re-run these products".
-   SSE: a listener per named event type. Duplicates are ignored and gaps are
    filled from `/events?after=`. The client closes the stream on the terminal
    event, which prevents the EventSource reconnect loop. It falls back to
    polling after repeated errors, and a 404 means the run expired. The run
    ID is kept in `?run=`.
-   Prices: the UI formats backend decimal strings and does no arithmetic. A
    difference is shown only when `comparison.eligible`, labelled
    "competitor − Stompbox"; there are no percentages, savings or discounts.
-   Selection is limited to 5 (`MAX_PRODUCTS_PER_RUN` mirrors the backend
    setting, and the backend still enforces it).
-   Tests: `npm test` (Vitest, jsdom, Testing Library; new dev dependencies,
    approved by the user). It runs through `frontend/scripts/vitest.mjs` because
    a lower-case Windows drive letter makes Vitest load twice. Fixture:
    `backend/tests/make_dashboard_fixture.py` writes
    `frontend/src/test/fixtures/llm-run.json` from the mocked Phase 7
    pipeline.
-   Live check (Exa, no LLM, scratch review database, separate ports): SSE
    worked through the Vite proxy (27 events), the stream closed after
    `run_completed`, and `Last-Event-ID` resume worked. The UI has not been
    checked visually in a browser (before or after the redesign).

### OpenRouter configuration (14 Nov 2026)

-   **Decision (user):** both LLM 1 and LLM 2 use `openai/gpt-6-luna`, with
    `LLM1/LLM2_REASONING_EFFORT=low`, `LLM1_MAX_COMPLETION_TOKENS=12000` and
    `LLM2_MAX_COMPLETION_TOKENS=8000` in `backend/.env`. The user adds
    `OPENROUTER_API_KEY` themselves. Until then both agents stay `LLM_DISABLED`.
-   OpenRouter's model list (checked 14 Nov 2026) shows the model supports
    `structured_outputs`, `response_format`, `reasoning` and
    `max_completion_tokens`, but **not `temperature`**. Because the client
    sends `provider.require_parameters`, an unsupported parameter means no
    endpoint is found. `LLM1/LLM2_TEMPERATURE` are therefore optional (default
    unset, not sent; an explicit value including 0 is sent).
    `LLM1/LLM2_REASONING_EFFORT` are sent as `reasoning: {effort, exclude: true}`.
-   Blank or whitespace-only `LLM1/LLM2_MODEL`, temperature and effort values
    in `.env` count as unset (a settings validator).
-   Truncation (`finish_reason=length`, including a reasoning model that spends
    the whole budget with no answer) is never parsed or repaired.
    `LLMOutputError` carries the usage and request IDs of failed calls, and
    both agents add those tokens and that cost to their run usage.
-   Real OpenRouter calls since then (no others are recorded): one LLM 1 discovery run on
    14 Nov 2026 (10 classified domains in the review database), the classification backfill on
    16 Nov 2026 ($0.0142) and the two Stage 6 product-intelligence runs on 19 Nov 2026 (8 LLM 2
    calls each, about $0.005 each). Accuracy, latency and cost per full pipeline run are still
    unmeasured.

### Explicit competitor classification, stage 1 (15 Nov 2026, backend only)

-   LLM 1 proposes `competitor_class` (DIRECT_COMPETITOR, RELATED_RETAILER,
    NEEDS_REVIEW) in its existing classification call. `rules.classify_competitor`
    applies downgrade-only safeguards with recorded reasons. Proposal and final
    class are stored separately with the timestamp and model. Rules:
    `docs/competitor-discovery.md`. Classification is independent of approval and
    benchmark eligibility, and Direct does not use the 0.75 threshold.
-   **Decisions (user):** known marketplace domains are always Related Retailer,
    overriding any LLM 1 proposal (the one upgrade the rules make);
    manufacturers, directories and other types are Needs Review.
-   PMT is excluded through `DISCOVERY_EXCLUDED_COMPETITOR_DOMAINS` in discovery
    and in `ReviewStore.record_run`.
-   Backfill executed (16 Nov 2026, user-approved; stage 3 below): all 48 stored
    domains now have a backend class.
-   Backfill, stage 1 (16 Nov 2026): a dry-run-only command,
    `python -m app.agents.discovery.backfill`, reads the review database
    read-only (`mode=ro`) and reports proposed and final classes per domain. It
    reuses the LLM 1 prompt and `classify_competitor`. It is tested only on copied
    temporary databases with a fake LLM. See `docs/competitor-discovery.md`.
-   Backfill, stage 3 (16 Nov 2026, user-approved): `--write` updates only the
    five classification fields of CLASSIFIED records. It works in one transaction,
    after a verified timestamped backup, with conflict detection and a post-write
    check. It ran on the real database: 48 of 48 written, 31 Direct, 4 Related and
    13 Needs Review, at $0.0142. Decisions, history, evidence and row metadata were
    verified unchanged.
-   Merge fix (16 Nov 2026): if a domain is rediscovered and its LLM
    classification is skipped, failed or missing, `merge_candidate` keeps the
    stored LLM-backed class (including backfilled ones), with all five class fields
    together. A new successful LLM classification replaces it. Relevance and
    confidence are never used. See `docs/competitor-discovery.md` and
    `tests/test_review_merge.py`.
-   Frontend integration, stage 2 (16 Nov 2026): the dashboard shows the backend
    `competitor_class`. The frontend relevance threshold is removed. A temporary
    fallback for unclassified older records gives Related Retailer for known or
    reviewer-typed marketplaces and Needs Review otherwise, and never Direct. The
    `competitor_discovered` SSE event now carries `competitor_class` and
    `proposed_competitor_class`. Rules: `docs/dashboard.md`.

### Domain Review simplification (14 Nov 2026, user request)

-   Domain cards show only: company name, domain, company type, competitor
    classification (Direct Competitor / Related Retailer / Needs Review), review
    status, Approve/Reject (or Change decision), and evidence links and snippets
    on demand. Relevance, confidence, rationale, eligibility, facts and history
    are hidden in the UI but still stored and returned by the API.
-   The classification shown is the backend's `competitor_class` (since 16 Nov
    2026; it was previously derived in the frontend).
    `frontend/src/competitorClass.ts` maps it and holds the temporary fallback.
    Rules are in `docs/dashboard.md`. Missing or unclear data gives "Needs Review".
-   **Decision (15 Nov 2026, user):** "Related Retailer" deliberately includes
    marketplaces. Known marketplace domains (`MARKETPLACE_DOMAIN_LABELS`) are
    always Related, never Direct, even if LLM 1 proposed "retailer". Unnamed
    domains are titled by the domain, and a missing company type is not shown.

### UK relevance evidence, Option B (18-19 Nov 2026, uncommitted)

-   **Authoritative baseline:** `docs/stompbox_uk_competitor_intelligence_implementation_plan.md`.
    The user selected the simplified UK relevance approach (Option B) on 19 Nov 2026. Design
    and rules: `docs/competitor-discovery.md` ("UK relevance evidence").
-   LLM 1 discovers UK-focused competitors (UK-based shops and shops abroad that serve UK
    customers) and returns `uk_evidence` only: up to 5 exact quotes, each with an evidence kind.
    It gives no UK verdict. Python verifies each quote (verbatim at the cited URL, wording
    fits its kind, weak signals never count) and stores `DiscoveredCompetitor.uk_assessment`
    (`UkEvidenceAssessment`: `sufficient`, `contradicted`, `mixed_location`,
    `independent_sources`, verified evidence, unverified claims, reasons).
-   **Decisions (19 Nov 2026, user):**
    -   No formal four-category assessment (`UK_BASED` / `SERVES_UK_FROM_ABROAD` /
        `NOT_SERVING_UK` / `UNKNOWN`), no UK confidence, no review recommendation, and no
        reviewer UK override. Reviewers approve or reject with optional `notes`.
    -   Auto-approval needs sufficient verified UK evidence, no verified contradiction, no
        mixed location, and supporting quotes from at least two different evidence pages
        (several lines from one page count once), plus every other existing criterion.
    -   `.uk` domain, GBP price and postcode signals are not an auto-approval requirement and
        never establish UK relevance on their own (the Direct Competitor class safeguard
        still uses them; unchanged).
    -   Verified contradictory evidence goes to review and is never rejected automatically. A
        reviewer may approve despite it: the domain is then searched, and the contradiction,
        decision and note stay visible.
    -   Auto-approval never grants benchmark permission (`decide` always sets
        `price_benchmark=false`). Only the reviewer's explicit `benchmark_eligible` or the
        workbook flag makes a domain a benchmark.
    -   With LLM 1 disabled, new domains stay pending. Configured competitors keep their
        configured path, workbook types and benchmark flags, with no new-domain rules applied.
-   Earlier decisions kept: the seven approved domains stay approved with their benchmark
    flags (D2); Thomann is searched and benchmarked from its workbook flag (D5); the 13 show
    as Configured (D6); one profile search per candidate (D7); no paid backfill (D9).
-   **The separate UK eligibility gate is retired:** `uk_gate.py`, `uk_evidence.py`,
    `schemas/uk_eligibility.py`, `/api/review/uk-eligibility`, the `review_migration` CLI,
    exceptions and seeding, and `docs/uk-eligibility.md`. Product searches use the 13
    configured competitors, reviewer-approved domains and (LLM enabled,
    `PIPELINE_SEARCH_AUTO_APPROVED`) this run's auto-approved domains. Pending, rejected and
    directory domains are never searched. Benchmark comparisons need only the benchmark flag.
    `trusted.py` still rebuilds competitors from server state and fails closed (no benchmarks)
    if the review database cannot be read.
-   **Storage:** new review databases are created at version 1 (three core tables). Existing
    version-1 and version-2 files open without any write; a version-2 file's UK tables are
    never read, written or dropped, and no file's version changes. No migration. Older
    records with the retired category or override fields load (the extra keys are ignored)
    and are never rewritten. The real database is version 1 with no UK assessments and was
    not touched.
-   API and events: `uk_review` on review candidates (evidence view); discovery candidates
    carry `uk_assessment` and `uk_review`; `competitor_discovered` carries `uk_assessed`,
    `uk_evidence_sufficient`, `uk_evidence_contradicted`, `uk_mixed_location` (Stage 6.3) and
    `uk_independent_sources` (flags only; the quotes are in the run result).
-   LLM 1's UK-evidence prompt has never run against the real model (the 14 Nov discovery run
    predates Option B); the accuracy of its UK quotes is unmeasured.
-   **Stored review records (checked 20 Nov 2026, read-only):** 48 candidates (39 pending,
    7 approved, 2 rejected; 10 classified by LLM 1 on 14 Nov, 38 from no-LLM runs). None has a
    `uk_assessment`, so all show "UK evidence not assessed" until a domain is rediscovered with
    LLM 1. They are deliberately not backfilled (D9).
-   **Auto-approval thresholds:** `DISCOVERY_AUTO_APPROVE_MIN_CONFIDENCE=0.85`,
    `DISCOVERY_AUTO_APPROVE_MIN_RELEVANCE=0.75`, `DISCOVERY_AUTO_APPROVE_MIN_EVIDENCE_URLS=2`,
    `DISCOVERY_REJECT_MAX_RELEVANCE=0.3` (settings defaults; not overridden in `.env`). They are
    uncalibrated starting values: no labelled evaluation set exists yet. Calibrate them in Phase 9
    before relying on auto-approval; do not change them without that evidence.

### Product Intelligence Agent, stages 1-5 (19 Nov 2026, uncommitted)

-   **Authority:** plan §3.6, §8.4.1, §8.5 and §8.6; decisions D-20 to D-34 (P-1 to P-7, U-1 to U-4
    all approved; only the live test budget is open). Design notes: `docs/product-intelligence.md`
    and `docs/product-matching.md`.
-   **Stage 1, Task B:** `PageExtraction` gained `options`, `option_prices`, `issues`,
    `availability_normalized` and `specifications[].normalized_name` (defaults keep old fakes valid;
    the strict schema requires them). `rules.check_extraction` verifies each against the page,
    gives specs a deterministic `key`, and makes the price `AMBIGUOUS` when more than one verified
    current amount is shown. The shared amount checks are `rules._verify_amount`.
-   **Stage 2, availability:** `app/services/availability.py`; `AvailabilityStatus` in
    `app/schemas/common.py`. `CompetitorProduct.availability_status`, `availability_reason`,
    `availability_proposed`.
-   **Stage 3, Task A:** `MatchProposal` (LLM contract), `rules.check_match_proposal`,
    `prompts.MATCH_SYSTEM`/`match_user` (no Stompbox SKU sent), `agent._propose`.
    `CompetitorProduct.match_proposal`, `match_proposal_status`, `match_proposal_error`. Usage:
    `llm_calls`/`llm_failures` count both calls; `llm_match_calls`/`llm_match_failures` Task A only.
    `INTELLIGENCE_MATCH_PROPOSALS` (default true). Both calls share `LLM2_MAX_COMPLETION_TOKENS`
    (the optional separate Task A cap in the plan was not added: the client takes one cap).
-   **Stage 4, matching:** `MatchStatus.EQUIVALENT_VARIANT`; `matcher.decide` grants it when the
    finish is the only critical conflict and nothing else needs review (a finish-only conflict with
    an open review reason is `REVIEW_REQUIRED`). `matcher.reconcile` applies verified LLM 2 issues
    and proposals (review only). `alternative_price`, `price_kind`, `availability`,
    `proposal_agreement` on `CandidateMatch`; `alternative_prices` on summaries.
    Approved behaviour change: a finish-only conflict that was `SIMILAR_MATCH` is now
    `EQUIVALENT_VARIANT`.
-   **Stage 5, Task C and integration:** `app/services/matching/view.py` builds
    `CandidateMatch.intelligence`. Additive SSE payload fields and run-summary counts
    (`docs/pipeline.md`). Dashboard: separate "Alternative products" section without a difference
    column, availability pills, LLM 2 proposal and uncertainties in the evidence drawer
    (`docs/dashboard.md`). The dashboard fixture was regenerated
    (`python -m tests.make_dashboard_fixture`, fakes only).
-   **Fakes:** `FakeExtractLLM(by_url, proposals)` answers both schemas; a page without a registered
    proposal gets a quote-less default that Python marks `UNSUPPORTED`, so matching is unchanged.
-   Stages 1-5 were built and tested with fakes only. Stage 6 then ran live twice (see "Live
    evaluation runs" below); accuracy, latency and cost per full pipeline run of the two-call design
    are still unmeasured beyond those two small runs.
-   **Audit fixes (19 Nov 2026, uncommitted):**
    -   Variant prices: `app/agents/intelligence/variants.py` (`associate`). `check_extraction(..., product)`
        keeps a price only when exactly one verified option (or the main price labelled by the selected
        options) matches every known catalogue variant attribute; otherwise `AMBIGUOUS`. Non-matching
        option prices carry `matches_catalogue_variant=false` and `variant_checks`.
    -   Electronics: `matcher.check_electronics` (pickups HSS/SSS/HH/HSH/SSH, active/passive,
        electro-acoustic) using verified electronics specs (`EvidenceSource.EXTRACTED_SPEC`) and
        title/URL evidence; the three attributes are in `CONFIRM_REQUIRED`. New profile fields:
        `pickups`, `electronics`, `electro` (from the "Electro-acoustic" / "Acoustic guitar" subcategory).
    -   Usage: `llm_failures` / `llm_match_failures` include pages not attempted after an account error,
        so they can exceed `llm_calls` / `llm_match_calls` (documented in the schema).
    -   A Task A failure alone still makes the extraction stage PARTIAL and the run PARTIALLY_COMPLETED
        (unchanged policy, now tested).
-   **Reliability fixes (19 Nov 2026, uncommitted):** an option's `selected_value` is kept only with explicit
    selection wording around the whole value in its verified quote (`rules.selection_evidence`; a "Colour: Black"
    heading is not evidence); otherwise `selected_value=null` and LLM 2's claim stays in `claimed_selected_value`.
    Pickup layout codes are detected case-insensitively as whole tokens and normalised to upper case.
-   **Stage 6.1 corrections (19 Nov 2026, uncommitted; from live run `intelligence-20261119T193707Z-8189db`):**
    `sale_indicated` only from SALE-category ignored prices or sale wording in the price quote; delivery charges are
    categorised (`ignored_price_details`) and never become a product price (`prices.is_delivery_context`).
    Specification categories in `app/services/specs.py` (power_supply, output_power, controls, electronics,
    pickup_configuration, other); only electronics / pickup_configuration specs feed the electronics rules.
    Controlled finish aliases in `attributes.FINISH_ALIASES` ("ice tea" for "Iced Tea Burst"); no fuzzy matching.
-   **Stage 6.1.1 integration fixes (19 Nov 2026, uncommitted):** `matching.price.validate_price` re-checks delivery
    context and delivery-listed amounts (re-derived from the raw ignored texts); issue-quote price counting excludes
    delivery/finance amounts; delivery discounts ("Save £5 on delivery") never set `sale_indicated`, genuine sales
    mentioning delivery still do. Pickup selector/switch/toggle/blend/knob specs are controls; the matcher always
    re-derives spec categories (stored ones are never trusted; disagreements go to `spec_category_mismatches`) and an
    electronics spec confirms an electro-acoustic only if it names a device/type/maker (`specs.is_meaningful_electronics`).
    A finish named together with another finish is UNVERIFIED in matching and UNKNOWN in option-price association
    (`attributes.other_finish_colours`). Frontend types: `NormalizedSpec`, `SpecCategory`, `IgnoredPriceRecord`
    (checked against the backend enums by `tests/test_stage611_integration.py`).

### Live evaluation runs (Stage 6 and Stage 6.2, 19 Nov 2026)

Both used the intelligence CLI (not the dashboard pipeline), Exa and `openai/gpt-6-luna`, for
SB-001 (Boss RC-5) and SB-028 (Epiphone Les Paul Standard '60s LH, Iced Tea Burst) on
Andertons and Gear4music. Run files are in `data/intelligence_runs/` (git-ignored, never edited);
page text is not stored, only verified quotes and metadata.

| Run | Searches / pages | LLM 2 calls | Cost (search + LLM) | Purpose |
|---|---|---|---|---|
| Stage 6 (`...193707Z-8189db`) | 4 / 4 | 8 (4 Task A), 0 failed | $0.032 + $0.0050 | First live evaluation; findings fixed in 6.1 and 6.1.1 |
| Stage 6.2 (`...210447Z-8f9d6e`) | 4 / 4 | 8 (4 Task A), 0 failed | $0.032 + $0.0049 | Re-check after 6.1 and 6.1.1 |

-   **Stage 6.2 outcomes (current code, recomputed offline from the run file):** Andertons RC-5
    EXACT_MATCH, £219.00, comparable (+£29.01), IN_STOCK; Gear4music RC-5 secondhand SIMILAR_MATCH
    (used), no price, UNKNOWN availability ("currently unavailable for delivery to your country");
    Andertons and Gear4music LP Standard '60s **Figured** LH Iced Tea REVIEW_REQUIRED (Python now
    confirms the approved Figured designation; the stored LLM 2 proposals, made before the decision,
    flagged Figured as a different model, U-4), OUT_OF_STOCK. No
    matching file was saved for Stage 6.2.
-   **Verified live in 6.2:** Task A proposals (4 of 4 completed and verified), specification
    categories on real pages, the Figured disagreement, a geographic delivery restriction, an
    "OTHER" ignored amount. **Offline only (unit and integration tests):** delivery-price exclusion
    (seen live only in Stage 6), delivery discounts, genuine sale wording (no sale page in 6.2),
    the Ice Tea alias (Stage 6 page only), mixed finishes, option prices (no live page had options)
    and spec-category tampering.

### Stage 6.3, pre-demo hardening (20 Nov 2026, uncommitted)

-   **Geographic availability (6.3.1):** `availability.geographic_restriction`. A restriction in the
    availability text ("unavailable for delivery to your country", "cannot ship to your region",
    "not available for delivery") is `UNKNOWN` whatever LLM 2 proposes. Generic unavailability
    ("currently unavailable", "not available for purchase") is `OUT_OF_STOCK` only when the page shows
    no restriction anywhere; explicit stock wording ("Out of stock", "Sold out", "Not in stock") stays
    `OUT_OF_STOCK` even when LLM 2 proposes only `UNKNOWN` (a contrary proposal still gives `UNKNOWN`).
    Matching keeps using the status stored at extraction time.
-   **P-5 refinement (6.3.1) approved by the user (20 Nov 2026).**
-   **SB-028 identity (6.3.2), DECIDED (user, 20 Nov 2026):** SB-028 is the Epiphone Les Paul
    Standard '60s **Figured** Left-Handed, Iced Tea Burst. The catalogue record is unchanged (it does
    not say "Figured"). Figured is a model designation, not a finish alias:
    `attributes.APPROVED_DESIGNATIONS` (SB-028 only) and `matcher.check_designation` (critical,
    `CONFIRM_REQUIRED`, not cosmetic). "Figured" as a whole word in a title, URL or extracted
    model/SKU, or a listed Figured SKU (EIGLP6ITNHRL, EIGLP6ITNH1L), confirms it; the plain-top SKU
    (EILS6ITNH1) is a CONFLICT (`SIMILAR_MATCH`); silence or contradiction is `REVIEW_REQUIRED`;
    specifications never count. Task A receives `model_designation`. The temporary hold
    (`IDENTITY_DECISION_PENDING`) was removed. Stored Stage 6.2 proposals (made without the
    designation) still hold the two Figured pages at `REVIEW_REQUIRED` (U-4 unchanged).
-   **SB-028 price baseline: NOT CONFIRMED.** The catalogue £579 was recorded as a sale price ("was
    £599", 10 Nov 2026). The catalogue price is unchanged and used as for every product; £599 is not
    used and no regular price is assumed. It is not independently verified and awaits the user.
-   **UK evidence in Live Runs (6.3.3):** `components/UkEvidence.tsx` (shared with Domain Review);
    a "Discovered domains and UK evidence" section (`RunDomains.DiscoveredDomainsTable`, from the run
    result) and a UK status pill on each `competitor_discovered` feed item (`ukRelevance.ukEventStatus`).
-   **Phone layout fix (6.3.6):** `.stack > *, .stack-sm > *, .new-run > * { min-width: 0 }`; wide
    tables widened Live Runs at 390 px.
-   **Workflow test:** `tests/test_workflow_integration.py` (discover, review via API, re-run, extract,
    match, stream; temporary database, fakes only).

### Stage 7 live demo and post-demo fixes (20 Nov 2026, uncommitted)

-   **Live demo (user-authorized, $2.00 ceiling):** two dashboard-pipeline runs on a demo review database
    (`data/review/demo-20261120.sqlite3`, a copy; the real database was not touched), SB-001 and SB-028.
    Run 1 `run-20261120T202714Z-28317c80` (LLM 1 discovery + Andertons and Gear4music) and Run 2
    `run-20261120T203804Z-3f13cfde` (reviewer-approved `wildwireguitars.com` only, benchmark off;
    `idealo.co.uk` rejected). Saved as `data/intelligence_runs/pipeline-<run>.json|.events.json|.run.json`
    (git-ignored, never edited). Reported spend: Exa $0.229, OpenRouter $0.0167.
-   **SB-028 baseline warning:** `frontend/src/priceBaseline.ts` and `components/BaselineWarning.tsx` label the
    £579 Stompbox price as an unconfirmed baseline (SB-028 only) in the product header, the evidence drawer and
    the Overview price cards. Display only; extraction, matching, differences and benchmark rules unchanged.
-   **Price: current AND ignored amount:** `rules._verify_amount` now uses the deterministic category of
    the ignored text: sale wording gives `EXCLUDED_SALE_PRICE`, finance wording `AMBIGUOUS`, a bare amount
    (category "other") `CONFLICT`. The amount is never used in any case. `matching.price.validate_price`
    relabels an older stored `EXCLUDED_SALE_PRICE` with no sale wording (quote or ignored texts) as
    `CONFLICT`; saved run files are not rewritten. (Stage 7 Andertons £599 for SB-028.)
-   **Auto-approval evidence URLs:** the rule is unchanged (2 distinct `DISCOVERY_RESULT` URLs from the
    global product searches). Domain-profile pages (about/contact/terms) carry UK evidence and count for
    `independent_sources` only. The reason now says so (`N discovery evidence URL(s) < 2: ...`).
-   **Finish alias (user-approved 20 Nov 2026):** `FINISH_ALIASES["iced tea burst"] = ("ice tea", "iced tea")`.
    Exact whole-phrase match only; mixed finishes ("Iced Tea / Ebony", "Iced Tea Sunburst") stay review; the
    alias confirms the finish only and never overrides a stricter LLM 2 proposal.
-   **Investigated, unchanged:** muziker.co.uk (a verified non-UK operator alone is insufficient, not a
    contradiction; a UK delivery quote is missing).
-   **Known follow-up:** at 390 px the product-switcher chips on Product Intelligence overflow the right edge.
-   **Comparison table:** the Evidence column is pinned to the right edge (`.col-action`, sticky) and long
    "not compared" reasons wrap; tables still scroll sideways on narrow screens.
