# Search provider evaluation: Exa vs Tavily

**Run:** `search-benchmark-20261110T203634Z`, 10 Nov 2026, 20:36–20:39 UTC.
**Providers:** Exa and Tavily. Brave was not run: it has no page-content API, and
the user kept it disabled.
**Decision (10 Nov 2026, approved):** Exa is the default search and retrieval
provider (`SEARCH_PROVIDER=exa`). Tavily is optional with no automatic fallback. Brave is disabled.

| Artefact | Location | In git |
|---|---|---|
| Metrics summary (current rules) | `docs/benchmarks/search-benchmark-20261110T203634Z.json` | yes |
| Metrics summary (first-pass rules, before manual inspection) | `docs/benchmarks/search-benchmark-20261110T203634Z.classifier-v1.json` | yes |
| Raw searches, pool, retrievals with full page content, batches, run log | `data/benchmark_runs/search-benchmark-20261110T203634Z/` | no (third-party content) |
| Re-scored raw records | same folder, `*.rescored.json` | no |

## 1. Recommendation

**Use Exa as the single provider for targeted search and page retrieval.** Keep
the Tavily adapter as a tested fallback. Don't build a hybrid yet. Revisit
Tavily for global company discovery during the Phase 3 evaluation, where it was
only slightly better here.

Why:

- **Targeted search:** Exa found an exact product page for 38 (product, retailer)
  pairs against Tavily's 27. Every pair Tavily found, Exa also found (Exa recall
  against the union of both: 100%; Tavily: 71%).
- **Retrieval:** Exa returned usable content for 28 of 42 shared pages (67%),
  Tavily for 14 (33%). Tavily failed on **every Gear4music page (12/12)** and
  **every Bax page (13/13)**. Gear4music is a high-priority competitor.
- **Price evidence:** on exact product pages, Exa content included the displayed
  price whenever the product was purchasable. Every price checked against the
  live page was correct (section 6). Tavily's content omitted the price on 2 of
  its 11 exact pages.
- **Cost and latency:** comparable. Exa's retrieval batches had a p95 of 4.0 s;
  Tavily's p95 was 41.6 s.
- **Where Tavily was better:** global discovery was slightly more UK-focused (24%
  vs 16% UK hosts; an exact known-retailer page in the top 10 for 46% vs 31% of
  products). However, the sets of UK retailers each discovered overlap heavily
  (13 shared out of 18 in total). And when a retailer doesn't stock the product,
  Tavily returns nothing, while Exa returns unrelated filler results. Those are
  harmless after deterministic scoring but add noise.

Gaps that remain whichever provider is chosen are listed in section 9.

## 2. Method

**Products:** 13 products chosen to cover all four catalogue categories and the
identity traps exact matching must handle (`app/benchmark/products.py`).

**Identity rules:** each product has a **base model** plus **variant rules**
derived from the catalogue name or SKU. The catalogue file is not modified.

- `required_variants`: every one must appear for an EXACT match. Examples: SB-028
  left-handed *and* Iced Tea; SB-021 Vivid Burgundy; SB-035 Cosmic Blue; SB-047 Head.
- `forbidden`: a different configuration. Examples: SB-047 2x12, cab, stack or
  combo; SB-069 "Hall".
- `review_if_missing`: SB-028 must say "Figured". Stompbox's own page calls it the
  "Les Paul Standard 60s Figured", although the catalogue name doesn't.
- **Brand** must be present. A Gibson page can't be EXACT for an Epiphone product.
- **Condition:** pre-owned, second-hand, B-stock, ex-demo and nearly-new listings
  are not EXACT.
- **Accessory:** a power supply, case, cable or flight case "for <model>" is not
  the product.

A result is classified as `EXACT`, `VARIANT_MISMATCH`, `CONDITION_MISMATCH`,
`ACCESSORY`, `REVIEW` or `NO_MATCH`, from its title and URL slug. "Exact product
page" means EXACT on a retailer product URL. For Bax and Yamaha Music London the
URL pattern isn't modelled, so the page type is "unknown" and is counted.

**Queries (identical for both providers):** `<brand> <base model> [variant words]`,
localised to GB.

- **Global:** 13 queries, 10 results each.
- **Targeted:** 13 products × the 12 retailer competitors (Gibson excluded as a
  manufacturer reference), 5 results each.

**URL handling:** every result keeps its original URL as evidence plus a
canonical form, used for deduplication and fetching. Canonicalisation strips
trailing slashes, fragments and default ports, and maps Gear4music `/<cc>/<lang>/`
paths to the UK store.

**Retrieval:** both providers fetched one shared pool of 42 canonical URLs. The
pool held, per product, up to 2 best-ranked EXACT/REVIEW retailer product pages
from each provider, plus one mismatch page as a **negative control**, to test
whether a price would be wrongly attributed to the requested product.

- **Exa:** `maxAgeHours=24` (accept a cache entry under 24 h old, otherwise
  crawl live); live-crawl wait capped at 15 s.
- **Tavily:** basic extract depth.

**Price attribution (benchmark heuristic, not Phase 5 extraction):**

- Take the first GBP amount after the first mention of the model, once markdown
  link and image URLs are removed.
- Skip amounts that read as delivery thresholds, savings, RRP or recommended
  prices, finance terms, basket totals, loyalty credit or zero.

**Bot-check pages:** content under 2,000 characters that matches a bot-check or
access-denied pattern is now reported as `BLOCKED`, not `OK`. This is in the
production adapters as well as the benchmark.

**Manual inspection:** I read every pooled page's classification and price
context, inspected targeted results for the variant and zero-coverage products,
and fetched 7 retailer pages live to check prices.

The first-pass rules contained six errors, found during that inspection and fixed:

1. Missing brand check: a Gibson page was scored EXACT for an Epiphone product.
2. Accessories scored EXACT: power supply, cables, hard case and flight case.
3. "Figured" was treated as a different model. Stompbox's own page shows it is
   the same model.
4. Andertons' curly-quote spelling `‘60s` wasn't matched.
5. Bot-check pages were counted as `OK`.
6. Price-heuristic errors:
   - It took the RRP instead of Bax's selling price.
   - "Not currently in stock" was read as "in stock".
   - Markdown URLs distorted distances in Tavily content.
   - "bag" and "get" matched inside other words ("gig bag", "budget").

The run was then re-scored from the saved raw data (`--rescore`) **without new API
calls**. Both summaries are kept.

## 3. Overall comparison

| | Exa | Tavily |
|---|---|---|
| Search requests / errors | 169 / 0 | 169 / 0 |
| Targeted pairs with an exact product page | **38 / 156 (24.4%)** | 27 / 156 (17.3%) |
| Targeted recall vs union of both | **100%** | 71.1% |
| Global: products with an exact known-retailer page in the top 10 | 30.8% | **46.2%** |
| Retrieval OK on the shared 42 pages | **28 (66.7%)** | 14 (33.3%) |
| Exact retrieved pages with an attributed price | 15 / 20; the other 5 are discontinued or "not available" pages | 10 / 11 (1 wrong, see section 6) |
| Search latency p50 / p95 | 1.9 s / 4.3 s | 2.4 s / 3.5 s (targeted) |
| Retrieval batch latency p50 / p95 | 2.5 s / **4.0 s** | 1.8 s / **41.6 s** |
| Cost of this run | **$1.130** (provider-reported) | **175 credits** (169 search, reported; 6 extract) = $1.40 at the $0.008 pay-as-you-go rate; within the free plan, so $0 billed |

## 4. Global discovery

| | Exa | Tavily |
|---|---|---|
| Results | 130 | 129 |
| Results on UK hosts | 16.2% | **24.0%** |
| Results on the 12 configured retailers | 5.4% | **9.3%** |
| Products with an exact configured-retailer page in the top 10 | 4 / 13 | **6 / 13** |
| Mean rank of the first such page | 4.0 | 4.0 |
| UK hosts with an exact match | 15 | 16 |

- **UK hosts found by both providers (13):** andertons, backalleymusic,
  beckettsmusic, ebay.co.uk, ev-entz, gmmusic, guitarguitar,
  lisburnmusiccentreshop, prebeat, rguitars, stompbox, thomann,
  volumemusicsolutions.
- **Exa only:** livelouder, musicbits.
- **Tavily only:** absolutemusic, amazon.co.uk, brittensmusic.

**Key finding for Phase 3:** four products (SB-055 OMP-1, SB-044 BAT4CEL, SB-047
St. James head, SB-091 RCT-4) have **no** exact page at any configured competitor,
from either provider.

- **SB-047:** Andertons, Guitarguitar, Gear4music and Bax list only the combo
  versions. All were correctly scored `VARIANT_MISMATCH`.
- **The other three:** the exact items appear only at smaller UK shops outside the
  configured list (Beckett's, GM Music, Volume Music Solutions, Prebeat, Ev-Entz
  and others).

Whole-web discovery of additional competitors is therefore needed for useful
coverage, not optional.

## 5. Targeted domain search

| | Exa | Tavily |
|---|---|---|
| Empty responses | 13 / 156 | **113 / 156** |
| Pairs with an exact product page | **38** | 27 |
| Pairs with only variant, condition or accessory mismatches | 7 | 5 |
| Top result is an exact product page (of non-empty responses) | 23.8% | **48.8%** |
| Off-domain results leaked through the domain filter | 0 | 0 |

**Exact pages found per retailer:**

| Retailer | Exa | Tavily |
|---|---|---|
| Andertons | 7 | 4 |
| GuitarGuitar | 8 | 4 |
| Gear4music | 7 | 6 |
| Thomann | 8 | 8 |
| Bax | 7 | 4 |
| Yamaha Music London | 1 | 1 |
| Langley, The Music Dept, Project Music, The 58 Sound, Perfect Pitch, South London Music Shop | 0 | 0 |

**Exact retailers found per product:**

| Product | Exa | Tavily |
|---|---|---|
| SB-001 Boss RC-5 | 5 | 4 |
| SB-081 Line 6 Helix LT | 6 | 3 |
| SB-069 JHS 3 Series Reverb | 5 | 5 |
| SB-075 DigiTech Whammy DT | 5 | 5 |
| SB-013 Mooer Ensemble King | 4 | 2 |
| SB-055 Emma OMP-1 | 0 | 0 |
| SB-028 Epiphone LP Std '60s LH | 4 | 2 |
| SB-021 Cort G250 SE | 4 | 3 |
| SB-035 Ibanez BTB705LM | 4 | 2 |
| SB-044 Bromo BAT4CEL | 0 | 0 |
| SB-049 NUX Mighty 20 MKII | 1 | 1 |
| SB-047 Blackstar St. James Head | 0 | 0 |
| SB-091 Chord RCT-4 | 0 | 0 |

- The six smaller retailers returned no exact pages from either provider. Exa
  returned unrelated items from those sites (for example trombones and cello
  strings for a guitar query); Tavily mostly returned nothing.
- Tavily's higher top-1 rate is mostly a side effect of returning nothing, not
  better ranking: Exa found more exact pages in absolute terms.
- **The left-handed case (SB-028):**
  - Exa found the exact Figured, left-handed, Iced Tea page at Andertons,
    Guitarguitar, Gear4music and Thomann (`60s F ITB LH`); Tavily found it at
    Andertons and Thomann. The Bax left-handed Iced Tea page doesn't say
    "Figured", so it is REVIEW.
  - Both providers also returned the right-handed Figured, left-handed Ebony and
    pre-owned versions, plus a *Gibson* left-handed Iced Tea page. These are
    correctly scored as mismatch or REVIEW under the current rules.
- **Gear4music:** both providers often return `/us/en/` URLs. These are now
  canonicalised to the UK store and fetched there, and Exa retrieved GBP prices
  from them. One Spanish URL with a translated path (`/Guitarra-y-bajo/...`)
  can't be canonicalised by a rule and returned 404.

## 6. Page retrieval and price evidence

Both providers fetched the same 42 pages: 33 candidates and 9 negative controls.

| | Exa | Tavily |
|---|---|---|
| OK | **28** | 14 |
| Blocked (bot-check page returned as content) | 10 (all Bax) | 0 |
| Failed | 4 | **28** |
| Failures by host | Andertons 2, Thomann 1 (B-stock pages that no longer exist: 404 for both providers), Gear4music 1 (Spanish URL) | **Bax 13, Gear4music 12**, Andertons 2, Thomann 1 |
| Median content length | 11.0k characters | 12.6k characters (markdown, includes navigation) |
| Freshness metadata | 23 crawled live, 5 cache under 24 h | none |
| Candidate pages with availability text found | 95.8% | 81.8% |

### Prices checked against the live page (fetched directly, 10 Nov 2026)

| Page | Live | Exa | Tavily |
|---|---|---|---|
| Andertons Boss RC-5 | £219.00 | £219.00 ✔ | £219.00 ✔ |
| Andertons Line 6 Helix LT | £599.00 | £599.00 ✔ | £599.00 ✔ |
| Andertons Mooer Ensemble King | £49.99 | £49.99 ✔ | **£10.00 ✘** (price element missing from content; £10 came from prose about a power supply) |
| Andertons Ibanez BTB705LM Cosmic Blue | £999.00 | £999.00 ✔ | **none** (price missing from content) |
| Andertons Epiphone LP Std ‘60s Figured LH Iced Tea | £599.00, Out of Stock | £599.00 ✔ | £599.00 ✔ |
| Thomann Mooer Ensemble King | £43 | £43.00 ✔ | £43.00 ✔ |
| Thomann NUX Mighty 20 MKII | £132 | £132.00 ✔ | £132.00 ✔ |

- **Couldn't be checked independently:** Gear4music, Guitarguitar, Bax and
  Stompbox return 403 or 404 to a direct fetch.
- **Exa values for those pages:** Gear4music RC-5 £195.50, Helix LT £677.00,
  JHS £90.00, Whammy DT £224.50; Guitarguitar RC-5 £209.00, JHS £99.00 (Tavily
  agrees on both Guitarguitar prices); Bax JHS £99, Helix LT £749.
- **Cross-provider agreement:** of the 14 pages both retrieved, the attributed
  price matches on 12. The 2 mismatches are the Tavily content gaps above.
- **Negative controls** all behaved correctly. The Andertons St. James **combo**
  (£1,199), the Guitarguitar BTB705LM **Natural Browned Burst** (£1,089), the
  Andertons **right-handed** Figured Iced Tea (£599, Preorder) and the Gear4music
  **Nearly New** RC-5 (£186) were all classified as a different product, so their
  prices would not be attributed to the requested Stompbox product.
- **Unrelated amounts:** pages contain many GBP figures besides the price
  (delivery thresholds, RRP, loyalty credit, basket totals, related products).
  The median page has 4 distinct GBP amounts; Tavily's Thomann pages have
  25–38 because related-product tiles are included. A snippet or first-£
  approach is unsafe, which confirms the plan that LLM 2 extracts the price and
  deterministic checks validate it.

## 7. Exact-model and variant accuracy

Under the current rules, manual review of every retrieved page classified EXACT
(Exa 20, Tavily 11) found **no misclassification**. The first-pass rules had
**5** wrong EXACTs (4 accessories and 1 wrong brand), now fixed and covered by
tests.

**Traps handled correctly:**

- RC-500, RC-505 and RC-10R never matched RC-5.
- "3 Series Hall Reverb" never matched "3 Series Reverb".
- The St. James combos and 2x12 version were flagged.
- Left-handed Ebony and right-handed Iced Tea were flagged.
- BTB705LM in Natural Browned Burst was flagged.
- Secondhand, B-stock and Nearly New listings were flagged, including Gear4music's
  Spanish "De segunda mano".

**REVIEW cases left for a person or LLM:**

- Gear4music "Les Paul Standard 60s Left Handed, Iced Tea" without "Figured": it
  may be the earlier plain-top version.
- Truncated titles that end in "…".

These are rules for this benchmark. Phase 6 must implement production identity
rules (base model and variant attributes, brand, condition, accessory) with the
same strictness.

## 8. Latency and cost

| | Exa | Tavily |
|---|---|---|
| Global search p50 / p95 | 1.9 s / 3.4 s | 1.5 s / 3.9 s |
| Targeted search p50 / p95 / max | 1.9 / 4.3 / 6.1 s | 2.4 / 3.5 / 3.9 s |
| Retrieval batch p50 / p95 | 2.5 / 4.0 s | 1.8 / 41.6 s |
| Search cost | $1.092 for 169 searches (≈ $0.0065 each, provider-reported) | 169 credits (reported) |
| Retrieval cost | $0.038 for 42 pages (provider-reported) | 6 credits (part reported, part estimated pro rata) |
| Estimated cost per product run (1 global + 12 targeted + ~4 pages) | ≈ $0.09 | ≈ 14 credits (≈ $0.11 pay-as-you-go) |

**Earlier smoke test (same day):**

- Tavily searches took 4–20 s. Today they took 2–4 s, so Tavily latency varies
  by time of day.
- Exa's first retrieval batch took 36 s. That was caused by the old 36 s
  live-crawl cap; the cap is now 15 s.

**Total spend today:**

- **Exa:** ≈ $1.24. That is this run ($1.130), the smoke test ($0.101) and
  fetching Stompbox's own SB-028 page ($0.001).
- **Tavily:** ≈ 190 credits of the free 1,000/month: this run (175 credits) plus
  about 15 from the smoke test.

## 9. Failure analysis and remaining gaps

| Problem | Affects | Notes and next step |
|---|---|---|
| Bax serves a bot-check page | Exa (10 of 13 Bax pages blocked); Tavily (all failed) | Now reported as `BLOCKED`. Bax evidence is unreliable from both providers; consider a fallback fetcher or marking Bax `UNVERIFIED` |
| Gear4music not retrievable | Tavily only (12 of 12 failed) | Exa retrieved every Gear4music UK page except the translated-path URL |
| Search index contains removed pages | Both (B-stock pages 404) | Retrieval reports a failure; never treat it as evidence |
| Translated Gear4music paths (`/Guitarra-y-bajo/`) | Both | Can't be canonicalised by a rule; skip or flag non-English paths |
| Six smaller configured retailers have no indexed exact pages | Both | May not stock these products, or are poorly indexed. Shopify-style URLs (`/products/…`) could be added to page-type rules |
| Niche products not stocked by configured competitors | Both | Needs whole-web discovery in Phase 3 |
| Many unrelated GBP amounts in content | Both, worse for Tavily | LLM 2 extraction plus deterministic validation (Phase 5/6) |
| Price element missing from extracted content | Tavily (2 of 11 Andertons pages) | Not seen with Exa on available products |
| Stompbox page shows "Original price was £599, current price £579" (SB-028), and the catalogue stores £579 | Catalogue | The price policy says standard displayed price only; the catalogue snapshot may hold a sale price. Flag for the Phase 5/6 price policy |

## 10. Limitations

- One run on one day with 13 products. The provider indexes and retailer stock
  change constantly.
- Identity and price attribution are deterministic heuristics written for this
  benchmark, refined by manual inspection of this same data. Absolute rates are
  therefore optimistic. The **relative** comparison between providers is the
  reliable signal.
- Only 7 prices could be verified against live pages. Four retailers block direct
  fetching.
- "No exact page found" means the provider didn't surface one. It does **not**
  mean the retailer doesn't stock the product.
- Retrieval timestamps are our own; they are not the retailer's update time. Exa
  says whether content came from its cache (under 24 h old) or was crawled live;
  Tavily gives no freshness information.
- Tavily was run at `basic` depth for both search and extract. `advanced` (2
  credits) wasn't tested and might change the retrieval results.
- The retrieval pool was chosen with the first-pass rules. After re-scoring, 7 of
  the 33 pooled candidates changed class (3 accessories, 3 REVIEW, 1 condition),
  and some pages that became EXACT were not pooled.
