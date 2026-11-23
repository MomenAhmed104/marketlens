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
| `EQUIVALENT_VARIANT` | Otherwise the same product; the only difference is the cosmetic finish or colour (decision U-1). Its price is an alternative-product price: never a benchmark, no difference |
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
   - Any critical variant **conflict** other than finish: `SIMILAR_MATCH`.
     Handedness, size, configuration, strings, wattage, bundle, generation and
     edition never qualify as equivalent (U-1).
   - Condition used or refurbished: `SIMILAR_MATCH`.
   - Otherwise `REVIEW_REQUIRED` if any of the following holds, else
     `EXACT_MATCH`, or `EQUIVALENT_VARIANT` when the finish is the only
     conflict (a finish-only conflict with an open review reason is
     `REVIEW_REQUIRED`, with "equivalent variant not established"):
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

### LLM 2 evidence after the decision (`matcher.reconcile`, decisions P-4, U-4)

Python decides first. LLM 2's verified evidence is then applied, and it can only
add uncertainty:

- A **verified** LLM 2 issue (`AMBIGUOUS_VARIANT` or `CONFLICTING_INFORMATION`)
  on an identity field (model, finish, handedness, specifications...) turns
  `EXACT_MATCH` or `EQUIVALENT_VARIANT` into `REVIEW_REQUIRED`.
- A **verified** Task A proposal (status `COMPLETED`) is compared with Python's
  status through the P-3 mapping (`SIMILAR_ALTERNATIVE` = `SIMILAR_MATCH`,
  `NOT_A_MATCH` = `NO_MATCH`). When Python says `EXACT_MATCH` or
  `EQUIVALENT_VARIANT` and the proposal is more cautious, or names a verified
  non-cosmetic differing attribute, the result is `REVIEW_REQUIRED`, with the
  proposal's reason and quote in `review_reasons`.
- Nothing else changes a status. LLM 2 never upgrades, downgrades or replaces a
  classification: `REVIEW_REQUIRED` is the only consequence. Python being stricter
  (for example Python `EQUIVALENT_VARIANT`, proposal `EXACT_MATCH`) is recorded as
  `PYTHON_STRICTER` and kept.
- `UNSUPPORTED` (no verified quote), `FAILED` and skipped proposals, and
  unverified issues, are ignored. `proposal_agreement` records `AGREES`,
  `PYTHON_STRICTER`, `LLM_STRICTER` or `NOT_AVAILABLE`.

### Match confidence

- `HIGH`: `EXACT_MATCH` or `EQUIVALENT_VARIANT` with the model confirmed by an LLM 2 field plus title or URL.
- `MEDIUM`: the same from page title and URL only (no-LLM mode).
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
| pickups | Layout codes HSS, SSS, HH, HSH, SSH in the name, model or specifications, in any letter case ("hss", "Hss"), as whole tokens only, normalised to upper case | A different layout in the title, URL, extracted fields or a verified pickups spec | Stated layout not shown: REVIEW (unless the exact model contains it). Catalogue silent but the competitor **title or URL** names a layout: REVIEW. A layout only in specs, catalogue silent: not compared |
| electronics | "active"/"passive" next to pickups/electronics/EQ/preamp/bass, or bare in specifications | The other mode in the title or a verified electronics spec | Stated mode not shown: REVIEW. Catalogue silent but the title names one: REVIEW |
| electro_acoustic | Subcategory or name "Electro-acoustic" (electro); an "Acoustic guitar" subcategory without electro wording (no electronics) | Electro product: the competitor states "no pickup", "non-electro", "acoustic only" or an electronics spec of "None". Acoustic product: the competitor lists an electro-acoustic version | Electro product, competitor silent: REVIEW, unless an exact code-like model implies it (`BAA2CE`) |

The electronics checks (audit fix, 19 Nov 2026) read only verified Task B
specifications categorised as `electronics` or `pickup_configuration`
(`app/services/specs.py`, Stage 6.1; `EvidenceSource.EXTRACTED_SPEC`):

- Pickup layout comes from `pickup_configuration` specs only.
- Active/passive comes from `electronics` and `pickup_configuration` specs.
- Electro-acoustic confirmation comes from `electronics` specs only.

Controls, power-supply and output-power specs never take part. Older records
without a stored category get it derived the same way. These specs never count
as model evidence. No difference is inferred from silence: a plain "Acoustic
Guitar" title is not evidence that the guitar has no electronics.

**Finish aliases (Stage 6.1).** `attributes.FINISH_ALIASES` is a controlled
list of verified alternative spellings: currently `"iced tea burst": ("ice
tea", "iced tea")`: "ice tea" seen on Gear4music in the Stage 6 run, "iced tea"
seen on Wildwire in Stage 7 (user-approved 20 November 2026). `finish_in` matches the
catalogue finish or an alias as a whole phrase, ignoring case, spacing and
punctuation. There is no fuzzy matching:

- "Tea Burst", "Spice Tea", "Iced" and "Ice" are not Iced Tea Burst.
- "Iced Tea" together with another colour ("Iced Tea / Ebony", "Iced Tea
  Sunburst") is mixed evidence (`UNVERIFIED`, review), never a match.
- An alias only confirms the finish attribute; a stricter LLM 2 proposal still
  gives `REVIEW_REQUIRED` (U-4).
- Black against White stays a conflict.
- Cherry Sunburst against Vintage Sunburst stays unconfirmed (review), never a
  match.

Add aliases only with evidence.

**Stage 6.1.1 hardening:**

- **Mixed finishes.** If the text names the catalogue finish (or an alias) and
  also another finish ("Ice Tea / Ebony"), the finish check is `UNVERIFIED`
  ("Mixed finish evidence"), and in option-price matching the option is
  `UNKNOWN`. A mixed label therefore can't authorise a variant price. Part
  colours ("Gold Hardware", "Cream Binding") and sheen words ("Satin", "Gloss")
  are ignored.
- **Specification categories** are always re-derived from name and value.
  Stored or client-submitted categories are never trusted; a disagreement is
  listed in `spec_category_mismatches` and as an uncertainty. Pickup selector,
  switch, toggle, blend and knob specs are controls.
- **Electro-acoustic confirmation.** An electronics spec confirms an
  electro-acoustic only when it names a device, type or maker
  (`specs.is_meaningful_electronics`: preamp, piezo, pickup, active/passive,
  EQ, Fishman, LR Baggs ...). "Electronics: Volume, Tone" stays unknown.
- **Delivery prices.** `validate_price` rejects a delivery-charge quote
  (`INVALID`) and an amount listed as a delivery charge (`AMBIGUOUS`),
  re-derived from the raw ignored-price texts. This also applies to older run
  files and to runs submitted to `POST /api/matching/evaluate`.

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


## Alternative-product prices (decisions D-28, D-32)

`EQUIVALENT_VARIANT` and `SIMILAR_MATCH` candidates get `alternative_price`:

- Shown only with the same price validation as a direct price (`VALID` and
  `VERIFIED`).
- No benchmark flag is needed, but the competitor must be a search-approved
  target (configured, reviewer-approved or auto-approved in this run).
- Used and refurbished items are shown with an explicit `condition_label`.
- No difference is calculated, `benchmark_eligible` is always false, and they are
  never counted in `comparable_prices` (counted in `alternative_prices`).

`price_kind` is `DIRECT` (eligible exact match), `ALTERNATIVE` (shown alternative
price) or `NONE`.

## Task C view (`matching/view.py`)

`CandidateMatch.intelligence` (`ProductIntelligenceView`) answers the four
questions per candidate from validated data only: seller (domain, type, approval,
benchmark flag), match (final status, proposal, agreement, reasons), price (kind,
validation, quote, source, retrieval time; a difference for `DIRECT` only) and
availability (status, verified text, reason). `uncertainties` lists every
unsupported, ambiguous or conflicting field, verified LLM 2 issue, unknown
availability, review reason and proposal disagreement. Missing values stay
missing.

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
    candidate's competitor (benchmark flag, approval, type) is
    rebuilt from the catalogue and the review database (`REVIEW_DB_PATH` or
    `--review-db`), opened read-only (`app/services/matching/trusted.py`).
  - If that database is missing, unreadable, at an unsupported version or holds
    invalid records, matching still runs but fails closed: no candidate (configured
    competitors included) is a price benchmark, so no price is compared, and a
    warning says why. `--out-dir` changes
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
  Stompbox snapshot £579, while the Stompbox page showed "was £599". The price
  baseline still awaits confirmation; the catalogue price is used unchanged.
- **SB-028 is the approved Figured model (20 November 2026):** see "Approved
  identity decisions" below.

## Approved identity decisions (Stage 6.3, 20 November 2026)

`attributes.APPROVED_DESIGNATIONS` lists catalogue products whose model
designation is part of the product identity but is not written in the catalogue
record. Each entry is a user decision; the catalogue workbook is unchanged. The
profile carries it as `ProductProfile.designation`, and `matcher.check_designation`
adds a critical `designation` variant check (in `CONFIRM_REQUIRED`, never
cosmetic):

| Evidence (titles, URL, extracted model/SKU only) | Result | Effect |
|---|---|---|
| The designation as a whole word ("Figured"), or a listed SKU of the designated model | `MATCH` | can be `EXACT_MATCH` if every other check passes |
| A listed SKU of the model without the designation | `CONFLICT` | `SIMILAR_MATCH` (a model difference, never `EQUIVALENT_VARIANT`) |
| Both of the above | `UNVERIFIED` (contradictory) | `REVIEW_REQUIRED` |
| Neither | `UNVERIFIED` | `REVIEW_REQUIRED`: a page that does not name the designation is never accepted as the designated model |

Specifications never count ("Highly Figured Maple Veneer" in a spec is not
identity evidence: flame maple tops are often described as figured). SKUs are
compared as whole tokens with case and `-`/`_`/`.` ignored; no fuzzy matching and
no generic model aliases. Products without an entry get no `designation` check.
Handedness, finish, edition, condition, LLM 2 reconciliation (U-4) and every
other rule are unchanged. Task A (LLM 2) receives the designation as
`model_designation` in the catalogue product, so future proposals compare against
the approved identity; proposals stored in earlier run files were made without it.

Only SB-028 is listed: the **Epiphone Les Paul Standard '60s Figured
Left-Handed**, Iced Tea Burst (user decision, 20 November 2026). Figured SKUs:
EIGLP6ITNHRL (Andertons), EIGLP6ITNH1L (Gear4music); plain-top SKU: EILS6ITNH1
(Gear4music secondhand). All three come from the Stage 6 and 6.2 run records.
The temporary Stage 6.3.2 hold (`IDENTITY_DECISION_PENDING`) was removed.

Recomputed offline from the saved run files with the current code:

| Run | Page | Designation | Status |
|---|---|---|---|
| Stage 6 | Andertons Custom 1960 Les Paul Standard Reissue LH | `UNVERIFIED` | `SIMILAR_MATCH` (reissue edition) |
| Stage 6 | Gear4music LP Standard 60s Ice Tea, secondhand (EILS6ITNH1) | `CONFLICT` | `SIMILAR_MATCH` |
| Stage 6.2 | Andertons LP Standard '60s Figured LH Iced Tea | `MATCH` | `REVIEW_REQUIRED` (stored LLM 2 proposal, made without the designation, is stricter) |
| Stage 6.2 | Gear4music LP Standard 60s Figured LH Iced Tea | `MATCH` | `REVIEW_REQUIRED` (same) |

**SB-028 price baseline: still unconfirmed.** The catalogue stores £579, which the
Stompbox page recorded on 10 November 2026 showed as the current price with
"was £599". The catalogue price is unchanged, £599 is not used, and no regular
price is assumed. A direct SB-028 comparison (only on an `EXACT_MATCH`) uses the
catalogue £579 exactly as for every other product; that baseline is not
independently verified and awaits the user's confirmation.

## Bundles with included extras (Stage 9.1 fix, 23 Nov 2026)

Found in the Stage 9.1 smoke run: Gear4music "Boss RC-5 ... with Power Supply" was EXACT_MATCH and
price-compared, because bundle detection only knew the words bundle / pack / kit / package /
starter set / " + ". `attributes.bundle_evidence` now classifies a listing's title / URL /
extracted model text:

| Kind | Example | Bundle check | Result for a standalone Stompbox product |
|---|---|---|---|
| BUNDLE | "with power supply", "includes USB cable", "& PSA adaptor", "hard case included", "bundle", "2 pack" | CONFLICT | SIMILAR_MATCH, never price-compared |
| OPTIONAL | "power supply not included", "sold separately", "requires 9V power supply", "without ..." | not applicable | unchanged (can be EXACT) |
| AMBIGUOUS | "Boss RC-5 Loop Station Power Supply" (an extra named, inclusion unclear) | UNVERIFIED | REVIEW_REQUIRED (`bundle` is in CONFIRM_REQUIRED) |
| STANDALONE | no extra named | not applicable | unchanged |

- Extras recognised: power supply / PSU / adaptor, cable, case, bag / gig bag, strap, stand,
  footswitch, picks, headphones, batteries. Product features (Bluetooth, vibrola), tuners and
  strings are not extras.
- A guitar listed only "with case / gig bag" goes to review (UNVERIFIED), not CONFLICT: guitars
  are often supplied with one as standard.
- A Stompbox bundle ("... with Deluxe Gig Bag", SB-033) matches only the same extras; different
  extras are a CONFLICT and an unspecified "bundle" is UNVERIFIED. A standalone listing against a
  Stompbox bundle keeps its previous result (not applicable).
- A catalogue product that is itself an accessory is never read as a bundle of itself.
- Tests: `tests/test_bundle_matching.py` (including the smoke-run regression fixture
  `tests/fixtures/stage91_smoke_sb001.json`, snippets removed).

## Similar matches need a compatible product type; finish from specifications (Stage 9.2, 23 Nov 2026)

- Same brand + different model is SIMILAR_MATCH only when the listing's product type equals the
  Stompbox product's (`attributes.product_type`: cabinet, combo amp, amp head, tuner, looper,
  tremolo, drive, delay, reverb, modulation, pedalboard, bass guitar, guitar; first match wins).
  Otherwise NO_MATCH. Generic words (watt, pedal, amplifier) are never type evidence. The listing
  type is read from the extracted title, then search title, page title, URL. Catalogue products
  with no type in the vocabulary (8: multi-effects, pitch shifter, noise gates...) keep the
  previous rule. Effect on history: 5 of 74 recomputed candidates (all Run A) went SIMILAR ->
  NO_MATCH (Boss TR-2 for RC-5; Ashdown cabinet, combos and "Studio 15" for SX-50H); RC-500 stays
  SIMILAR; no exact match or price comparison changed.
- "ITB" is an Iced Tea Burst alias. When title / URL are silent on the finish, an explicit,
  verified "Colour" / "Finish" / "Body colour" specification can confirm it (textures such as
  gloss / satin are ignored); it never creates a conflict, mixed values stay UNVERIFIED, and a
  title conflict is never overridden. Thomann "60s F ITB LH" (SB-028): finish now MATCH, still
  REVIEW_REQUIRED because "F" is not accepted as Figured (pending a decision).
- Tests: `tests/test_similar_and_finish_rules.py` (Run A fixture `tests/fixtures/stage92_runA.json`).
- Retailer names never establish a product type (23 Nov 2026): title segments that are the
  retailer's own name ("– Langley Guitar Centre", "| guitarguitar", "at Gear4music", "– Thomann UK")
  are removed before the type is read, and URLs contribute only their final product slug (never
  category navigation such as `/collections/electric-guitars/`). "Ashdown Studio 15" is now
  "type not stated" (NO_MATCH) instead of "guitar".
