# Product matching and price validation, Phase 6

Implemented in `backend/app/services/matching/`. Deterministic Python only:
no LLM, no search, no API key. It takes a Phase 5 `IntelligenceRunResult`
and returns, for every candidate:
- a match status, with confidence and reasons;
- brand, model, variant and condition checks;
- a separate price validation;
- whether the pair qualifies for direct UK price comparison.

Match validity and price validity are independent. An `EXACT_MATCH` can have
a missing or invalid price, and a `VALID` price says nothing about identity.

## Evidence used

Identity evidence is read from three **independent groups**:

| Group | Sources |
|---|---|
| title | Retrieved page title (only when the page was retrieved), search-result title, LLM 2 product title |
| url | Product URL path and the search-result URL path |
| field | LLM 2 `model` and `sku` |

- LLM 2 fields exist only when extraction ran and Phase 5 found each
  field's quote in the page.
- Search snippets are never used for identity or price.
- Stompbox SKUs are internal shop codes (e.g. `child-6136`), so they are not
  compared.
- Every check lists the exact texts it used (`EvidenceRef`).

## Match statuses

| Status | Meaning |
|---|---|
| `EXACT_MATCH` | Same product identity and required variants. Can be compared if the price, condition and benchmark checks also pass |
| `SIMILAR_MATCH` | Related alternative, not the same product or offer: a sibling model, a different generation, a conflicting variant, a bundle, or a used/refurbished item |
| `REVIEW_REQUIRED` | Possible match with unresolved evidence. `review_reasons` lists what a person must check |
| `NO_MATCH` | The evidence does not support a match |

### Decision rules (`matcher.decide`), in order

1. **Not a single-product page: `NO_MATCH`.**
   - With LLM 2, this is the page type it reported (listing or other).
   - Without LLM 2, URL patterns are used: `forum.`/`blog.`/`learn.` hosts;
     `/blog/`, `/topic/`, `/guide/`, `-guide`, `/compare/`, `/category/`,
     `/sale`, `-vs-` paths.
2. **Different extracted brand: `NO_MATCH`.** Brands are never treated as
   aliases of each other.
3. **Model found** (exact identifier, flexible separators and word
   boundaries, so `RC-5` matches `rc5` but not `RC-500`):
   - Any critical variant **conflict**: `SIMILAR_MATCH`.
   - Condition used or refurbished: `SIMILAR_MATCH`.
   - Otherwise `REVIEW_REQUIRED` if any of the following holds, else `EXACT_MATCH`:
     - a required variant is unconfirmed (handedness, finish, generation, edition, configuration);
     - the brand was not found;
     - the model was found in **only one evidence group** (title similarity
       alone is never enough);
     - the title also names a sibling model;
     - the page was not retrieved.
4. **A different model:**
   - A sibling of the same code family (`RC-500` for `RC-5`, `BAA2` for
     `BAA2CE`), or a different LLM 2 model with the same brand: `SIMILAR_MATCH`.
   - Otherwise `NO_MATCH`.
5. **Model line without its generation marker** (e.g. "Micro Looper" for
   "Micro Looper II"):
   - a different generation shown: `SIMILAR_MATCH`;
   - no generation shown: `REVIEW_REQUIRED`.
6. **No model found:**
   - Same brand and product type on a page LLM 2 classified as a product
     page: `SIMILAR_MATCH`.
   - Otherwise `NO_MATCH`. Without LLM 2 such pages are usually category pages.

### Match confidence

- `HIGH`: `EXACT_MATCH` with the model confirmed by an LLM 2 field plus title or URL.
- `MEDIUM`: `EXACT_MATCH` from page title and URL only (no-LLM mode).
- `LOW`: every other status.

This is a rule-based label, not a calibrated probability.

## Variant checks

Variants are profiled from the catalogue row
(`attributes.profile`): the name, model, subcategory and specifications.

| Attribute | Stompbox source | Conflict (blocks EXACT) | Unconfirmed |
|---|---|---|---|
| handedness | "Left-Handed", "LH", "left-handed" subcategory. Right-handed is the unstated default | Listing says the other hand | Left-handed product, listing silent: REVIEW. Exception: an exact single-token model code (e.g. `BAT4CEL`) implies it |
| finish | Last comma segment of the name when it names a colour ("Iced Tea Burst", "Sonic Blue") | Listing colours share no colour word with the finish | Full finish name absent (incl. a partial overlap such as "Bourbon Burst"): REVIEW |
| strings | "5-String", "7 String"; default 6 for guitars, 4 for basses | A different stated count | Explicit count not stated: allowed |
| size | "3/4 Size"; default full | A different size | Allowed |
| wattage | "15-Watts", "50 Watts" | A different stated wattage | Allowed |
| configuration | Speaker layout (`2x12`) and head/combo/cabinet/stack | A different layout, or an extra form (head product vs head + cab page) | Layout of a stack product not shown: REVIEW |
| bundle | "bundle/pack/kit/+" | A bundle offer for a single product | n/a |
| generation | II, Mk.II/MKII, V2, Gen 2 (normalised to numbers) | A different generation, or one shown for a product without one | REVIEW |
| edition | "10th Edition", "Reissue", "Limited Edition" | An edition the Stompbox product lacks | REVIEW |

Colour words that belong to the identity ("White Light", "Gold Foil") and
"Black Friday" / "Cyber Monday" are never read as finishes. Speaker-layout
detection is limited to 1/2/4/8 × 8/10/12/15. This change came from the live
run, where a Gear4music product code "7X8" in a URL was first misread as a
cabinet.

## Condition

| Condition | Markers (titles, URL, availability text) | Comparable to new |
|---|---|---|
| `USED` | used, secondhand, pre-owned, preloved | No: `SIMILAR_MATCH` at best |
| `REFURBISHED` | refurbished, B-stock, ex-demo, ex-display, open box, nearly new, graded, blemished | No |
| `NEW` | brand new, new in box, "condition: new" | Yes |
| `UNKNOWN` | none (most new retail listings) | Yes, treated as a standard new listing (stated in the note) |

The catalogue's own `condition` column is unverified ("Unknown"/blank).
Stompbox products are treated as new retail items for this comparison.

## Price validation (`price.validate_price`)

A non-`EXTRACTED` Phase 5 price status maps directly to a Phase 6 status:

| Phase 5 field status | Price status | Evidence status |
|---|---|---|
| no extraction (no LLM, page not retrieved, LLM failed) | `NOT_EXTRACTED` | `UNVERIFIED` |
| `NOT_FOUND` | `MISSING` (never zero) | `NOT_FOUND` |
| `UNSUPPORTED` | `UNSUPPORTED` | `UNVERIFIED` |
| `EXCLUDED_SALE_PRICE` | `EXCLUDED_SALE_PRICE` | `UNVERIFIED` |
| `AMBIGUOUS` | `AMBIGUOUS` | `UNVERIFIED` |
| `CONFLICT` | `CONFLICT` | `CONFLICT` |
| `INVALID` | `INVALID` | `UNVERIFIED` |

An `EXTRACTED` price is re-verified from scratch. Phase 6 never trusts the
Phase 5 status alone. The checks are:
- the price text parses to exactly the stored amount and currency;
- the quote contains the amount;
- the amount is not a was/RRP/original/saving figure (`EXCLUDED_SALE_PRICE`);
- it is not a from/per-month/deposit amount (`AMBIGUOUS`);
- it is positive and on a single-product page.

Any inconsistency gives `INVALID` (evidence `CONFLICT`). A consistent price is:
- `VALID` when in GBP, or `WRONG_CURRENCY` otherwise;
- evidence `VERIFIED`, or `STALE` when retrieved more than
  `MATCHING_PRICE_MAX_AGE_HOURS` (24) before evaluation.

The retrieval time is not the retailer's update time, and the provider cache
source is noted. Sale wording on the page is only flagged (`sale_indicated`).
The current displayed price is used, and no discount is computed.

## Direct UK price comparison

`comparison.eligible` is true only when **all** of these hold:
- `EXACT_MATCH`;
- price `VALID` (GBP) with `VERIFIED` evidence;
- the competitor is a price benchmark (`price_benchmark`; never marketplaces,
  directories, unknown types or Gibson);
- the condition is comparable to new;
- the Stompbox GBP price is present.

Then `difference_gbp` = competitor − Stompbox. No percentages, savings or
historical figures are computed. Otherwise `reasons` lists every failed condition.

## Interfaces

- **API:** `POST /api/matching/evaluate` with a Phase 5 `IntelligenceRunResult` as the body.
  - Returns a `MatchingRunResult`: per product, counts by status, the number of comparable prices, and candidates (EXACT first).
  - 404 for unknown product IDs and 422 for an invalid body.
  - Stateless and not stored.
- **CLI:** `python -m app.services.matching.cli data/intelligence_runs/<run>.json`
  writes to `data/matching_runs/` (git-ignored). Run files from before the
  Phase 5 status rename (`SKIPPED_LLM_DISABLED`) are rejected with a clear
  message.
  - Like the API, it never trusts the file for who a competitor is: every
    candidate's competitor (benchmark flag, approval, type, UK gate decision) is
    rebuilt from the catalogue and the review database (`REVIEW_DB_PATH` or
    `--review-db`), opened read-only (`app/services/matching/trusted.py`).
  - If that database is missing, unreadable, at an unsupported version or holds
    invalid records, matching still runs but fails closed: nothing passes the UK
    gate, so no price is compared, and a warning says why. `--out-dir` changes
    the output directory.
- **Combined run:** running Phase 5 and Phase 6 in one step is Phase 7
  (orchestration).

## Tested results (12 Nov 2026, no LLM)

On the live Phase 5 run (SB-001 Boss RC-5 and SB-060 Boss FZ-5 on 5 domains),
saved as `tests/fixtures/phase5_no_llm_run.json` with snippets removed:

- **SB-001:** 3 `EXACT_MATCH` (MEDIUM) at Andertons, BopDJ and Promenade; 2
  `SIMILAR_MATCH` (Gear4music "Secondhand", condition USED); 5 `NO_MATCH`
  (guide page, Gibson forum threads, BopDJ and Promenade category pages).
- **SB-060:** 3 `EXACT_MATCH`; 1 `SIMILAR_MATCH` (secondhand); 6 `NO_MATCH`.
- Every price is `NOT_EXTRACTED`, so **no candidate is comparable** in no-LLM
  mode. This is by design: titles and snippets are never price evidence.

## Limitations

- **No real LLM 2 output yet:** price validation and `HIGH` confidence have
  only been exercised with synthetic LLM 2 output. Real extraction accuracy
  is unmeasured.
- **Rule-based variant detection:**
  - colours outside the vocabulary (e.g. "Bourbon") cannot conflict, only
    stay unconfirmed;
  - wording such as "Used by pros" could be misread as a condition.
- **No-LLM page classification relies on URL patterns:** a category page
  with a product-like URL that also names the model could reach
  `EXACT_MATCH` (MEDIUM). None did in the live run.
- **Brands have no alias table:** e.g. "EQD" for EarthQuaker Devices. Such
  listings fall to `REVIEW_REQUIRED` (brand not found).
- **Required editions are strict:** products whose catalogue name carries an
  edition ("10th Edition") need it stated for `EXACT_MATCH`.
- **SB-028's catalogue price may be a sale price:** the catalogue stores the
  Stompbox snapshot £579, while the Stompbox page showed "was £599". This
  needs a business decision before SB-028 is used as a comparison baseline.
