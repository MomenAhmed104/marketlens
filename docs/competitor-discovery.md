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

## Failure behaviour

- **Planning fails:** only the baseline queries run, with a warning.
- **Classification fails:** the affected candidates become `APPROVAL_REQUIRED`
  ("LLM classification failed"), with a warning.
- **A search fails:** it is counted in `usage.search_errors` with a warning;
  the other queries continue.
- **The LLM returns invalid JSON:** one repair attempt, then the failure
  handling above. Truncated output and provider errors raise `LLMOutputError`.

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
