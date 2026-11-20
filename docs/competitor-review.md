# Competitor domain review (Phase 4)

Discovery (Phase 3, [competitor-discovery.md](competitor-discovery.md)) finds
new companies, but with no LLM configured every one of them is
`APPROVAL_REQUIRED` and unclassified. Phase 4 lets a person review each
discovered domain, inspect its evidence, and approve or reject it. Decisions
persist across backend restarts and apply to every later discovery run.

## What is stored, and where

SQLite, using Python's standard `sqlite3` module (no new dependency). The file
is `data/review/competitor_review.sqlite3` (`REVIEW_DB_PATH`), and it is
git-ignored because it holds third-party snippets.

| Table | Contents |
|---|---|
| `review_candidates` | One row per discovered domain: its latest evidence snapshot, merged across runs, plus first/last seen, times seen and last run ID |
| `domain_decisions` | The current decision per domain. The domain is the primary key, so duplicate records are impossible |
| `decision_history` | Append-only. Every version of every decision (`UNIQUE(domain, version)`) |

- Rows are validated Pydantic JSON and are validated again on read.
- Writes run in `BEGIN IMMEDIATE` transactions.
- Discovery run results are still temporary: only each candidate's evidence
  snapshot is kept, never the whole run.
- Merging: evidence accumulates (capped at 200 items per domain). A completed
  LLM classification is never overwritten by a later unclassified (no-LLM) run.

A decision record (`DomainDecisionRecord`) holds:

- domain, decision (`APPROVED` or `REJECTED`)
- company name and company type, both as reviewed; `null` means unknown
- `benchmark_eligible`
- reviewer, decision time (UTC) and notes
- the evidence URLs the reviewer saw, and the source run ID
- version

## Statuses

A domain's review status is `PENDING` (no decision yet), `APPROVED` or
`REJECTED`. The automatic result from the Phase 3 rules (`AUTO_APPROVED`,
`APPROVAL_REQUIRED` or `REJECTED`) is shown separately as `automatic_status`.

## Rules (deterministic)

| Rule | Enforced in |
|---|---|
| Approving needs an explicit `company_type`. Sending `null` records "reviewed, still unknown"; the type is never filled in | `ApproveRequest` (field required) |
| Approval (may it be searched?) and benchmark eligibility (do its prices count as a direct retailer benchmark?) are separate | `DomainDecisionRecord` |
| `benchmark_eligible=true` only for `retailer`, `specialist_retailer` or `manufacturer` (manufacturers since 18 Nov 2026, user decision). Unknown, marketplace, directory and other types are refused (422, never silently changed) | `DomainDecisionRecord` validator and API |
| A rejected domain is never benchmark-eligible | Same |
| Known marketplaces (`amazon`, `ebay`, `reverb`, `etsy`, `gumtree` in any TLD; `MARKETPLACE_DOMAIN_LABELS`) can only be approved as `marketplace`, and are never auto-approved, even if LLM 1 calls them a retailer | API and `rules.decide` |
| The 13 preconfigured competitors cannot be approved or rejected here (409). Decisions stored for them are ignored by discovery. Gibson stays a manufacturer reference | API and agent |
| A domain must have been discovered before it can be decided (404 otherwise), so every decision has evidence | API |
| A second approve or reject is 409. Changing a decision needs an explicit `PUT`, which keeps the old version in the history | API and store |

## Effect on discovery runs

The API and CLI load the current decisions and pass them to the agent:

- **Targeted searches** (`DiscoveryRunResult.search_targets`) cover:
  - the 13 preconfigured competitors, unchanged;
  - every reviewer-`APPROVED` domain, even if this run did not rediscover it;
  - in `LLM_ENABLED` mode, domains auto-approved in this run only. Auto-approval is never persisted.
- **Pending and rejected domains never get targeted queries.** A reviewer
  rejection overrides an LLM auto-approval.
- Each targeted query carries `price_benchmark` from the decision, so an
  approved marketplace or unknown-type domain is searched but never used as a
  benchmark. `company_type` stays `null` for domains approved as unknown.
- Candidates in the run show `review_decision` and `automatic_approval_status`
  alongside the overriding `approval_status`.
- After each run, its candidates are added to the review queue. The CLI's
  `--no-review-queue` flag skips this.

## API

All routes are under `/api/review`.

| Method | Path | Description |
|---|---|---|
| GET | `/candidates?status=PENDING\|APPROVED\|REJECTED` | Discovered domains with review status, decision, search and benchmark eligibility, discovery mode, classification status, and confidence/relevance (null unless LLM 1 classified the domain) |
| GET | `/candidates/{domain}` | The above plus evidence (URL, title, snippet, query, provider, retrieval time), automatic reasons and decision history |
| POST | `/candidates/{domain}/approve` | Body `{reviewer, company_type (required, may be null), company_name?, benchmark_eligible?, notes?}`. 201 |
| POST | `/candidates/{domain}/reject` | Body `{reviewer, company_type?, company_name?, notes?}`. 201 |
| PUT | `/decisions/{domain}` | Body `{decision, reviewer, company_type, company_name?, benchmark_eligible?, notes?}`. Explicit update; increments the version |
| GET | `/decisions?decision=APPROVED\|REJECTED` | Current decisions |

`{domain}` accepts a hostname and is normalised to its registrable domain
(`www.shop.co.uk` becomes `shop.co.uk`). Unknown body fields are rejected
(422). Responses never include settings or keys.

Every candidate has `uk_review` (additive): whether the verified UK evidence is sufficient,
contradicted or mixed, from how many independent pages, the verified evidence, unverified
claims and what is missing. There is no UK category and no UK override (Option B, 19 Nov
2026): the reviewer's judgement is the approve/reject decision and its `notes`, kept in the
decision history. Bodies that still send `uk_relevance_override` are refused (422). A
decision never changes the stored evidence, and approval never implies benchmark
permission. Rules: `docs/competitor-discovery.md` ("UK relevance evidence").

## Storage versions

- Version 1 is the three tables above. New databases are created at version 1.
- Version 2 (checkpoint a6d5a15) added five tables for the retired UK eligibility gate.
  Version-2 files still open and work for the three core tables; their UK tables are
  never read, written or dropped, and the file's version is never changed.
- Opening any existing file never writes it; unsupported versions are refused. There is
  no migration (the `review_migration` CLI was removed with the gate). Tests:
  `tests/test_review_store_versions.py`.

## Frontend

The **Competitor domain review** section on the main page:

- Runs discovery for product IDs and shows the mode and any warnings.
- Takes a reviewer name, stored in the browser.
- Filters by Pending, Approved, Rejected or All, with counts.
- Shows each company's name (or "Company name unknown") and domain, its status, and its type (or **Unknown type**).
- Shows an **Unclassified · discovered in no-LLM mode** badge where it applies, plus a known-marketplace badge.
- Shows search and benchmark eligibility, confidence and relevance (or "not assessed"), UK signals and products found.
- Shows the reviewer, time and notes for decided domains.
- Has approve, reject and change-decision forms. The type must be chosen, or explicitly set to unknown. The benchmark checkbox is only enabled for retailer and manufacturer types.
- Has an expandable evidence list (links, snippets, query, retrieval time) and the decision history.

## Limitations

- There is no authentication. The reviewer name is self-declared text.
- The marketplace list is a short, configurable label list, not a registry.
- The review queue keeps evidence until the database file is deleted. There
  is no pruning yet.
- The UI has no automated frontend tests (none exist in the project). It is
  covered by lint, type-checked build and backend API tests.

## Stored records and thresholds (checked 20 November 2026)

- The real review database holds 48 candidates (39 pending, 7 approved, 2
  rejected). None has a UK evidence assessment: 38 come from no-LLM runs and 10
  from an LLM 1 run on 14 November 2026, before Option B. They show "UK evidence
  not assessed" until rediscovered with LLM 1, and are not backfilled (D9).
- Auto-approval thresholds are the settings defaults (confidence 0.85,
  relevance 0.75, 2 evidence URLs; rejection at relevance 0.3), not overridden
  in `.env`. They are uncalibrated starting values; calibrate them against a
  labelled set (Phase 9) before changing them.
- Version-2 review databases (retired UK gate) are still opened read/write for
  the three core tables; their UK tables are never read, written or dropped.
