# UK eligibility gate and review API

Operational product searches and price benchmarks use only competitors operated
by a UK business. One deterministic gate (`backend/app/services/uk_gate.py`)
decides this per domain. Reviewers record assessments and exceptions through
the review API. The gate itself never guesses.

## Statuses (default deny)

| Status | Meaning | Search | Benchmark |
|---|---|---|---|
| `UK_ELIGIBLE` | Strong evidence of a real UK business | allowed | allowed |
| `INSUFFICIENT_EVIDENCE` | Not enough evidence either way. **Every unassessed domain has this status**, configured or discovered | blocked unless an exception allows it | blocked unless an exception allows it |
| `NOT_UK_ELIGIBLE` | Evidence that the operator is outside the UK | always blocked | always blocked |

- **Default deny:** with no stored assessment, a domain is `INSUFFICIENT_EVIDENCE`
  and blocked. This includes the 13 configured competitors. While the review
  database is at schema version 1, there are no UK tables at all, so **every
  domain is blocked and runs search nothing** until the migration is applied and
  assessments are recorded.
- The gate result (`UkGateDecision`) is always computed on the server from stored
  records. It is never accepted from a client, a run payload or a file. That
  includes the matching API and the matching CLI, which rebuild competitors from
  trusted state (`app/services/matching/trusted.py`).

## Exceptions

An exception is an explicit, user-approved permission for a domain whose evidence
is still `INSUFFICIENT_EVIDENCE`. It never changes the evidence status.

- **Two independent scopes:** `allow_search` and `allow_benchmark`. Benchmarking
  requires search, because a domain that is never searched has no prices. A
  search-only exception never enables benchmarking.
- **Only for `INSUFFICIENT_EVIDENCE`:** granting is refused (HTTP 409
  `EXCEPTION_NOT_ALLOWED`) while the domain is `UK_ELIGIBLE` or
  `NOT_UK_ELIGIBLE`. An unassessed domain counts as `INSUFFICIENT_EVIDENCE`. A
  revocation (both scopes false) is always accepted.
- **Never overrides `NOT_UK_ELIGIBLE`:** the gate ignores any exception on a
  `NOT_UK_ELIGIBLE` domain.
- **Revoked on every status change:** when an assessment changes status, an
  active exception is revoked in the same transaction. The store writes a new
  exception version with both scopes false and `revoked_by_status_change: true`,
  recording the reviewer and time. A later return to `INSUFFICIENT_EVIDENCE`
  never revives it; a new exception needs a new approval.
- Approved for this demo (not seeded yet): guitarguitar.co.uk and bax-shop.co.uk
  with search and benchmark, gibson.com with search only.

## Evidence

- `UK_ELIGIBLE` needs at least one **strong** item: `PHYSICAL_STORE`,
  `REGISTERED_COMPANY` (a UK registered company with retail operations),
  `UK_OPERATING_ADDRESS` or `UK_RETAIL_OPERATION`. Weak signals (`WEAK_SIGNAL`:
  GBP prices, UK delivery, a .co.uk domain, UK customers) are never enough on
  their own.
- `NOT_UK_ELIGIBLE` needs at least one evidence item (e.g. `NON_UK_OPERATOR`).
  `INSUFFICIENT_EVIDENCE` may have none. Every assessment needs a `reasoning`.
- **`REVIEWER_SUPPLIED`**: a page the reviewer read themselves, such as a Companies
  House entry or a store page. It must have a `note` saying how and when it was
  read. It cannot be checked against saved data, so it rests on the reviewer.
- **`SAVED_EVIDENCE`**: verified, never trusted. The URL must be one of the
  domain's stored discovery evidence items, and the quote must appear verbatim
  (whitespace and case aside) in that item's stored title or snippet. Otherwise
  the request is 422 `UNVERIFIED_EVIDENCE`. Configured competitors have no stored
  discovery evidence, so their evidence is reviewer-supplied
  (`app/services/uk_evidence.py`).

## Separate concepts

These are independent; changing one never changes another.

| Concept | Where | Decides |
|---|---|---|
| UK eligibility (and exceptions) | `uk_eligibility*`, `uk_gate_exception*` tables | Whether the UK gate allows search and benchmarking |
| Domain approval | `domain_decisions` (Phase 4) | Whether a discovered domain may be searched at all |
| Competitor classification | candidate record (`competitor_class`) | Direct Competitor / Related Retailer / Needs Review (display only) |
| Benchmark eligibility | workbook `priority` (configured) or the reviewer's `benchmark_eligible` (discovered) | Whether the domain's prices may be compared |

A price is compared only when all of these hold: an exact match, a valid and
verified GBP price, a page on the competitor's own domain, the competitor is
currently a search target (configured, reviewer-approved, or auto-approved in
this run in `LLM_ENABLED` mode), its benchmark flag
is true, and the UK gate allows benchmarking.

### Manufacturer benchmarking (decision, 18 Nov 2026)

Manufacturers join the main price benchmark alongside retailers
(`BENCHMARK_ELIGIBLE_TYPES` = retailer, specialist retailer, manufacturer). Each
still needs its own benchmark flag and UK clearance. Yamaha Music London
(`manufacturer`, `price_benchmark=true`) is compared once it is UK-cleared.
Gibson (`manufacturer`, workbook priority "Reference only", so
`price_benchmark=false`) is never compared, whatever its UK status or exception.
Marketplaces, directories and other types can never be benchmarks.

## Review API

Router: `backend/app/api/uk_eligibility.py`, prefix `/api/review/uk-eligibility`.

| Method and path | Purpose |
|---|---|
| `GET /api/review/uk-eligibility` | `{uk_supported, schema_version, domains}`: every configured competitor and every discovered domain, assessed or not, once per canonical domain, with assessments and exceptions merged in. Each entry: `domain, configured_competitor, in_review_queue, assessable, eligibility, exception, gate` |
| `GET /api/review/uk-eligibility/{domain}` | One domain, plus `eligibility_history`, `exception_history` (oldest first) and any audit `proposal`. An unassessed domain is 200 (`INSUFFICIENT_EVIDENCE`), not 404 |
| `PUT /api/review/uk-eligibility/{domain}` | Create or update an assessment: `{status, confidence, reasoning, evidence[], review_flags[], reviewer, notes?, expected_version}` |
| `PUT /api/review/uk-eligibility/{domain}/exception` | Grant, narrow or revoke: `{allow_search, allow_benchmark, reason, reviewer, notes?, expected_version}` |

- **Domains:** canonicalised (case, surrounding whitespace, trailing dot and a
  leading `www.`). URLs, ports, other subdomains, bare suffixes and invalid names
  are 422 `INVALID_DOMAIN`.
- **Who can be written:** only configured competitors and discovered domains (the
  review queue). Any other valid domain is 404 `UNKNOWN_DOMAIN`, as in the review
  API for undiscovered domains. Reads are not restricted, so a stored record stays
  readable.
- **Optimistic concurrency:** `expected_version` is required. Send the version
  you read, or 0 if nothing is stored. A mismatch is 409 `VERSION_CONFLICT` with
  `current_version`: reload and retry.
- **Other errors:**
  - 409 `EXCEPTION_NOT_ALLOWED` (with `evidence_status`).
  - 422 `INVALID_RECORD` (model rules, e.g. weak-only evidence for `UK_ELIGIBLE`, or benchmark without search).
  - 422 `UNVERIFIED_EVIDENCE`.
  - 422 for unknown or forged body fields (`price_benchmark`, `competitor_class`, `search_allowed`, `version`, `decided_at`, ...).
  - 503 `MIGRATION_REQUIRED` for writes on a version-1 database.
- **Audit:** every write appends to an append-only history with the reviewer, the
  time, and the reason or evidence. There are no update or delete routes for
  history. `decided_at` and versions are set by the server.

## Local-only limitation

The demo has **no authentication**. `reviewer` is self-declared in each request
body, and the write endpoints accept any client that can reach the backend. Run
it on a trusted local machine only. Authentication is a prerequisite for any
shared use.

## Schema version 2 migration

The UK tables need schema version 2. Opening a version-1 database never writes
it (no silent migration). The real database is still at version 1.

```bash
cd backend
# 1. Dry run (default): opens the database read-only and reports what would change.
.venv/Scripts/python.exe -m app.services.review_migration --db ../data/review/competitor_review.sqlite3
# 2. Apply, only after an explicit decision:
.venv/Scripts/python.exe -m app.services.review_migration --db ../data/review/competitor_review.sqlite3 --apply
```

`--apply`:
1. Takes an `IMMEDIATE` write lock.
2. Writes a timestamped backup to `data/review/backups/` with the SQLite backup
   API and verifies it: integrity check, version 1, identical rows. An existing
   backup file is never overwritten.
3. Creates the five UK tables, empty, and sets `user_version = 2` in the same
   transaction.
4. After commit, checks that the existing tables are identical to the backup and
   the new tables are empty.

Any failure before commit rolls everything back. A database already at version 2
is left untouched. No assessment or exception is written; seeding is a separate,
explicit step.
