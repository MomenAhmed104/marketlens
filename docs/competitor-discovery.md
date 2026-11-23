# Competitor discovery (LLM 1)

Implemented in Phase 3, in `backend/app/agents/discovery/`. LLM 1 proposes;
deterministic Python validates and decides. Simplified in **Stage 8.1 (22 Nov 2026,
user-approved)**: LLM 1 searches specifically for retailers serving UK customers, and there
is no UK verification, UK evidence scoring or UK approval gate. See "Stage 8.1" below.

## Execution modes

The mode is chosen at run time and recorded on every result (`mode`, plus
`llm_disabled_reason` when disabled). `GET /api/discovery/config` reports the
mode a run would use, whether automatic search is on and the per-run cap.

| | `LLM_ENABLED` | `LLM_DISABLED` |
|---|---|---|
| When | `OPENROUTER_API_KEY` and `LLM1_MODEL` set, and `DISCOVERY_USE_LLM=true` (default) | Either is missing, or `DISCOVERY_USE_LLM=false` |
| Global queries | Baseline + validated UK-focused LLM 1 queries | Baseline `"<brand> <model> buy UK"` only |
| Exa searches, grouping, dedup, exclusions, profile evidence | Yes | Yes (identical) |
| Company name, type, categories, relevance, confidence, `serves_uk_customers` | Proposed by LLM 1, then validated | **Left null/unknown**; never filled in |
| New-company decision | `AUTO_APPROVED` when `is_search_eligible`, otherwise `APPROVAL_REQUIRED` | Always `APPROVAL_REQUIRED` ("LLM classification skipped (LLM disabled)") |
| Targeted queries | Known + reviewer-approved + this run's automatically eligible domains | Known + reviewer-approved domains |
| Warnings | Only for actual problems | "LLM query planning skipped (…)" and "LLM classification skipped (…)" |

Missing LLM configuration is never an error: the app starts, known competitors
are available, and discovery runs in `LLM_DISABLED` mode. Only a missing
search provider or key makes discovery unavailable (HTTP 503 / CLI exit 3).

Each candidate also carries `classification_status`: `COMPLETED`,
`SKIPPED_LLM_DISABLED`, `FAILED` (the call failed or its output was unusable)
or `MISSING` (the LLM returned nothing for that domain), and `classification_version`
(`CLASSIFICATION_VERSION = 3` since the used-only refinement; 2 was the first Stage 8.1 prompt, never
used by a real run; null for older ones). Only a
`COMPLETED` classification at the current version can make a domain automatically searchable.

## Flow

1. **Known competitors.** The 13 workbook competitors are loaded as
   `PRECONFIGURED` and never re-decided. Gibson stays a manufacturer reference
   with `price_benchmark=false`. Its targeted queries carry that flag, so later
   phases never mix its prices into the retailer benchmark.
2. **Planning (LLM 1).** For each selected product, LLM 1 proposes up to N global queries
   aimed at retailers selling to UK customers (UK musical instrument retailers, UK guitar
   shops, UK music equipment retailers, shops delivering to the UK, UK stockists of the brand
   or product; a shop abroad that sells to UK customers is valid) and up to M targeted query
   variants. Code then:
   - always adds a baseline global query `"<brand> <model> buy UK"` and a
     baseline targeted query `"<brand> <model>"`;
   - drops queries containing `site:`, queries over 200 characters, duplicates,
     targeted variants that don't contain the exact model, and unknown product
     IDs (each drop is logged as a warning).
3. **Global discovery.** Each query runs on the configured provider (Exa),
   localised to GB. Every result becomes an `EvidenceItem` holding the original
   URL, canonical URL, title, snippet, query, product ID, provider, provider
   request ID and retrieval time, plus deterministic flags for whether the
   model or brand appears in the title or URL. The same page found by several
   queries is kept once per product.
4. **Grouping and deduplication.** Evidence is grouped by registrable domain
   (`blog.andertons.co.uk` becomes `andertons.co.uk`).
   - Own site (`OWN_DOMAINS`): excluded.
   - Removed competitors (`DISCOVERY_EXCLUDED_COMPETITOR_DOMAINS`, PMT): excluded.
   - Known competitors: their hits are kept as *evidence* (which selected
     products they list), never as new candidates.
   - Non-shop platforms (YouTube, Reddit, …): excluded.
   - The rest are ranked by evidence (distinct products matched, model
     mentions, hits). The top `DISCOVERY_MAX_CANDIDATES` are evaluated; the
     others are recorded as "not evaluated".
5. **Profile evidence.** One search restricted to each candidate's domain ("about us /
   contact / delivery / shipping") collects evidence about the business itself, for LLM 1's
   classification only.
6. **Classification (LLM 1).** Evidence is sent as delimited, untrusted JSON (at most 8 items
   per domain, up to 3 of them profile pages in the order found; profile snippets up to 400
   characters, discovery snippets up to 280). The model returns the company name *as written
   in the evidence*, company type, categories, `serves_uk_customers` (yes/no/unknown) with a
   short `serves_uk_customers_note`, whether it sells new instruments, relevance, confidence,
   a rationale, the cited evidence URLs and a `competitor_class` proposal. It is not asked for
   UK evidence quotes.
7. **Validation (deterministic, `rules.apply_classification`).**
   - Cited URLs not present in the evidence are discarded.
   - Categories outside the catalogue are discarded.
   - The company name is *verified* only if it appears in a title or snippet
     from that domain; a lookalike hostname is never enough.
   - The rationale (600) and the UK note (300 characters) are trimmed.
   - `classification_version` is set to the current version.
8. **Decision (deterministic, `rules.decide`).** `REJECTED` (no discovery evidence),
   `AUTO_APPROVED` (automatically search-eligible, below) or `APPROVAL_REQUIRED`
   (everything else; the reasons list every unmet criterion). Never a benchmark.
9. **Reviewer decisions (Phase 4).** Persisted decisions override step 8:
   a rejected domain is never searched, and an approved domain is searched
   with its reviewed benchmark flag. See [competitor-review.md](competitor-review.md).
10. **Targeted queries** are generated only for search targets: preconfigured
    competitors, reviewer-approved domains (even if not rediscovered) and this run's
    automatically search-eligible domains. Each product gets its baseline query plus up to M
    validated LLM variants, restricted to the domain. The pipeline and the intelligence API
    also search stored eligible domains from earlier runs (see "Persistence and cap").

## Stage 8.1: UK-focused discovery without UK verification (22 Nov 2026)

**Why it replaced Option B.** Option B (19 Nov 2026) asked LLM 1 for UK evidence quotes and had
Python verify them, judge their sufficiency and require two independent evidence pages before
auto-approval. Very few domains met it (in Stage 7, Wildwire, Spears, Mann's, Promenade and
Roland Store London all needed review), it was a second approval system to maintain, and the
user decided that UK relevance should come from LLM 1's search strategy instead. Option B's
components were removed; nothing replaces them under another name.

### Automatic search eligibility (`rules.is_search_eligible`)

A discovered domain may be searched for products without a person approving it when **all**
of these hold:

1. LLM 1 completed its classification, at the current `CLASSIFICATION_VERSION`;
2. the company type is `retailer` or `specialist_retailer`;
3. the company name is written in the domain's discovery evidence (`name_verified`);
4. at least one URL LLM 1 cited is in the domain's discovery evidence;
5. the domain is a valid, normalised registrable domain (`domains.canonical_domain`);
6. it is not excluded (own site, non-shop platforms, removed competitors such as PMT);
7. it is not a known marketplace (`MARKETPLACE_DOMAIN_LABELS`) or a directory;
8. no person has rejected it;
9. it is not confirmed to sell only used instruments (`sells_new_musical_instruments` is `false`).

`sells_new_musical_instruments` is LLM 1's tri-state answer since version 3: `true` sells new
instruments (with or without used ones), `false` the evidence shows it sells only used, second-hand
or vintage instruments, `null` unclear. Only `false` blocks automatic search; unknown never does. A
used-only retailer stays pending and can be approved by a reviewer, which never grants benchmark
permission on its own (only the explicit `benchmark_eligible` flag does).

Never used: `serves_uk_customers` (even "no"), relevance, confidence,
a `.uk` domain, GBP prices or a UK address. A business abroad
that sells to UK customers qualifies on the same terms. Manufacturers, marketplaces,
directories, `other` and unknown types, and every no-LLM candidate, stay pending for a reviewer.
`decide` rejects only a candidate with no discovery evidence; low relevance never rejects.

### Persistence and cap

- Eligibility is **recomputed** from the stored candidate record and the current human decision
  every time it is needed (pipeline targets stage, `POST /api/intelligence/runs` and its config,
  the review API). Nothing is written for it: the decisions table holds human decisions only.
- An eligible domain stays searchable across runs, including runs without LLM 1, because
  `merge_candidate` keeps a completed classification (with its version) when a later run has none.
- A human rejection always wins, even when LLM 1 rediscovers the domain as eligible.
- **Existing records** classified before Stage 8.1 have no `classification_version`, so they
  are never activated from their old classification; they need rediscovery under the new prompt
  (user decision, 22 Nov 2026). The classification backfill never writes the version.
- **Cap:** at most `MAX_AUTO_SEARCH_TARGETS` (default 5) automatic domains per run
  (`eligible_search_targets`). Configured and reviewer-approved domains do not count. Ranking
  (`auto_target_rank`) uses discovery results only: selected products whose model was on a page
  of the domain, then selected products whose global searches returned it, then all products
  seen, then distinct discovery pages, then the domain name. Domains over the cap are listed in
  `skipped_targets` with their rank and the reason.
- `PIPELINE_SEARCH_AUTO_APPROVED=false` switches automatic search off everywhere (the name
  predates Stage 8.1).
- **Cost:** each automatic domain adds one Exa domain search per selected product (about
  $0.007-0.008 in Stages 6-7), its page retrievals (about $0.001 each) and, with LLM 2, up to
  two LLM 2 calls per retrieved page (about $0.0006 each in Stage 6). With the default cap of 5
  and 2 products, that is roughly $0.10-0.15 extra per run (estimate from earlier runs, not
  measured for this change).
- **Benchmark permission is independent:** automatic targets always have
  `price_benchmark=false`. Only the workbook flag or a reviewer's explicit `benchmark_eligible`
  grants it, and `services/matching/trusted.py` (unchanged) rebuilds benchmark flags from the
  configured competitors and the decisions only, so an automatic domain is never a direct price
  comparison.

## Competitor classification (15 Nov 2026; Stage 8.1 update)

Separate from approval and price-benchmark eligibility: it never changes
`approval_status`, `price_benchmark` or a reviewer decision, and they never change it.

- **LLM 1** returns `competitor_class` in the existing classification call (no extra
  call): `DIRECT_COMPETITOR` (a retailer selling to UK customers with meaningful overlap with
  the searched products or catalogue categories), `RELATED_RETAILER` (a retailer of adjacent
  music products, or a marketplace) or `NEEDS_REVIEW` (manufacturer, directory, other, or
  insufficient evidence). `rationale` explains it.
- **Marketplace override (first, 15 Nov 2026):** a known marketplace domain
  (`MARKETPLACE_DOMAIN_LABELS`: Amazon, eBay, Reverb, Etsy, Gumtree) is always
  `RELATED_RETAILER`, whatever LLM 1 proposed and even without an LLM classification.
  The reason records the override and the original proposal.
- **Code safeguards** (`rules.classify_competitor`) otherwise only downgrade, never
  upgrade, and record a reason for every downgrade in `competitor_class_reasons`:
  - another domain typed `marketplace` and proposed as Direct becomes Related Retailer;
  - manufacturer, directory and other types become Needs Review;
  - Direct needs a selected product found on the domain or a supported catalogue category;
  - any class other than Needs Review needs at least one valid cited evidence URL;
  - no completed LLM 1 classification (no-LLM mode, failed, missing) gives Needs Review.
- Removed in Stage 8.1: the "UK selling must be yes" downgrade and the "Direct needs a `.uk`
  domain, GBP price or UK postcode" downgrade (it treated UK location as UK relevance).
- Direct does not use relevance or confidence.
- **Stored** on the candidate record (JSON; no SQL migration): `proposed_competitor_class`
  (LLM 1's answer), `competitor_class` (final), `competitor_class_reasons`,
  `classified_at` and `classification_model`. A later run without a completed LLM
  classification never erases a completed or backfilled classification (see "Rediscovery
  keeps the class" below).
- **API:** `GET /api/review/candidates` includes `competitor_class` and
  `proposed_competitor_class`; the detail endpoint also adds the reasons, timestamp and model.
- The 13 configured competitors are never classified. PMT (`pmtonline.co.uk`,
  `DISCOVERY_EXCLUDED_COMPETITOR_DOMAINS`) is excluded before classification and the
  review store refuses to write it.

### Classification backfill (dry run only, 16 Nov 2026)

`python -m app.agents.discovery.backfill --db <copy.sqlite3> [--domain D ...] [--output report.json]`
(from `backend/`) re-classifies stored review candidates from their saved evidence and
writes a JSON report (default `data/backfill_reports/`, git-ignored). Code:
`app/agents/discovery/backfill.py`; tests: `tests/test_classification_backfill.py`.

- It uses the live discovery path: `CLASSIFY_SYSTEM`, `classify_user`,
  `ClassificationBatch` and `apply_classification`, then `classify_competitor`. It does
  not call `decide` or `apply_review_decision`. There is one domain per LLM call, so
  failures stay per domain. The LLM is an injected `LLMClient` (`build_llm1` by default).
- The database is opened with a SQLite `mode=ro` URI and never through `ReviewStore`,
  whose constructor writes the schema version. A missing file is never created.
- The report gives, per domain: previous classification fields, the new proposal, the
  final class and its reasons, evidence and usable-evidence counts, review status
  (context only), whether the class changed, and a status. Statuses are `CLASSIFIED`,
  `SKIPPED_NO_EVIDENCE`, `SKIPPED_NO_USABLE_EVIDENCE` (no title or snippet text),
  `SKIPPED_PRECONFIGURED`, `SKIPPED_EXCLUDED_DOMAIN`, `NOT_FOUND`,
  `FAILED_INVALID_RECORD`, `FAILED_LLM_ERROR`, `FAILED_LLM_MISSING` and `FAILED_ERROR`.
  Skipped and failed domains get no proposal or final class (null means "leave
  unchanged"). A failure is never turned into Needs Review.
- **`--write` (16 Nov 2026)** must be given explicitly. It opens the file read-write
  without creating it (`mode=rw`) and takes the write lock (`BEGIN IMMEDIATE`). It then
  makes a timestamped backup with the SQLite backup API in `<db folder>/backups/`
  (`--backup-dir`; never overwritten) and verifies it: integrity check, schema
  version, and table contents identical to the database. Only then does it update the
  five fields in `CLASS_FIELDS` (`proposed_competitor_class`, `competitor_class`,
  `competitor_class_reasons`, `classified_at`, `classification_model`), and only for
  `CLASSIFIED` domains, all in one transaction.
- A row that changed after it was read is a `CONFLICT` and is not overwritten. Any
  update that would change another field rolls the whole transaction back (exit 4).
  After the commit the database is re-checked against the backup (exit 5 if a
  problem is found).
- Stop the dev server first: `uvicorn --reload` reopens `ReviewStore` on every
  reload, which rewrites two SQLite header bytes (change counter).
- **Executed (16 Nov 2026, user-approved):** all 48 stored domains were classified
  and written. 48 `openai/gpt-6-luna` calls, 75,034 + 9,564 tokens, $0.0142. Result:
  31 Direct Competitor, 4 Related Retailer, 13 Needs Review. The backup is
  `data/review/backups/competitor_review.pre-classification-backfill-20261115T223330Z.sqlite3`.
  The report is `data/backfill_reports/backfill-20261115T223330Z-dfd7b9.json`.
- **Rediscovery keeps the class (merge fix, 16 Nov 2026):** when a later discovery
  run's LLM classification for a domain is skipped, failed or missing,
  `merge_candidate` keeps the stored class. That includes the backfilled records,
  whose `classification_status` still says `SKIPPED_LLM_DISABLED`. A stored class
  counts when it is LLM-backed (`has_llm_competitor_class`: a final class, an LLM 1
  proposal and `classified_at`). The five `CLASS_FIELDS` are always kept together. A
  new successful (`COMPLETED`) LLM classification replaces them. Relevance and
  confidence are never used to infer or keep a class. A class that came only from
  the no-LLM rules (no proposal) is replaced by the newer run, as before. Tests:
  `tests/test_review_merge.py`.

## History: retired UK relevance designs

- **Option B (19 to 22 Nov 2026), retired in Stage 8.1:** LLM 1 UK evidence quotes
  (`uk_evidence`, kinds such as `UK_ADDRESS` and `UK_DELIVERY`), Python quote verification
  (`verify_uk_quotes`, `assess_uk_evidence`), the stored `uk_assessment`, the `uk_review` view,
  `reconcile_uk_seller`, the deterministic `uk_signals`, the auto-approval conditions
  (sufficient, uncontradicted, not mixed, 2 independent UK evidence pages, UK selling = yes,
  2 discovery evidence URLs, a selected product on the domain), the score thresholds
  (`DISCOVERY_AUTO_APPROVE_MIN_CONFIDENCE` / `_RELEVANCE` / `_EVIDENCE_URLS`,
  `DISCOVERY_REJECT_MAX_RELEVANCE`), the automatic low-relevance rejection, the UK SSE flags and
  the UK review UI. Old stored records with `uk_assessment`, `uk_signals` or `uk_seller` keys
  still load: the first two are ignored, `uk_seller` is read as `serves_uk_customers`.
- **Four-category assessment and UK eligibility gate (to 19 Nov 2026), retired with Option B:**
  `UK_BASED` / `SERVES_UK_FROM_ABROAD` / `NOT_SERVING_UK` / `UNKNOWN`, UK confidence, the
  reviewer UK override, `uk_gate.py`, `/api/review/uk-eligibility` and the migration CLI.
  Version-2 review databases still open; their UK tables are left untouched and never read.
  See `docs/competitor-review.md` ("Storage versions").

## Failure behaviour

- **Planning fails:** only the baseline queries run, with a warning.
- **Classification fails:** the affected candidates become `APPROVAL_REQUIRED`
  ("LLM classification failed"), with a warning.
- **A search fails:** it is counted in `usage.search_errors` with a warning;
  the other queries continue.
- **The LLM returns invalid JSON:** one repair attempt, then the failure
  handling above. Truncated output and provider errors raise `LLMOutputError`.
- **Truncated output** (`finish_reason=length`, e.g. a reasoning model that
  spends the whole `LLM1_MAX_COMPLETION_TOKENS` budget on reasoning): never
  parsed and never repaired (a retry would hit the same budget). The error
  names the token counts and the setting to raise. The tokens and cost the
  failed call spent are still counted in `usage`.
- **Model parameters:** `temperature` and `reasoning.effort` are sent only
  when `LLM1_TEMPERATURE` / `LLM1_REASONING_EFFORT` are set, because
  `provider.require_parameters` makes OpenRouter refuse a request with a
  parameter the model does not support. `openai/gpt-6-luna` (chosen 14 Nov
  2026) does not support temperature.

## Interfaces

- **API:** `POST /api/discovery/runs` with `{"product_ids": [...]}` (1–10)
  returns a `DiscoveryRunResult`. It runs synchronously and is not stored
  (temporary execution state); its candidates are added to the Phase 4
  review queue. It returns 503 only if the search provider is
  not configured; the message names the missing variable, never its value.
- **Mode:** `GET /api/discovery/config` returns the mode, the reason when
  disabled, the LLM 1 model when enabled, `auto_search_enabled` and
  `max_auto_search_targets`.
- **CLI:** `python -m app.agents.discovery.cli SB-001 SB-044`. It prints a
  summary and writes the full result to `data/discovery_runs/` (git-ignored,
  because it holds third-party snippets).

## Limitations

- Model matching on titles and URLs is a relevance signal, not identity.
  Exact product identity is Phase 6.
- `registrable_domain` uses a small built-in list of two-label suffixes
  (co.uk, com.au, …), not the full public suffix list.
- The candidate limit means lower-ranked domains are recorded but not
  evaluated in that run.
- Known competitors have no category-relevance data in the workbook. Relevance
  is recorded only as evidence (`known_competitor_evidence`), never assumed.
- Automatic eligibility trusts LLM 1's company type. A wrong "retailer" costs search spend and
  adds alternative-product rows (never benchmark prices); reject the domain to stop it.
- The Stage 8.1 prompt has not run against the real model yet (no paid run since); discovery
  relevance under it is unmeasured.

## Exception-based review (Stage 9.2, 23 Nov 2026)

Same pipeline (LLM 1 proposes, `rules.is_search_eligible` / `decide` decide); three additions so a
human only sees cases the evidence cannot settle. All are industry-neutral and use only the
evidence discovery already collects (titles and snippets of the domain's own pages):

- **Insufficient evidence** -> review: no discovery page on the domain names a searched product's
  brand or model; or, when discovery runs its business-profile search (`DISCOVERY_PROFILE_RESULTS`
  > 0), no about / contact / delivery page was found.
- **Conflicting business type** -> review: labelled retailer / specialist retailer, but the
  domain's own pages say trade-only, distributor / wholesaler, "find a dealer / where to buy",
  third-party sellers, price comparison or "rather than selling / we don't sell"
  (`rules.NON_RETAIL_SIGNALS`). The reason quotes the wording and gives the URL. Never an
  automatic rejection.
- **Conflicting identity** -> review: the same company name on another discovered domain or on a
  configured competitor's domain (`find_identity_conflicts`, stored as `identity_conflicts`, kept
  with the classification by `merge_candidate`).
- Every review outcome ends with `Needs review: evidence reviewed: <URLs>` (cited pages first).

Unchanged: exclusions (marketplace, directory, manufacturer and other types, excluded domains,
used-only), human rejection always wins, human approval keeps its behaviour and benchmark flag,
automatic eligibility never grants benchmark eligibility, the competitor class stays independent of
eligibility, LLM relevance / confidence and `serves_uk_customers` stay informational, the per-run
cap (`MAX_AUTO_SEARCH_TARGETS`) and `PIPELINE_SEARCH_AUTO_APPROVED` still apply.

On the 28 Stage 9 candidates (read-only recompute): only justpedals.co.uk changes (automatic ->
review: its about page says it shows "where to buy" "rather than selling"); all 11 user-approved
domains still qualify automatically.
