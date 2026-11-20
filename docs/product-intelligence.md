# Product Intelligence Agent (LLM 2)

Implemented in `backend/app/agents/intelligence/`. For each selected Stompbox
product, the agent searches the approved competitor domains, retrieves the
candidate pages, and (when LLM 2 is configured) runs LLM 2's tasks on them.
Deterministic Python checks every LLM 2 value against the page text. The
architecture and approved decisions (D-20 to D-34) are in
`stompbox_uk_competitor_intelligence_implementation_plan.md` §3.6 and §8.6.

LLM 2 is one logical agent with three tasks, each with its own prompt and
structured output, on the existing OpenRouter client and `LLM2_MODEL`:

| Task | Where | What |
|---|---|---|
| B, extraction | 1st call per retrieved page (`PageExtraction`) | Title, brand, model, SKU, price, currency, availability, specifications, options, per-option prices and issues, each with verbatim quotes |
| A, match proposal | 2nd call, only on a retrieved page Task B classified as `product_page` (`MatchProposal`) | `EXACT_MATCH`, `EQUIVALENT_VARIANT`, `SIMILAR_ALTERNATIVE` or `NOT_A_MATCH`, with reasons and differing attributes, each quoting the page |
| C, combined output | Python, no call (`app/services/matching/view.py`) | One evidence-backed view per candidate: seller, match, price, availability, uncertainties |

LLM 2 proposes; Python validates and decides. The final match status, price
validation, price kind (direct or alternative) and availability status are set
deterministically (see `product-matching.md`). Search and retrieval are
unchanged.

**Not done by LLM 2:** domain approval or search permission, search and
retrieval, quote/price/currency validation, the final match decision,
benchmark eligibility and orchestration.

## Status: what has been tested (12 Nov 2026)

| Area | State |
|---|---|
| **No-LLM mode** (`LLM_DISABLED`) | **Supported and the current default.** No OpenRouter key or model is needed. Pages are searched and retrieved, every retrieved page is `SKIPPED_NO_LLM` with the reason, and no product field is filled in |
| **Real search and retrieval** | **Tested live with Exa.** See the results below |
| **Deterministic quote and price checks** | Tested with unit tests and on real retrieved page text, independently of any LLM |
| **OpenRouter / LLM 2 path** | Implemented and tested end to end with mocked HTTP: strict JSON-schema request to `LLM2_MODEL`, response checked, usage and cost recorded, malformed output handled, account errors stop further calls |
| **Real LLM extraction, accuracy and cost** | **Deferred** until `OPENROUTER_API_KEY` and `LLM2_MODEL` are configured. Nothing about extraction quality or cost per page has been measured |

**To activate:** set `OPENROUTER_API_KEY` and `LLM2_MODEL` (a model that
supports structured outputs on OpenRouter) in `backend/.env`. No code change
is needed. `GET /api/intelligence/config` then reports `LLM_ENABLED`.

**Configured model (14 Nov 2026):** `LLM2_MODEL=openai/gpt-6-luna`,
`LLM2_REASONING_EFFORT=low`, `LLM2_MAX_COMPLETION_TOKENS=8000`, and
`LLM2_TEMPERATURE` unset. It is a reasoning model that does not accept
`temperature`; temperature and reasoning effort are sent only when set. The
key is still to be added by the user, so the mode stays `LLM_DISABLED` until
then.

**Live no-LLM run** (API, Exa, scratch copy of the review database):

- Setup: products SB-001 and SB-060 on 5 domains (Andertons, Gear4music,
  Gibson, and the reviewer-approved `promenademusic.co.uk` and `bopdj.com`).
- Cost: 10 searches ($0.070); 20 of 20 pages retrieved ($0.020); 0 LLM calls.
- Every candidate was `SKIPPED_NO_LLM`, with all product fields null.
- Gear4music pages were fetched from the UK store.
- Target filtering:
  - an approved directory (`idealo.co.uk`) and a rejected domain
    (`scan.co.uk`) were listed in `skipped_targets`;
  - none of the 4 pending domains was a target;
  - a request naming them in `domains` was refused with 422.

## Modes

| | `LLM_ENABLED` | `LLM_DISABLED` |
|---|---|---|
| When | `OPENROUTER_API_KEY` and `LLM2_MODEL` set, and `INTELLIGENCE_USE_LLM=true` (default) | Either is missing, or `INTELLIGENCE_USE_LLM=false` |
| Search and page retrieval | Yes | Yes (identical) |
| Product fields | Extracted by LLM 2, then checked | **All null**; status `SKIPPED_NO_LLM` |

- LLM 2 is configured separately from LLM 1. `LLM1_MODEL` never enables extraction.
- There is no default model.
- Missing LLM configuration is never an error. Only a missing search provider
  or key gives HTTP 503 (CLI exit code 3).

## Flow

1. **Search targets.** `eligible_search_targets()` returns:
   - the 13 preconfigured competitors, unchanged;
   - every reviewer-`APPROVED` domain from Phase 4.

   Some domains are never searched:
   - **Pending domains** (discovered but not yet reviewed): not included.
   - **Rejected domains:** listed in `skipped_targets`.
   - **Approved domains of an excluded type** (default: `directory`, because a
     directory lists other sellers' prices): listed in `skipped_targets`.
   - **Stored decisions on preconfigured competitors** are ignored, so Gibson stays a non-benchmark reference.

   Each target carries its company type, approval status and benchmark
   eligibility (`price_benchmark`) unchanged. An approved marketplace (Amazon,
   eBay) is searched but stays `price_benchmark=false`. An API request may
   narrow the targets with `domains`, but only to a subset of the eligible
   ones (422 otherwise).
2. **Queries.** By default there is one search per domain, combining brand,
   model and product name. The name carries variant details such as
   handedness and finish. When the catalogue has specifications (14 of 100
   products), their first segment is added. With
   `INTELLIGENCE_QUERIES_PER_DOMAIN=2`, an exact `"<brand> <model>"` query is
   added.
3. **Domain-restricted search** runs on the configured provider (Exa). The
   provider layer enforces scope again; off-domain results are dropped and
   counted (`out_of_scope_dropped`).
4. **Page selection** (deterministic, to bound cost; not an identity decision):
   - results whose title or URL names the model come first, then those naming the brand;
   - results naming neither are not selected;
   - at most `INTELLIGENCE_MAX_PAGES_PER_DOMAIN` (2) pages per domain and
     `INTELLIGENCE_MAX_PAGES_PER_PRODUCT` (20) per product.

   Every unselected result is listed in `not_selected` with its reason.
5. **Retrieval** is one batched provider call per product.
   - It fetches the **canonical URL**, which maps locale storefronts such as
     Gear4music `/us/en/` to the UK store with GBP prices. The provider's
     original URL is kept on the search hit as evidence.
   - Failed, blocked (bot-check) and empty pages are never sent to LLM 2.
6. **Extraction (LLM 2):**
   - LLM 2 receives the first `INTELLIGENCE_PAGE_CHARS` (12,000) characters
     of page text as delimited, untrusted data, plus the reference product
     for context only.
   - For each field it returns the value **and a verbatim quote**.
   - It is told never to return was, RRP, original, list, compare-at, saving,
     percentage, finance, deposit, bundle or shipping amounts as the price,
     and to list any such text in `ignored_prices`.
7. **Checks** (deterministic, `rules.check_extraction` and `services/prices.py`). Each field gets a `FieldEvidence` status.

| Status | Meaning |
|---|---|
| `EXTRACTED` | The quote is in the page text (or page title) and the value is in the quote |
| `NOT_FOUND` | LLM 2 found no value. A missing price is never 0 |
| `UNSUPPORTED` | No quote, the quote is not in the page, or the value is not in its quote. Catches invented values and snippet-only prices |
| `EXCLUDED_SALE_PRICE` | Every occurrence of the amount in its quote is shown as was, RRP, originally, save, list price, compare at, "£x off", or Shopify "Regular price" next to "Sale price". Also when LLM 2 itself listed that amount in `ignored_prices` |
| `AMBIGUOUS` | A conditional price ("from", per month, deposit, or `price_is_conditional`), no explicit currency (a bare `$`), or not a single-product page |
| `CONFLICT` | The stated currency differs from the symbol on the page, or the quote shows several currencies |
| `INVALID` | Not exactly one amount with a currency marker, or zero or negative |

**Task B additions** (`rules.check_extraction`):

- **Per-option prices** (`option_prices`) get the same checks as the main price.
  Several verified current amounts (main and option prices, or a verified
  `MULTIPLE_PRICES`/`CONFLICTING_INFORMATION` issue on `price` whose quotes show
  two current amounts) are resolved by `variants.associate` (audit fix, P-6):
  - The candidates are the verified option prices, plus the main price labelled
    with the options **explicitly** shown as selected (if any).
  - An option counts as selected only when its verified quote marks the whole
    value with selection wording (`rules.selection_evidence`): "Selected: Black",
    "Selected colour - Black", "Black (selected)", "Black - selected", "✓ Black",
    "[x] Black", or "You have selected Black".
    - A value merely appearing on the page, or a heading such as "Colour: Black",
      is not evidence. Retrieved pages use the same form for spec bullets
      ("- Colour: Ocean Blue Grey") and hardware ("Finish: Chrome").
    - "Selected: Black Sunburst" and "Jet Black (selected)" do not select
      "Black".
    - Without evidence, the option is kept with `selected_value=null`; LLM 2's
      claim stays in `claimed_selected_value`. The main price is then not
      treated as the Stompbox variant's price, and it becomes `AMBIGUOUS` unless
      an option-specific price matches.
  - Each label is checked against every known catalogue variant attribute:
    finish, handedness, size, strings, configuration, bundle, pickups,
    active/passive and electro-acoustic. Each attribute is `MATCH`, `CONFLICT`,
    `UNKNOWN` or `ABSENT`.
  - An option matches only with no `CONFLICT` and no `UNKNOWN`. So a finish match
    alone is not enough: a left-handed product needs "left-handed" stated, and
    "Black Left-Handed" conflicts with a right-handed Black product.
  - Handedness, size, strings and electronics stated in the verified product
    title apply to every option, unless some option states them itself.
  - The price is kept only when **exactly one** option matches. Otherwise it is
    `AMBIGUOUS`: no option matches, several match, an attribute can't be
    verified, the catalogue names no finish while the options vary by colour,
    the page is not a single-product page, or a verified issue names a price that
    no option accounts for.
  - Non-matching options stay in `option_prices` as alternative information
    (`matches_catalogue_variant=false`, `variant_checks`).
  - The lowest, first or default-option price is never chosen. Without the
    catalogue product, several prices stay `AMBIGUOUS` as before.
- **Options** are kept only if their quote and every value are in the page.
- **Issues** (`AMBIGUOUS_VARIANT`, `CONFLICTING_INFORMATION`, `MULTIPLE_PRICES`,
  `MISSING_INFORMATION`) are `verified` only if every quote is in the page.
  Unverified issues never affect anything.
- **Specifications** get a deterministic `key` (`strings`, `pickups`, `finish`,
  ...; otherwise a slug) and a `category` (`app/services/specs.py`, Stage 6.1):
  `power_supply`, `output_power`, `controls`, `electronics`,
  `pickup_configuration` or `other`.
  - "Power: 9V AC Adapter/Battery" is a power supply; "Power: 50W" is output
    power. A value that mixes watts with supply wording is `other`, because
    output wattage is never inferred from a supply voltage.
  - "Controls" is never electronics.
  - LLM 2's `normalized_name` is only a lookup hint, and never sets a
    category-dependent key (`output_power`, `power_supply`, `controls`,
    `electronics`, `pickups`).
- **Ignored prices and the sale flag** (Stage 6.1):
  - Each ignored price gets a category in `ignored_price_details` (`SALE`,
    `DELIVERY`, `FINANCE`, `OTHER`). The raw texts stay in `ignored_prices` for
    audit.
  - `sale_indicated` is set only by a `SALE` entry or sale wording in the price
    quote. Delivery, shipping, postage and click & collect charges never set it.
  - A delivery amount never becomes a price. If its quote shows it as a
    delivery charge it is `INVALID`; if the same amount is listed as a delivery
    charge it is `AMBIGUOUS`.
  - "Free delivery £219" is still a product price.
  - Stage 6.1.1:
    - A delivery discount ("Save £5 on delivery", "£5 off shipping") is never
      sale evidence. A product sale that also mentions delivery ("Was £10 and
      free delivery") still is.
    - Amounts counted from verified multiple-price issue quotes exclude
      delivery, collection and finance charges.
    - Matching re-checks delivery context independently (`validate_price`).
- **Availability** (`app/services/availability.py`, P-5, U-3): the verified text
  maps to `IN_STOCK`, `OUT_OF_STOCK`, `PREORDER`, `BACKORDER`, `DISCONTINUED` or
  `UNKNOWN`. Missing, unverified, unrecognised, mixed or contradicted text, or an
  LLM 2 proposal that disagrees with the mapping, gives `UNKNOWN`. "Only 2 left"
  or "Low stock" is `IN_STOCK` only when the page shows the product can be bought
  now (in stock, add to basket, buy now).

**Task A checks** (`rules.check_match_proposal`): each reason and differing
attribute must quote the page text (never the catalogue product), and a
differing attribute's page value must be in its quote. Whether a difference is
cosmetic (finish or colour only) is decided in Python from the attribute name.
Statuses: `COMPLETED` (at least one verified quote), `UNSUPPORTED` (none:
ignored), `SKIPPED_NO_LLM`, `SKIPPED_NOT_ELIGIBLE` (not a retrieved
single-product page, extraction failed, or `INTELLIGENCE_MATCH_PROPOSALS=false`)
and `FAILED`. An account-level error on either call stops all further LLM 2
calls in the run. Usage counts both calls (`llm_calls`) and Task A separately
(`llm_match_calls`, `llm_match_failures`); both tasks share
`LLM2_MAX_COMPLETION_TOKENS`. `llm_calls` counts requests actually sent. The
failure counters also include pages and proposals **not attempted** after an
account-level error, so `llm_failures` can exceed `llm_calls`, and
`llm_match_failures` can exceed `llm_match_calls`.

A field's value appears on the `CompetitorProduct` only when its status is
`EXTRACTED`. Otherwise it is null, and the rejected value stays in
`field_evidence` for audit. Specifications are kept only if their value
appears in the page.

**Sale prices:** on "Was £219 Now £189", the current displayed price £189 is
extracted and `sale_indicated=true` is set for information. Nothing is
calculated from the was price, and no saving or discount is computed. Whether
a sale-period price is an acceptable benchmark is a Phase 6 policy question
(see the SB-028 note in `CLAUDE.md`).

## Record: `CompetitorProduct`

| Group | Fields |
|---|---|
| Identity | `candidate_id`, `stompbox_product_id`, `competitor` (domain, name, company type, approval status, `price_benchmark`) |
| Source | `product_url`; `search` (query, provider, request ID, rank, original and canonical URL, title, snippet, retrieval time, model/brand mention flags); `page` (retrieval status, requested and final URL, title, characters retrieved and sent to the LLM, retrieval time, cache source, error) |
| Extraction | `extraction_status` (`COMPLETED`, `SKIPPED_NO_LLM`, `SKIPPED_NOT_RETRIEVED`, `FAILED`), `extraction_error`, `page_type` (`product_page`, `listing_page`, `other`) |
| Values | `product_title`, `brand`, `model`, `sku`, `price` (decimal), `currency` (ISO), `price_text` (as displayed), `availability` (verbatim), `specifications` |
| Evidence | `field_evidence[]` (field, status, proposed value, verbatim quote, note), `ignored_prices`, `sale_indicated` |
| LLM | `llm_confidence` (self-reported, uncalibrated), `llm_notes`, `llm_request_ids` |

The API never returns the full page text. Only quotes and metadata are
returned, and the provider's raw payloads are not exposed.

## Usage and cost

`usage` holds:
- search requests, errors and cost;
- retrieval requests, errors, pages requested and pages OK, and retrieval cost;
- LLM calls, failures, prompt and completion tokens, and cost.

Exa reports its cost per request. OpenRouter reports cost in USD-denominated
credits; `llm_cost_usd` is null when no cost was reported.

**Default size of a run:** 1 search per eligible domain per product (15
domains gives 15 searches, about $0.10), at most 20 page retrievals and at
most 20 LLM 2 calls per product. Narrow the run with `domains` to cut cost.

## Failure behaviour

| Failure | Effect |
|---|---|
| Search fails on one domain | Recorded in that domain's `errors` and in warnings. Other domains continue |
| Retrieval call fails | That batch's pages become `SKIPPED_NOT_RETRIEVED` with the error |
| Page failed, blocked or empty | `SKIPPED_NOT_RETRIEVED`. Not sent to LLM 2 |
| Invalid JSON or schema from LLM 2 | One repair attempt (OpenRouter client), then `FAILED` for that page only. Nothing is filled in |
| Truncated LLM 2 output (`finish_reason=length`, e.g. reasoning used the whole `LLM2_MAX_COMPLETION_TOKENS`) | `FAILED` for that page with the token counts. Never parsed and never repaired. The tokens and cost spent are still added to `usage`. Other pages continue |
| LLM provider error | `FAILED` for that page, with a UI-safe message (keys never included) |
| LLM account error (HTTP 401/403 auth, 402 no credits) | `FAILED` for that page. Remaining pages are **not sent** (`FAILED`, "Not attempted"), with one warning |
| Blank `LLM2_MODEL` or key in `.env` | Treated as unset: `LLM_DISABLED` |
| Provider without retrieval (Brave) | Candidates recorded, pages not fetched |

## Interfaces

- `GET /api/intelligence/config`: mode, the reason when disabled, the LLM 2
  model when enabled, search targets, skipped targets and limits. No secrets.
- `POST /api/intelligence/runs` with body `{"product_ids": [...] (1–5), "domains": [...]?}`
  returns an `IntelligenceRunResult`:
  - per product: the queries, a per-domain search summary, the candidates and the unselected results;
  - usage and warnings.

  The run is synchronous and is not stored.
- CLI: `python -m app.agents.intelligence.cli SB-001 --domains andertons.co.uk gear4music.com`
  writes the full result to `data/intelligence_runs/` (git-ignored).

## Limitations

- **Not yet run with a real LLM:** the OpenRouter configuration is
  postponed, so LLM 2 has only been tested with mocked responses. Prompt
  quality, extraction accuracy and cost per page are unmeasured.
- **Sale detection is text-based:** Exa returns plain page text, so the
  strike-through formatting is lost. Labelled pairs are handled ("Was/Now",
  "RRP", Shopify "Regular price … Sale price", and Magento "Regular Price …
  Special Price", which was seen live on BopDJ). Was/now detection relies on wording;
  an unlabelled crossed-out amount shown next to the current price relies on
  LLM 2 and the `ignored_prices` cross-check.
- **Only the start of each page is used:** LLM 2 sees the first 12,000
  characters. A price further down is reported as `NOT_FOUND`.
- **Selection is shallow:** it keys on brand and model mentions in titles
  and URLs, so guide, blog and forum pages naming the brand can take a
  domain's page slots. In the live run these included `forum.gibson.com`
  threads (subdomains of an approved domain are in scope) and category pages.
  LLM 2 marks them `other` or `listing_page`, which keeps their price out.
  In no-LLM mode they are simply retrieved pages.
- **Condition is not extracted:** used and secondhand listings (e.g.
  Gear4music "Secondhand") are retrieved like new ones. Their title carries
  the condition for Phase 6.
- **Synchronous requests:** a large run can take a minute or more.
  Streaming over SSE is Phase 7.

## Geographic availability (Stage 6.3.1)

Stage 6.2 retrieved a Gear4music page that said "This product is currently
unavailable for delivery to your country": the page was rendered for a visitor
outside the UK (retrieval sends no location). It describes delivery, not stock.

- A geographic or delivery restriction in the verified availability text
  (`availability.geographic_restriction`: "unavailable for delivery to your
  country", "not available in your region", "cannot ship to your location",
  "do not ship to the United Kingdom", "not available for delivery") is always
  `UNKNOWN`, whatever LLM 2 proposes and whatever else the text says.
- Generic unavailability ("currently unavailable", "not available", "This
  product is currently not available for purchase") stays `OUT_OF_STOCK` when
  the page shows no restriction, because it states that the product cannot be
  bought; if a restriction appears anywhere on the page it may be the cause, so
  the status is `UNKNOWN`.
- Explicit stock wording ("Out of stock", "Sold out", "Not in stock", "No
  stock") is `OUT_OF_STOCK`; an LLM 2 proposal of only `UNKNOWN` does not
  override it. Any other disagreeing proposal still gives `UNKNOWN`.
- Footer wording such as "We deliver to over 100 countries" or "No delivery
  charge in the UK" is not a restriction.
- Matching uses the status recorded at extraction; older run files keep theirs.
- Approved by the user on 20 November 2026 as a refinement of P-5.

Tests: `tests/test_availability.py`, `tests/test_intelligence_tasks.py`,
`tests/test_workflow_integration.py`.

## Live evaluation (Stage 6 and Stage 6.2, 19 November 2026)

Two small live runs (CLI, Exa, `openai/gpt-6-luna`): SB-001 and SB-028 on
Andertons and Gear4music, 4 searches, 4 pages and 8 LLM 2 calls each (4 of
them Task A), no failures, about $0.032 search and retrieval plus $0.005 LLM
per run. Page text is not stored, only verified quotes.

Verified live in Stage 6.2: Task A proposals on real pages (all verified),
specification categories, the SB-028 "Figured" disagreement (since resolved:
SB-028 is the approved Figured model, and Task A now receives
`model_designation`), a geographic
delivery restriction and an "OTHER" ignored amount. Verified offline only:
delivery-price exclusion (seen live in Stage 6 only), delivery discounts,
genuine sale wording, the Ice Tea alias, mixed finishes, option prices and
spec-category tampering. Extraction accuracy at scale, latency and cost per full
pipeline run are not measured yet.
