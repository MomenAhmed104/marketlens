# Product intelligence (LLM 2), Phase 5

Implemented in `backend/app/agents/intelligence/`. For each selected Stompbox
product, the agent searches the approved competitor domains, retrieves the
candidate pages, and (when LLM 2 is configured) extracts structured product
fields. Deterministic Python checks every extracted value against the page
text.

**Out of scope here (Phase 6):**
- deciding whether a page is the same product (exact or similar);
- checking prices against the catalogue;
- comparing prices.

No record in this phase claims a match.

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
