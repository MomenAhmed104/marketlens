# Live dashboard, Phase 8

React + Vite + TypeScript, in `frontend/src/`. The dashboard calls the existing
backend APIs only; no backend behaviour was changed for it.

**Design v2 (13 Nov 2026, user-approved):** the second redesign replaced a
generic admin-panel look.

- **Visual direction:** navy navigation and hero bands, a cobalt accent, with
  cyan used only on navy, and varied emphasis instead of identical white cards.
- **User decisions:**
  - the Overview summarises **one selected run**;
  - **no trend charts**, only one distribution bar of backend counts;
  - system fonts and **no new dependencies**;
  - evidence opens in a **side drawer**;
  - eligible prices are shown as **comparison cards**.

## Shell

- **Sidebar** (`app/Sidebar.tsx`):
  - a pedal brand mark;
  - grouped navigation: **Analyse** (Overview, Product Intelligence) and
    **Operate** (Live Runs, Domain Review);
  - the active item gets a navy pill, a cyan icon and a cyan side bar;
  - a "Live" marker on Live Runs while a run is active, and an amber pending
    count on Domain Review (beside the label, never covering the icon);
  - a **System** card with on/off dots for search, LLM 1, LLM 2 and backend.

  Each link has one explicit accessible name, for example "Domain Review, 25
  pending review".
- **Top bar** (`app/TopBar.tsx`):
  - an eyebrow (the page group) above the page title;
  - the **run switcher** (`app/RunSwitcher.tsx`): a button showing the product
    name, status pill and date (no seconds). It opens a keyboard listbox of the
    runs in memory: arrows, Home/End, Enter, Escape (focus returns to the
    button), plus a "no run" option;
  - a **connection badge** shown only when it adds information (Connecting,
    Live, Reconnecting, Polling, Expired, Error), never "Finished" next to
    "Completed".
- **Routing:** hash routes (`#/overview`, `#/products/<id>`, `#/runs`,
  `#/domains`), with the run kept in `?run=`.
- **Run state:** `app/RunProvider.tsx` holds the single `useRunStream`, so one
  SSE connection survives page changes.

## Pages

### Overview (`pages/Overview.tsx`)

1. **Hero (navy).**
   - The run ID, and the product name with its ID (no longer the ID only).
   - The **key finding** headline and detail from `keyFinding()`.
   - "✓ Every completed product has an exact match" when it applies.
   - Buttons: Open product results, Run details.
   - Metadata: status, start time, discovery mode, extraction mode.
   - A coloured edge shows the finding's tone.
2. **Match distribution.**
   - One stacked bar: segment widths follow the backend counts (CSS
     `flex-grow`). The legend shows counts only, and there is a hidden table for
     screen readers. "No match" is a neutral hatched segment.
   - Per-product compact bars for multi-product runs.
3. **Four stat tiles with different emphasis:**
   - products analysed;
   - competitors searched (breakdown by approval);
   - needs review (amber when above 0);
   - eligible prices (navy with a cyan figure when above 0; a dashed "None ·
     Unavailable: extraction off" otherwise).
4. **Price comparisons:**
   - **cards** (competitor, Stompbox, difference "competitor − Stompbox" from
     the backend, direction, source link);
   - or, when there are none, the **price explainer** (`priceGate()`): a
     checklist of exact matches found, prices extracted (with the LLM 2 reason
     and how to enable it) and verified GBP prices, plus the backend's
     `comparison.reasons` for the exact matches that were not compared.
5. **Needs attention** (only when there is something to show): failed products,
   products with no candidates (worded so it is never read as unavailability),
   and products with only review, similar or no-match results.
6. **Competitor coverage:** a table of the competitors with candidates (status
   counts as coloured chips, benchmark ★, search errors). Competitors searched
   with no candidates are folded into a chip list.

### Product Intelligence (`pages/ProductIntelligence.tsx`)

- **Product switcher** (multi-product runs).
- **Product header:**
  - a navy top edge, the ID and category eyebrow, the name in 26 px, brand and
    model;
  - a compact distribution bar with the candidate and comparable counts;
  - the Stompbox price at 34 px, or "missing (nothing can be compared)", with
    the snapshot date and source link.
- **Comparison groups** (`dashboard/ComparisonTable.tsx`): exact and review
  first (direct competitor prices), then a separate **Alternative products**
  section with equivalent variants and similar alternatives, then no match
  (collapsed). Each group has a coloured dot and count, a short note, and a
  coloured rail on every row. Equivalent variants use the teal `--equivalent`
  token.
  - **Rows:** competitor (domain in monospace, approval pill if not
    configured, "Not a price benchmark"), page and match confidence with "LLM 2
    proposed …" (and "Python more cautious" / "LLM 2 more cautious" when they
    differ), condition (the backend's condition label for alternatives),
    availability status, and the price.
  - **Difference:** a chip shown **only when `comparison.eligible`**; otherwise
    "Not compared" with the first backend reason.
  - **Alternative products** have no difference column at all: the backend's
    `alternative_price` is shown as "information only · not benchmarked", or
    "Not shown" with its reason. Alternative prices never appear in the
    exact-match table or the price KPIs (D-28).
- **Evidence drawer** (`dashboard/EvidencePanel.tsx`, `components/Drawer.tsx`):
  a modal side panel, full screen on mobile. It traps focus, closes on Escape
  and returns focus to the opener. Three tabs:
  - **Price:** a receipt with price kind, status, displayed price, the verbatim
    quote, source and retrieval time; prices shown for options; then the backend
    equation, the alternative-product price callout, or the reasons it was not
    compared;
  - **Identity:** match reasons, a "To resolve" callout, the LLM 2 match
    proposal (classification, agreement, verified reasons and differing
    attributes with quotes) or why there is none, the backend's uncertainties,
    and a ✓ / ✕ / ? checklist of Stompbox against competitor values;
  - **Source:** page, retrieval, search query and rank, extraction,
    availability (status, verified text, reason), page type.

  Review-required rows open on Identity; the others open on Price.

### Live Runs (`pages/LiveRuns.tsx`)

- **Run header:**
  - product names, run ID, times, any domain subset or discovery-off note, and a
    large status pill;
  - two-step **Cancel** (focus moves to Confirm, and back on Keep running), and
    **Re-run**;
  - a compact **7-segment track**: decorative, `aria-hidden`; dashed for
    skipped, pulsing while running.
- **Outcome banner** for finished runs: the outcome, a count strip, usage and
  cost, and warnings.
- **Pipeline timeline** (`dashboard/PipelineStepper.tsx`): a vertical list with
  connected nodes.
  - Node states: ✓ completed, ● running (pulsing ring, `aria-current="step"`),
    ! partial, ✕ failed, a dashed outline for skipped, numbered for pending.
  - Each stage shows its status pill, finish time, progress bar, message and
    errors.
- **Products:** cards with a status rail and a "View results" link.
- **Results so far:** live counters taken from `run.summary`.
- **Event feed** (`dashboard/EventTimeline.tsx`):
  - filter chips (`aria-pressed`), a product filter, and a "Follow live" switch;
  - while paused, a "N new · resume" pill;
  - events grouped under sticky stage headings, newest first, capped at 200.
  - each `competitor_discovered` item shows the backend class and, when
    `auto_search_eligible` is true, an "Automatically searchable" pill (Stage 8.1;
    older events show none). The UK evidence pill was removed.
  - This is display only; SSE ingestion is untouched.
- **Discovered domains** (shown when the run result has discovery candidates):
  `RunDomains.DiscoveredDomainsTable`, the same table as Domain Review's run summary. Since
  Stage 8.1 its last column is **Why**: "Automatically searchable" or "Not searched
  automatically", with the backend's `decision_reasons` and LLM 1's informational UK answer on
  demand. The UK evidence column and `components/UkEvidence.tsx` were removed.
- **New run:** the catalogue picker, and a sticky **selection tray** with "N of
  5 selected", discovery and domain options, the disabled reason and Start.

### Domain Review (`pages/DomainReview.tsx`)

- **Selected run summary:** what the run searched, its pending domains, and
  everything it discovered.
- **Tabs** (`components/Tabs.tsx`: roving tabindex, arrows, Home/End): Pending,
  Approved, Rejected, Configured, Excluded and skipped, each with a count.
- **Domain cards** (`CandidateCard` in `Review.tsx`; simplified 14 Nov 2026 at the
  user's request; approval logic, endpoints and the decision form unchanged). Each
  card shows only:
  - company name and domain (monospace link), or the domain alone as the title
    when no company name is verified; and the review-status pill;
  - the **competitor classification**: Direct Competitor (green), Related Retailer
    (grey) or Needs Review (neutral outline), each with an icon and label;
  - the company type (the reviewer's, else LLM 1's proposal, marked as such).
    It is omitted when there is no type (the card then says Needs Review), except
    that a known marketplace domain shows "marketplace (known marketplace domain)";
  - for decided domains, one line: "Approved/Rejected by <reviewer>, <date>";
  - the **search status** (Stage 8.1, `searchEligibility.ts`, `components/SearchStatus.tsx`),
    from the backend's `search_basis` and reasons: "Automatically searchable retailer",
    "Manually approved: searched", "Rejected: never searched", "Manufacturer: needs review",
    "Marketplace: needs review", "Directory: not searched for offers", "Used-only retailer: needs
    review", "Pending: not
    classified yet" (no LLM 1 classification, or one from before Stage 8.1 that needs
    rediscovery), "Eligible: automatic search is off" or "Pending review", with one line saying
    why. "Show evidence" adds the full reasons and LLM 1's `serves_uk_customers` answer,
    labelled as information only. Approve and Reject stay available on automatic domains.
    The benchmark checkbox in the decision form is unchanged and never ticked automatically.
  - actions: Approve (primary), Reject, or Change decision, plus "Show evidence (N)";
  - evidence on demand: product pages first, each with a link, a snippet clamped
    to three lines, and its source and retrieval time (scrolls past 360 px).
- Relevance, confidence, the LLM rationale, automatic-rule reasons, eligibility
  flags, facts and decision history are no longer shown on the card. They are
  still stored and returned by the API.
- **Classification (16 Nov 2026):** the backend's `competitor_class` is the source
  of truth (LLM 1 proposal plus `rules.classify_competitor` safeguards; see
  `docs/competitor-discovery.md`). `competitorClass.ts` only maps
  `DIRECT_COMPETITOR` / `RELATED_RETAILER` / `NEEDS_REVIEW` to the three labels.
  The final class is shown, never `proposed_competitor_class`. The frontend
  relevance-threshold rule was removed: relevance and confidence are never used.
- **Temporary fallback** for records stored before classification existed (not yet
  backfilled; `competitor_class` null or unknown): a known marketplace domain
  (`MARKETPLACE_DOMAIN_LABELS`) or a reviewer-typed marketplace is **Related
  Retailer**; everything else (retailers, manufacturers, directories, other,
  unknown) is **Needs Review**. The fallback never gives Direct Competitor. Its
  pill has `data-source="fallback"` and a "provisional" tooltip; nothing else about
  the card changes.
- **Live runs:** the `competitor_discovered` SSE payload carries `competitor_class`
  and `proposed_competitor_class`. The event feed shows the class pill on that
  event as soon as it arrives (events without the field show none). The run's
  "All domains discovered" table shows the class, with the classification status
  beneath it.
- **Configured:** a tile grid, each tile showing Benchmark ★ or "Reference
  only, not a benchmark".
- **Excluded and skipped:** reason lists. Run-scoped exclusions are labelled as
  not stored.

## Product images and branding (MarketLens)

- `components/ProductThumb.tsx` and `productImages.ts`: one square thumbnail with one placeholder,
  loaded through `/api/images`, with a larger view on click. Used in the catalogue picker, the
  Product Intelligence header (catalogue image), the comparison tables and Overview price cards
  (each listing's own image) and the evidence drawer's Source tab. Details:
  [product-images.md](product-images.md).
- The product is named **MarketLens** in the landing page, sidebar, page titles and boot screen.
  "Stompbox" stays where it means the client retailer (its prices, pages and catalogue).

## Design tokens (`index.css`)

| Group | Values |
|---|---|
| Navy | 950 `#0a1324` (sidebar), 900 `#0f1c33` (hero), 800 `#172848`, 700 `#20365e` (active); nav text `#b8c4d6`, muted `#8193ad`; cyan `#5ec8ff` (on navy only) |
| Workspace | canvas `#f2f4f7`, surface `#fff`, surface-2 `#f8f9fb`, lines `#e4e7ec` / `#d0d5dd` |
| Text | `#101828`, `#344054`, muted `#5d6879` |
| Accent | cobalt `#2f5bea` (hover `#2448c4`, tint `#eef2fe`) |
| Match marks | exact `#12a071`, review `#e0a000`, similar `#7b61ff`, no match: neutral hatch |
| Match labels (text/tint) | exact `#05603a/#e7f6ef`, review `#7a4b00/#fff4dc`, similar `#4b2fc2/#f0edff`, no match `#475467/#f1f3f6` |
| Type | system UI stack; page title 22/650, hero 32, product name 26, prices 34; eyebrows 11 px uppercase 0.08em; monospace for IDs and domains; tabular figures |
| Shape | radius 16 (heroes), 12 (panels), 8 (controls); a hairline shadow; one radial navy gradient on the Overview hero only |

**Colour checks:**
- **Palette validator:** the three categorical match colours pass the dataviz
  skill's validator, adjacent and all-pairs (worst colour-blind ΔE 10.6, normal
  vision 22.7). "No match" is deliberately neutral (a hatch, not a series).
- **Contrast relief:** amber is under 3:1 against white, so it always comes with
  a visible count legend and the data table.
- **Text pairs:** all checked at 4.5:1 or better (lowest: accent on accent tint,
  4.93:1). Hero labels use `#b8c4d6`, because the muted navy failed on the
  lighter hero corner.

## Responsive behaviour

| Width | Layout |
|---|---|
| ≥ 1280 px | 240 px sidebar; Overview distribution (8 columns) beside 2×2 tiles (4 columns); prices (7) beside attention (5); pipeline (7) beside products (5) |
| 1024–1279 px | Full labelled sidebar kept (the screenshot showed the old rail at laptop width); paired sections stack; tiles in a row of 4 |
| 768–1023 px | 76 px icon rail (names kept for assistive technology); system card hidden; heroes stack; track labels hidden |
| < 768 px | Slide-out drawer navigation (Escape closes it and returns focus); full-width run switcher; 44 px touch targets; full-screen evidence sheet; single-column domain cards; sticky first column in tables |
| < 480 px | Compact track hidden (the timeline stays); tiles in one column |

Reduced motion stops every pulse, ring and slide.

## Live updates (`runStream.ts`, unchanged)

- **Stream:** an EventSource listening to each of the 19 event types.
- **Order and gaps:** duplicates are ignored, and gaps are filled from
  `/events?after=`.
- **Reconnect:** `Last-Event-ID`; the client polls after 3 errors or a refused
  stream.
- **Terminal event:** the stream is closed, followed by a final refresh.
- **Finished runs:** opened without a stream.
- **Expired runs:** a 404 shows "no longer available".

## Tests

Run with `cd frontend && npm test` (through `scripts/vitest.mjs`; see the README).
The fixture is generated by `python -m tests.make_dashboard_fixture`, and the
page tests render the whole app against `mockAppApi`.

| File | Covers |
|---|---|
| `selectors.test.ts` | KPIs equal backend fields; eligible-only comparisons (even when an ineligible row carries a difference); coverage; insights; `keyFinding` rules (finding, failed, running, extraction off, cancelled, no exact); `priceGate` from statuses and reasons only |
| `status.test.ts` | every backend status mapped; match statuses distinct; review vs stage pending |
| `components/DistributionBar.test.tsx` | segments sized by counts; zero counts omitted; data table; empty bar; no `%` |
| `runStream.test.ts` | 19 event types; merge, dedupe and gaps; terminal close; backfill; 404; polling fallback; unmount |
| `app/AppShell.test.tsx` | grouped navigation and `aria-current`; `?run=` kept; default run and page; pending count; config; drawer and Escape; skip link; one stream across pages; badge hidden when finished; **run switcher keyboard** (open, Escape returns focus, arrow and Enter switches run); connection badge states |
| `pages/Overview.test.tsx` | hero product and key finding; stat tiles and their sources; distribution counts only; price cards; coverage folding; price explainer when LLM 2 is off; needs attention; empty and expired states |
| `pages/ProductIntelligence.test.tsx` | header; eligible-only difference; non-benchmark label; **evidence drawer** (opening tab by status, tab arrows, Escape, Close returns focus, focus trap); collapsed no-match; missing price; no-LLM and empty states |
| `pages/LiveRuns.test.tsx` | start request and SSE; 429; two-step cancel with focus and 409; every stage state; outcome, re-run and approved-since note; feed filter chips; expired run; "Automatically searchable" feed pill from `auto_search_eligible`, no `uk_*` payload fields |
| `searchEligibility.test.ts` | every search status (automatic, manual approval, rejected, manufacturer, marketplace, directory, unclassified or pre-Stage 8.1, pending, automatic search off); the UK answer never changes it; older backends without `search_basis` |
| `pages/DomainReviewSearch.test.tsx` | Domain Review search status and reasons, no UK evidence UI, informational UK answer on demand, benchmark checkbox separate and never ticked, approved and rejected shown distinctly |
| `pages/DomainReview.test.tsx` | approval workflow; **approval and benchmark shown separately**; configured tiles; rejected; run-scoped excluded; arrow-key tabs; selected-run summary |
| `CataloguePicker`, `reviewer`, `format` tests | filter and 5-product limit; shared reviewer name; formatting (missing vs zero, signed difference, dates without seconds, plurals) |

## Limitations

- **One run:** the Overview covers a single in-memory run, which disappears
  after the TTL or a restart.
- **Without LLM 2:** no prices are extracted, so there are no comparisons; the
  price explainer says why and how to enable extraction.
- **Excluded domains are run-scoped;** rejected decisions persist.
- **Approval:** approval never affects a running run.
- **Visual check (20 Nov 2026, Stage 6.3.6):** done with headless Chrome against
  the production build and a read-only mock API serving the generated fixture
  (no backend, no providers), at 1440, 1024 and 390 px (390 and 1024 inside an
  iframe of exactly that width). It found that wide tables widened Live Runs at
  390 px (grid items with `min-width: auto`); fixed in `index.css`. After the fix
  no page scrolls horizontally; tables scroll inside their own container, so the
  last column of the discovered-domains table needs a sideways scroll on
  a phone. Not checked: a real phone, other browsers, touch interaction, a live
  run streaming real data.

## Post-Stage 7 additions (20 November 2026)

- **Unconfirmed price baseline:** `priceBaseline.ts` / `components/BaselineWarning.tsx` show a warning note
  for SB-028 (£579 recorded as a sale price, "was £599") in the product header (width-capped in the price
  column), the evidence drawer's Price tab (both the compared and not-compared branches) and the Overview
  price cards. Display only. Tests: `components/BaselineWarning.test.tsx`, `pages/BaselineWarningPages.test.tsx`.
- **Comparison table:** the Evidence column (`.col-action`) is sticky on the right edge with a separating
  shadow, and long "not compared" reasons in the price columns wrap. Checked with headless Chrome against the
  saved Stage 7 runs at 1440 (no horizontal scroll), 1024 and 390 px (the table scrolls inside its container,
  the Evidence button stays visible).

## Domain status wording (Stage 8.2, follow-up)

- `AUTO_APPROVED` is labelled **"Automatically eligible"** (status pill, `domain_auto_approved` feed
  label and the discovery feed pill). It was "Automatically searchable", which was wrong whenever
  automatic search is switched off or the per-run cap applies. Being searched is shown only from the
  run's search records ("Searched in this run", `domainStatus.searchedDomains`).
- The Overview "Competitors searched" tile counts this run's targets as "configured",
  "reviewer-approved" and "automatic".
- Live Runs and the Domain Review "Selected run" panel show the backend's
  `domain_outcomes[].reason` under "Status in this run" when a domain was not searched, and a
  callout lists approved or automatically eligible domains that were not searched, with the reason.
  Reasons are put in plain English by `plainLanguage.explain` (automatic search switched off, over
  the per-run limit). Runs saved before this show no reason (unchanged behaviour).
- Test stability: `vite.config.ts` sets `testTimeout: 20000` and `maxWorkers: '50%'`, and
  `src/test/setup.ts` sets Testing Library's `asyncUtilTimeout` to 5000 ms. With one jsdom worker per
  core, whole-app page tests timed out at random under load; assertions are unchanged.
