# Competitor discovery (LLM 1)

Implemented in Phase 3, in `backend/app/agents/discovery/`. LLM 1 proposes;
deterministic Python validates and decides.

## Execution modes

The mode is chosen at run time and recorded on every result (`mode`, plus
`llm_disabled_reason` when disabled). `GET /api/discovery/config` reports the
mode a run would use.

| | `LLM_ENABLED` | `LLM_DISABLED` |
|---|---|---|
| When | `OPENROUTER_API_KEY` and `LLM1_MODEL` set, and `DISCOVERY_USE_LLM=true` (default) | Either is missing, or `DISCOVERY_USE_LLM=false` |
| Global queries | Baseline + validated LLM 1 queries | Baseline `"<brand> <model> buy UK"` only |
| Exa searches, grouping, dedup, exclusions, profile evidence, UK signals | Yes | Yes (identical) |
| Company name, type, categories, relevance, confidence, UK selling | Proposed by LLM 1, then validated | **Left null/unknown**; never filled in |
| New-company decision | Rules below (can auto-approve) | Always `APPROVAL_REQUIRED` ("LLM classification skipped (LLM disabled)") |
| Targeted queries | Known + auto-approved domains; baseline + validated variants | Known competitors only; baseline query only |
| Warnings | Only for actual problems | "LLM query planning skipped (…)" and "LLM classification skipped (…)" |

Missing LLM configuration is never an error: the app starts, known competitors
are available, and discovery runs in `LLM_DISABLED` mode. Only a missing
search provider or key makes discovery unavailable (HTTP 503 / CLI exit 3).

Each candidate also carries `classification_status`: `COMPLETED`,
`SKIPPED_LLM_DISABLED`, `FAILED` (the call failed or its output was unusable)
or `MISSING` (the LLM returned nothing for that domain). Only `COMPLETED`
candidates can be auto-approved.

## Flow

1. **Known competitors.** The 13 workbook competitors are loaded as
   `PRECONFIGURED` and never re-decided. Gibson stays a manufacturer reference
   with `price_benchmark=false`. Its targeted queries carry that flag, so later
   phases never mix its prices into the retailer benchmark.
2. **Planning (LLM 1).** For each selected product, LLM 1 proposes up to N
   global queries and up to M targeted query variants. Code then:
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
   - Known competitors: their hits are kept as *evidence* (which selected
     products they list), never as new candidates.
   - Non-shop platforms (YouTube, Reddit, …): excluded.
   - The rest are ranked by evidence (distinct products matched, model
     mentions, hits). The top `DISCOVERY_MAX_CANDIDATES` are evaluated; the
     others are recorded as "not evaluated".
5. **Profile evidence.** One search restricted to each candidate's domain
   ("about us / contact / delivery") collects evidence about the business itself.
6. **UK signals (deterministic).**
   - Strong signals: `uk_domain` (.uk), `gbp_price` (a £ amount in titles or
     snippets) and `uk_postcode`.
   - Weak signal: `uk_mention`.
7. **Classification (LLM 1).** Evidence is sent as delimited, untrusted JSON.
   The model returns the company name *as written in the evidence*, company
   type, categories, UK selling (yes, no or unknown), whether it sells new
   instruments, relevance, confidence, a rationale and the cited evidence URLs.
8. **Validation (deterministic).**
   - Cited URLs not present in the evidence are discarded.
   - Categories outside the catalogue are discarded.
   - The company name is *verified* only if it appears in a title or snippet
     from that domain; a lookalike hostname is never enough.
   - The rationale is trimmed.
9. **Decision (deterministic, `rules.decide`).** The outcomes are
   `AUTO_APPROVED`, `APPROVAL_REQUIRED` or `REJECTED`; the table below gives the
   rules.
10. **Reviewer decisions (Phase 4).** Persisted decisions override step 9:
    a rejected domain is never searched, and an approved domain is searched
    with its reviewed benchmark flag. See [competitor-review.md](competitor-review.md).
11. **Targeted queries** are generated only for search targets: preconfigured
    competitors, reviewer-approved domains (even if not rediscovered) and
    domains auto-approved in this run. Each product gets its baseline
    query plus up to M validated LLM variants, restricted to the domain.
    Pending and rejected domains get none.

| Outcome | Rule |
|---|---|
| `REJECTED` | No discovery evidence. Type `other` with relevance ≤ 0.3. LLM says it doesn't sell to the UK *and* no strong UK signal. Relevance ≤ 0.3 *and* no selected product found on the domain |
| `AUTO_APPROVED` | **All** of: type retailer or specialist retailer; confidence ≥ 0.85; relevance ≥ 0.75; company name verified in the evidence; at least one strong UK signal; UK selling = yes; at least one page mentioning a selected product's model; at least 2 discovery evidence URLs; at least one valid cited URL; not stated to sell only used goods |
| `APPROVAL_REQUIRED` | Everything else, including **all marketplaces, directories and manufacturers**, any domain on the known-marketplace list (`MARKETPLACE_DOMAIN_LABELS`) whatever its classified type, conflicting evidence, and any candidate whose classification failed. The decision reasons list each unmet criterion |

The thresholds are configurable (`DISCOVERY_*`) and are **starting values, not
calibrated**. `DiscoveryRunResult.thresholds.calibrated` is `false` until they
have been evaluated against labelled runs.

## Competitor classification (15 Nov 2026)

Separate from approval and price-benchmark eligibility: it never changes
`approval_status`, `price_benchmark` or a reviewer decision, and they never change it.

- **LLM 1** returns `competitor_class` in the existing classification call (no extra
  call): `DIRECT_COMPETITOR` (a UK retailer with meaningful overlap with the searched
  products or catalogue categories), `RELATED_RETAILER` (a retailer of adjacent music
  products, or a marketplace) or `NEEDS_REVIEW` (manufacturer, directory, other, or
  insufficient evidence or uncertain UK selling). `rationale` explains it.
- **Marketplace override (first, 15 Nov 2026):** a known marketplace domain
  (`MARKETPLACE_DOMAIN_LABELS`: Amazon, eBay, Reverb, Etsy, Gumtree) is always
  `RELATED_RETAILER`, whatever LLM 1 proposed and even without an LLM classification.
  The reason records the override and the original proposal.
- **Code safeguards** (`rules.classify_competitor`) otherwise only downgrade, never
  upgrade, and record a reason for every downgrade in `competitor_class_reasons`:
  - another domain typed `marketplace` and proposed as Direct becomes Related Retailer;
  - manufacturer, directory and other types become Needs Review;
  - a retailer needs UK selling `yes`, otherwise Needs Review;
  - Direct also needs a strong UK signal (uk domain, GBP price or UK postcode) and a
    selected product found on the domain or a supported catalogue category;
  - any class other than Needs Review needs at least one valid cited evidence URL;
  - no completed LLM 1 classification (no-LLM mode, failed, missing) gives Needs Review.
- Direct does not use relevance, confidence or the auto-approval thresholds.
- **Stored** on the candidate record (JSON; no SQL migration): `proposed_competitor_class`
  (LLM 1's answer), `competitor_class` (final), `competitor_class_reasons`,
  `classified_at` and `classification_model`. Records stored before this change have
  these empty. A later run without a completed LLM classification never erases
  a completed or backfilled classification (see "Rediscovery keeps the class" below).
- **API:** `GET /api/review/candidates` adds `competitor_class` and
  `proposed_competitor_class`; the detail endpoint also adds the reasons, timestamp and
  model. All additive and optional. The frontend does not use them yet.
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

## UK relevance evidence (Option B, 19 Nov 2026)

LLM 1 discovers UK-focused competitors (UK-based shops and shops abroad that serve UK
customers) and cites the evidence for how each relates to the UK market. **There is no UK
category and no UK confidence:** Python verifies the quotes and decides whether the
evidence is sufficient. The result is stored on the candidate as `uk_assessment`
(`UkEvidenceAssessment`) and shown in review with its uncertainty. There is no separate UK
eligibility gate (retired the same day; see "Retired" below).

Three concepts stay separate and are stored separately: **UK relevance evidence**,
**domain approval** (Phase 4 decisions) and **benchmark permission** (the workbook flag or
the reviewer's `benchmark_eligible`). None is derived from another.

- **LLM output** (`CompanyClassification.uk_evidence`): up to 5 items (`url`, exact
  `quote`, `kind`), with no verdict. Kinds: `UK_PHYSICAL_STORE`, `UK_ADDRESS`,
  `UK_COMPANY_REGISTRATION`, `UK_DELIVERY`, `NON_UK_OPERATOR`, `NO_UK_SERVICE`,
  `WEAK_SIGNAL`. Required by the strict output schema. A malformed UK answer never fails
  the classification or the batch: malformed quotes are dropped and each one is recorded
  (`_isolate_uk_fields`). Legacy `uk_relevance` / `uk_confidence` keys are ignored.
  Core-field errors fail exactly as before (repair retry, then `LLMOutputError`).
- **Quote verification** (`rules.verify_uk_quotes`, unchanged from Phase A): a quote
  counts only if it is verbatim (whitespace and case aside) in the stored title or
  snippet of the cited URL (`text_match.quote_in_evidence`), and its text fits its kind:
  a UK postcode for an address, "registered" plus a number for a company, positive UK
  delivery wording (no negation; `sell` counts) for delivery, an explicit "does not ship
  to the UK" for no UK service, and a visitable shop (not an online store) for a physical
  store. "No delivery charge to the UK" is not a no-UK-service statement. GBP prices, a
  `.uk` domain or "UK" alone never count.
- **Evidence result** (`rules.assess_uk_evidence`, `summarise_uk_evidence`):
  - `sufficient`: a supporting UK shop, UK address or UK company registration quote; or a
    supporting based-abroad (`NON_UK_OPERATOR`) quote together with a supporting UK
    delivery quote.
  - `contradicted`: a supporting quote says the UK is not delivered to or served.
  - `mixed_location`: UK-presence evidence and a based-abroad quote at the same time.
  - `independent_sources`: distinct evidence pages (canonical URLs) among the quotes that
    support UK relevance. Several quotes from one page count as one source.
  - `evidence` holds the supporting quotes; `unverified_claims` holds the others (context
    only, never presented as verified). Both are clipped to 500 characters after
    verification. `reasons` says what was discarded or cleaned.
  - Every completed LLM 1 classification is assessed. No quotes means insufficient
    evidence, never "assumed UK". With LLM 1 disabled nothing is assessed
    (`uk_assessment = null`) and every new domain stays pending.
- **Approval (`decide`):**
  - a verified contradiction → `APPROVAL_REQUIRED` with the reason, never rejected
    automatically (decision D4);
  - auto-approval needs sufficient verified UK evidence, no contradiction, no mixed
    location, and supporting quotes from **at least two different evidence pages**
    (`AUTO_APPROVE_MIN_UK_SOURCES = 2`, user decision 19 Nov 2026), plus every other
    existing criterion (type, confidence, relevance, verified name, product match,
    evidence URLs, not a marketplace). `.uk` domain, GBP price and UK postcode signals are
    **not** an auto-approval requirement, so an overseas retailer that demonstrably serves
    the UK is not disadvantaged. They are still recorded (`uk_signals`) and still used by
    the Direct Competitor class safeguard (unchanged).
  - **Auto-approval never grants benchmark permission:** `decide` always sets
    `price_benchmark=false`. Only a reviewer's explicit `benchmark_eligible` (type rules
    apply) or the workbook flag for configured competitors makes a domain a benchmark.
- **`uk_seller` follows the verified evidence** (`rules.reconcile_uk_seller`, with a
  validation note when it changes): yes only when the evidence is sufficient, not
  contradicted, and LLM 1 also said yes; no only for a verified contradiction with no
  sufficient UK evidence; otherwise unknown. An unverified "no" never rejects.
- **Review view** (`rules.uk_review`, deterministic, never stored): sufficiency,
  contradiction, mixed location and sources are recomputed from the stored supporting
  quotes, so a stored flag is never trusted on its own. The view (`UkReview`) has
  `assessed`, `sufficient`, `contradicted`, `mixed_location`, `independent_sources`, a
  plain-English `summary`, `reasons`, `missing_evidence`, `verified_evidence` and
  `unverified_claims`. If a reviewer approves despite a contradiction or without
  sufficient evidence, the summary says so; the evidence and the contradiction stay
  visible and unchanged.
- **Reviewer decisions:** approve or reject explicitly, with optional free-text `notes`
  (kept in the append-only decision history). There is no UK override; requests that
  still send `uk_relevance_override` get 422. A reviewer-approved domain is searched even
  with contradictory evidence (user decision 19 Nov 2026).
- **API and events:** `GET /api/review/candidates` and `/candidates/{domain}` return
  `uk_review` for every candidate (`assessed: false` for older records). Discovery API
  candidates carry `uk_assessment` and the same `uk_review`. `competitor_discovered`
  events carry `uk_assessed`, `uk_evidence_sufficient`, `uk_evidence_contradicted` and
  `uk_independent_sources`.
- **Evidence for LLM 1 and search** (unchanged from Phase A): at most 8 items per domain,
  with up to 3 slots kept for profile pages; profile snippets up to 400 characters,
  discovery snippets up to 280. The planning prompt asks for UK shops and shops abroad
  that deliver to the UK. One profile search per candidate looks for about/contact pages,
  addresses, showrooms, company registration and delivery terms.
- **Persistence:** `uk_assessment` is part of the stored candidate JSON (no migration).
  `merge_candidate` keeps it when a later run has no completed classification, and a
  stored assessment is never replaced by none. Records stored with the retired category
  fields load (the extra keys are ignored and never trusted) and are never rewritten.
- **Dashboard:** each Domain Review card shows what the verified evidence says ("Verified
  UK evidence", "Contradictory UK evidence", "Insufficient UK evidence", "UK evidence:
  check location" or "not assessed") and from how many independent pages; "Show evidence"
  adds the verified UK evidence (kind, quote, link), what is missing, and the unverified
  claims in their own section. Approving a contradicted or insufficient domain shows a
  warning but is not blocked.
- Tests: `tests/test_uk_relevance.py`, `tests/test_uk_classification_schema.py`,
  `tests/test_uk_discovery_integration.py`, `tests/test_uk_review.py`,
  `tests/test_search_targets.py`, `frontend/src/pages/DomainReviewUk.test.tsx`.

### Retired (Option B, 19 Nov 2026)

- The formal four-category assessment (`UK_BASED`, `SERVES_UK_FROM_ABROAD`,
  `NOT_SERVING_UK`, `UNKNOWN`), its confidence, the review recommendation and the
  reviewer's UK relevance override.
- The separate UK eligibility gate (`uk_gate.py`), its records, exceptions, review API
  (`/api/review/uk-eligibility`) and migration CLI (`review_migration`). Product searches
  use the configured competitors, reviewer-approved domains and (LLM enabled) this run's
  auto-approved domains; benchmark comparisons use only the benchmark flag. Version-2
  review databases still open; their UK tables are left untouched and never read. See
  `docs/competitor-review.md` ("Storage versions").

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
  disabled, the LLM 1 model when enabled, and the thresholds.
- **CLI:** `python -m app.agents.discovery.cli SB-001 SB-044`. It prints a
  summary and writes the full result to `data/discovery_runs/` (git-ignored,
  because it holds third-party snippets).

## Limitations

- Model matching on titles and URLs is a relevance signal, not identity.
  Exact product identity is Phase 6.
- `registrable_domain` uses a small built-in list of two-label suffixes
  (co.uk, com.au, …), not the full public suffix list.
- The UK postcode regex can, rarely, match a product code.
- The candidate limit means lower-ranked domains are recorded but not
  evaluated in that run.
- Known competitors have no category-relevance data in the workbook. Relevance
  is recorded only as evidence (`known_competitor_evidence`), never assumed.

## Evidence URLs versus UK evidence pages (post-Stage 7, 20 November 2026)

Two different counts feed auto-approval, and both still need 2:

- **Discovery evidence URLs** (`DISCOVERY_AUTO_APPROVE_MIN_EVIDENCE_URLS=2`): distinct URLs of
  `DISCOVERY_RESULT` evidence, i.e. pages on which the domain appeared in the global product searches.
  A relevance signal. Domain-profile pages are not counted.
- **Independent UK evidence pages** (`AUTO_APPROVE_MIN_UK_SOURCES=2`): distinct pages behind verified,
  supporting UK quotes, from discovery results or profile pages.

In Stage 7, Wildwire, Spears, Mann's, Promenade and Roland Store London had 2-3 UK evidence pages
(mostly about/contact/terms profile pages) but appeared on only 1 page in the global searches, so they
needed review. That is the intended rule, not a counting bug. The reason now reads "1 discovery evidence
URL(s) < 2: the domain appeared on 1 distinct page(s) in the global product searches; its N
domain-profile page(s) (used for UK evidence) do not count here". Thresholds unchanged.
