# Stage 9: end-to-end evaluation

Prepared offline on 23 Nov 2026 (Stage 9.0), then run live under budget control: the smoke test
(Stage 9.1) and Runs A and B (Stage 9.2), all reconciled cleanly in the Stage 9 ledger
($1.023 of $3.00 committed). A2 / B2 have not been run. Spending control:
[../stage9-budget.md](../stage9-budget.md). Live state (ledger, isolated evaluation database,
run artifacts, review files) is in the git-ignored `data/stage9/`.

| Piece | Where |
|---|---|
| Scenario and answer key (approved 23 Nov 2026 as a hypothesis-based specification) | [answer-key.json](answer-key.json), schema `backend/app/stage9/evaluation.py` |
| Isolated review databases | `backend/app/stage9/isolated_db.py` |
| Evaluation harness | `backend/app/stage9/harness.py` |
| Scoring | `backend/app/stage9/scoring.py` |
| Tests | `backend/tests/test_stage9_evaluation.py`, `backend/tests/test_stage9_budget.py` |

## Scenario (six products, checked against the workbook)

| ID | Workbook name | Brand / model | Category | Price (9 Nov snapshot) | Why |
|---|---|---|---|---|---|
| SB-001 | Boss RC-5 Loop Station Guitar Effect Pedal | Boss / RC-5 | Effects | £189.99 | Widely stocked; historical exact matches; nearly-new and RC-500 look-alikes |
| SB-028 | Epiphone Les Paul Standard '60s Left-Handed Electric Guitar, Iced Tea Burst | Epiphone / Les Paul Standard '60s LH | Guitars | £579.00 (unconfirmed, sale) | Hardest variant case; **variant matching only, no price scoring** |
| SB-017 | Ashdown SX-50H All Valve Electric Guitar Amplifier Head, 50-Watts | Ashdown / SX-50H | Amplification | £349.00 | Head vs combo/cabinet; only product with workbook specifications |
| SB-030 | Ashdown Saint Artisan Flame Bass Guitar, Amethyst Purple | Ashdown / Saint Artisan Flame | Guitars | £369.00 | Finish-specific bass; Stompbox listing is pre-order |
| SB-090 | Cherub WST-905Li Flow Tune Rechargeable Clip-on Headstock Guitar Tuner | Cherub / WST-905Li | Accessories | £24.99 (live page checked 9 Nov) | Low price; model suffix 'Li' is the identity |
| SB-055 | Emma Electronic OMP-1 OnoMATOpoeia Booster Overdrive Pedal | Emma Electronics / OMP-1 | Effects | £109.99 | **Added**: less widely stocked; on 10 Nov no exact page at any of 12 configured retailers (Exa and Tavily) |

Missing or ambiguous (all recorded in the key): no workbook specifications except SB-017;
SB-001's catalogue URL returned 404 on 22 Nov (image only; not a reason to exclude it); the
workbook URLs use `/product/...` while the live Stompbox site serves `/products/...`; SB-028's
name omits "Figured"; SB-030 string count and handedness unknown; SB-055 brand spelt "Emma
Electronics" in the brand column but "Emma Electronic" in the name.

Planned runs (at most 5 products per run): **A** SB-001, SB-028, SB-017 and **B** SB-030,
SB-090, SB-055, both with discovery; optional re-runs **A2** / **B2** without discovery after
reviewer decisions in the evaluation database.

## Answer key (approved 23 Nov 2026)

Every claim is labelled **VERIFIED** (workbook or a dated project record, with its source),
**HYPOTHESIS** or **UNKNOWN**. Retailer expectations are never VERIFIED: a historical listing is
evidence for its date only (a test enforces this).

- **Identity rules** per product: `exact_requires` (every group must appear in the page title,
  URL or extracted model/SKU) and `not_exact_if` (any hit means not the exact product: used /
  secondhand / nearly-new / B-stock / refurbished, bundle / pack / kit, and product-specific
  terms such as right-handed, other finishes, 1960 reissue, Gibson, combo / cabinet, multi-packs).
  Matching is on normalised whole words, so "RC-5" never matches "RC-500".
- **SB-001:** Andertons (£219.00) and Gear4music (£192.00) were EXACT_MATCH on 20 Nov 2026
  (Stage 7 run `...28317c80`), Wildwire (£189.00) in `...3f13cfde`; all HYPOTHESIS for a live run.
  Five configured retailers had an exact page on 10 Nov, but the record does not name them, so
  Guitarguitar, Thomann and Bax are UNKNOWN.
- **SB-028:** exact = Figured + left-handed + Iced Tea Burst. Exact pages at Andertons,
  Guitarguitar, Gear4music and Thomann on 10 Nov (benchmark); Bax and Wildwire pages without a
  clear Figured / bundle status are UNKNOWN. Price scoring **excluded**.
- **SB-017, SB-030, SB-090:** no product-level history: retailers are UNKNOWN (configured
  competitors that sell the category).
- **SB-055:** HYPOTHESIS that configured competitors do not list it (true on 10 Nov); the smaller
  shops named in the benchmark are UNKNOWN for this product. NO_MATCH at configured competitors is
  the expected, correct outcome.
- **Evidence required:** identity from the retrieved product page (not a snippet), every required
  variant on the page, a single-product page for a new item; price as a current GBP amount quoted
  verbatim, not was/RRP/finance/delivery, retrieved within 24 h, from a benchmark-eligible seller.

The key is regenerated only by hand-review; `check_against_catalogue` verifies it against the
workbook (a test runs it).

## Harness (same path as the dashboard)

`run_scenario(ScenarioRun(...))` builds the real app (`create_app`) with evaluation settings and
drives it over HTTP in-process: `POST /api/runs`, poll `GET /api/runs/{id}`, then `/result` and
`/events`. The orchestrator, agents, matching and the budgeted run manager are the production
code; no running server is touched.

- Inputs: product IDs, discovery on/off, an optional domain subset (`RunRequest.domains`),
  reviewer `approvals` applied to the isolated database before the run, images (default off).
- Refuses to start unless budget mode is on with an existing ledger and the review database is an
  existing file outside `data/review/`. With prices unset the run itself is refused (503) and the
  refusal is recorded as an artifact.
- Artifacts (new folder per run, never overwritten): `manifest.json` (request, approvals, git HEAD,
  answer-key SHA-256, database and ledger paths, settings summary without secrets, timings),
  `run.json`, `result.json`, `events.json`, `ledger.json` (reservation, charges, every metered
  attempt). The harness waits for the ledger to be reconciled before shutting the app down.

## Scoring

`score_run(key, run, result, ledger=..., duration_s=..., verifications=..., review_decisions=...,
catalogue_prices=...)` returns raw counts, a percentage and what was left out (and why) for:
discovery relevance, domain eligibility, retrieval against the key, page retrieval, extraction
accuracy, exact-match precision, similar-match handling, price-comparison correctness and run
completion, plus cost and latency. `render_markdown` prints a table.

- A product never searched is `NOT_SEARCHED` and left out of every match metric (never NO_MATCH).
- NO_MATCH is never a failure. Pages not retrieved, or where extraction did not run, are left out
  of extraction accuracy.
- UNKNOWN key values and unverified candidates are left out. An EXACT_MATCH that the rules or a
  human find to be a variant / condition / bundle mismatch is a failure.
- Each eligible price comparison is re-checked independently: benchmark seller, EXACT_MATCH,
  identity, VALID, GBP, VERIFIED (not STALE) evidence, condition comparable to new, difference =
  competitor − Stompbox, Stompbox price = workbook, and the live price if a human verified it.
  SB-028 comparisons are left out.
- Discovery relevance and extraction accuracy need a human `Verifications` file (domain relevance
  labels; what each source page really shows). Without it they report "not verified".

## Isolated databases

```
create_copy(source, dest)     # SQLite backup from a read-only connection: schema, version, rows
create_empty(dest)            # fresh current schema
apply_decisions(dest, [...])  # idempotent; a conflicting decision is refused
reset(dest, source=None)      # delete and recreate between scenarios
```

The destination may not be the source or anything inside `data/review/`. The harness sets
`REVIEW_DB_PATH` to it, so discovery, the review queue and target selection all use it (a test
shows an approval applied to the copy becomes a search target). Tested on temporary databases
only; the real and demo databases were not copied or opened.

**For Stage 9.1 (not done):** choose the starting state: an empty database (only the 13
configured competitors are searched until you approve discoveries) or a copy of the real database
(its 7 approved and 2 rejected decisions apply). Suggested location: `data/stage9/` (needs a
`.gitignore` entry; not added).

## Unresolved before any paid run

1. **Exa** maximum price for one `/search` with up to 10 results and highlights, and for one page
   in `/contents` (text up to 30,000 characters, live crawl allowed).
2. **OpenRouter** maximum input and output token prices for `openai/gpt-6-luna` across every
   upstream provider it may be routed to.
3. Whether the model's tokenizer can produce more tokens than UTF-8 bytes (the input bound relies
   on it not doing so).
4. Whether `max_completion_tokens` includes reasoning tokens on that route (the output bound
   relies on it).
5. The reservation. Worst-case call counts per planned run, independent of prices (current
   settings: retries 2, LLM 2 max 8,000 output tokens, max request 160,000 bytes):

   | Run | Exa searches | Pages | LLM 1 calls | LLM 2 calls | Attempts per call |
   |---|---|---|---|---|---|
   | A or B (3 products, discovery, automatic search off) | 69 | 60 | 3 | 120 | Exa ×3, LLM ×6 |
   | Same with automatic search on | 84 | 60 | 3 | 120 | Exa ×3, LLM ×6 |
   | A2 / B2 (no discovery, 13 configured + 3 approved) | 48 | 60 | 0 | 120 | Exa ×3, LLM ×6 |

   The LLM 2 term (120 calls × 6 attempts × the maximum request size) will dominate, so the full
   worst case is likely far above realistic use; a per-run cap (`STAGE9_MAX_RESERVATION_PER_RUN_USD`)
   is expected. Historical Stage 7 runs cost $0.11–0.12 each, but they searched only 1–2 domains.

These must be checked against the providers' own pricing and API documentation before any
`STAGE9_*` value is configured.

## Stage 9.1 smoke test (prepared, not run)

`backend/app/stage9/smoke.py` fixes the scope in code: SB-001, discovery off, `andertons.co.uk`
and `gear4music.com` only, $0.25 per-run reservation cap, $3.00 ledger ceiling.

```
cd backend
python -m app.stage9.smoke setup --dir ../data/stage9      # ledger ($3.00) + empty evaluation DB
python -m app.stage9.smoke check --dir ../data/stage9 --exa-search-usd 0.007 --exa-page-usd 0.001 \
    --llm-input-usd-per-token 0.00000025 --llm-output-usd-per-token 0.000001   # preflight, no network
python -m app.stage9.smoke run   --dir ../data/stage9 <same prices>            # paid: only when authorised
```

- `run` refuses unless: prices are not below the verified maxima; the ledger exists with exactly
  $3.00, has no RESERVED or NEEDS_REVIEW run and at least $0.25 remaining; the evaluation database
  exists outside `data/review/`; Exa and LLM 2 are configured; images are off.
- Worst case for this scope at those prices: 2 searches, 4 pages, 8 LLM 2 calls (×3 / ×6 attempts)
  = $2.37, so the reservation is the $0.25 cap and the meter enforces it per HTTP attempt.
- A ledger write failure stops all further paid calls in the run (fail closed); any unresolved
  run blocks every new run until reconciled or resolved (`cli resolve`); `run` exits non-zero
  unless the ledger ends RECONCILED.
- `data/stage9/` is not git-ignored yet; run artifacts contain third-party page data, so add an
  ignore entry before the first run (decision pending).

## Stage 9.2 human review (prepared 23 Nov 2026)

- `python -m app.stage9.review_pack --dir ../data/stage9` writes, under the git-ignored
  `data/stage9/review/`: `domain-review-pack.json|md` (all 28 discovered candidates, flags for the
  reviewer, no decisions) and `extraction-verification.json|md` (36 retrieved listings to verify,
  8 blocked or failed pages listed separately). The evaluation database is opened read-only.
- Domain fix: generic second-level labels under a two-letter country code (`com.ua`, `org.uk`,
  `co.jp`...) are part of the public suffix. Run B's `com.ua` candidate merged four Ukrainian
  shops; that stored record is left as it is (flagged in the pack). A maintained Public Suffix
  List library (e.g. `publicsuffixlist`) would replace this interim rule; it needs an approved
  dependency install.
- Workflow: `review_pack build` also writes `domain-decisions.template.json` (28 UNDECIDED rows,
  never applied) and `verification-worksheet.json` (36 retrieved listings, every field UNVERIFIED;
  never overwritten once it exists). `review_pack check-worksheet` validates the filled worksheet
  (reviewer and check time required; allowed values only) and writes `verifications.run<X>.json`
  for `scoring.score_run`.
- Domains: `publicsuffixlist==1.1.2.20261122` (MPL-2.0, no dependencies) is pinned in
  `backend/requirements.txt`; its bundled list is read offline (private suffixes such as
  `github.io` count as suffixes). The previous rule is the fallback. On all 146 stored hosts it
  agrees with the interim rule; only the four `.com.ua` hosts differ from the original rule.
- Proposed decisions: `review_pack build` writes `domain-decisions.proposed.json|md` (13 APPROVE
  for search only, 1 REJECT for the merged `com.ua`, 14 DEFER; benchmark eligibility false for
  all; never applied). `review_pack decisions-check` validates it and prints what would be
  written without writing. The reviewer's spreadsheet is `verification-worksheet.csv` (evidence
  quote or reference required for any verified field).
