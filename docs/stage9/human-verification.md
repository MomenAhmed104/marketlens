# Stage 9 human verification checklist

Used after each Stage 9 run, before scoring. The reviewer fills in the run's
`verifications.template.json` (written next to the run's artifacts; schema:
`backend/app/stage9/scoring.py`, `Verifications`). Anything not checked stays `UNKNOWN` / empty and
is left out of the scores; it is never counted as right or wrong.

Work from the run's `result.json` (extracted values and verbatim quotes) and the live source page.
Record the date and time you looked at each page: a price can change between retrieval and review.

## 1. Each candidate listing (`candidates[]`, one per product URL)

| Check | How | Record |
|---|---|---|
| Page is the one retrieved | Open `product_url`; confirm it is the same product page (not a redirect to a category or another item) | `note` if not |
| Title | Compare the extracted `product_title` with the page heading | `title` (as shown on the page) |
| Brand and model | Brand and the full model (e.g. RC-5, not RC-500; WST-905Li, not WST-905) on the page | `model` |
| Price | The current selling price on the page, in GBP, for the item itself. Not a was / RRP / saving amount, a finance or "from" price, a delivery charge or a basket total | `price`, `currency` |
| Price is current | The page shows that price now; if it differs from the extracted price, record the page value and the time | `price`, `note` |
| Availability | In stock, out of stock, pre-order, back-order / "available to order", discontinued, or unclear | `availability` (IN_STOCK, OUT_OF_STOCK, PREORDER, BACKORDER, DISCONTINUED, UNKNOWN) |
| Standalone or bundle | Is anything included with the product (power supply, cable, case, bag, strap, stand, footswitch, multi-pack)? Is an extra only optional / sold separately / required but not included? | `identity` (below) |
| Condition | New, used / secondhand / nearly new, B-stock / ex-demo / refurbished | `identity` = VARIANT_MISMATCH if not new |
| Variant | Handedness, finish, model designation (SB-028 must be Figured, left-handed, Iced Tea Burst), configuration (head vs combo / cabinet), string count | `identity` |

`identity` values:

- `EXACT`: the exact catalogue product, new, standalone (extras only optional / not included).
- `VARIANT_MISMATCH`: same model but another variant, condition, configuration, or a bundle with
  included extras.
- `DIFFERENT_PRODUCT`: another model or another product (e.g. RC-500, an accessory for the RC-5).
- `NOT_A_PRODUCT_PAGE`: category, search, blog, guide or forum page.
- `UNKNOWN`: cannot tell from the page (say why in `note`), e.g. a guitar "with gig bag" where the
  page does not say whether the bag is standard.

## 2. Each discovered domain (`domain_relevance`)

Mark `RELEVANT` only if all hold; otherwise `NOT_RELEVANT`; `UNKNOWN` if you cannot tell.

- It is a retailer (or specialist retailer) selling to customers, not a marketplace, price
  comparison site, directory, manufacturer-only site, review site, forum or blog.
- It sells new products in the relevant category (guitars, amplification, effects, accessories).
- It sells to UK customers: a UK business, or a shop abroad that delivers to the UK with prices
  that can be paid in GBP (note which).
- It is not Stompbox itself and not PMT (excluded by decision).

Record the evidence page you used in a note to the reviewer log (the template has no free-text
field per domain; keep a short list alongside it).

## 3. Answer-key consistency

For each product, compare what the run found with `docs/stage9/answer-key.json`:

- Did a HYPOTHESIS expectation hold (e.g. Andertons lists the exact RC-5)? Note the date: it
  confirms the listing for that date only.
- Does the evidence contradict a hypothesis? Record it; do not edit the key during scoring.
- UNKNOWN expectations stay unknown unless you propose, separately, to change the key.
- Price scoring: SB-001 and SB-028 are excluded (baselines not confirmed). Prices for those two are
  still checked for extraction accuracy, but no price difference is scored.

## 4. Then score

```
cd backend
python -c "from app.stage9 import scoring, evaluation, harness; ..."   # score_run(..., verifications=...)
```

(`score_run(key, run, result, ledger=..., duration_s=..., verifications=Verifications(...),
catalogue=...)`; `render_markdown` prints the table.)
