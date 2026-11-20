"""Phase 6 price validation (deterministic, independent of match status).

Starts from the Phase 5 field evidence and re-checks an EXTRACTED price from
scratch with `app.services.prices`:
- the price text parses to the stored amount and currency;
- the verbatim quote contains that amount;
- the amount is not a was/RRP/original/saving figure, and not a conditional
  (from / per month / deposit) amount;
- the amount is positive and the page is a single-product page.

Only VALID + GBP prices with fresh evidence can be compared. Snippet text is
never price evidence. Missing is never zero.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.agents.intelligence.schemas import CompetitorProduct, ExtractedField, ExtractionStatus, FieldStatus, PageType
from app.schemas.common import EvidenceStatus
from app.services.matching.schemas import PriceStatus, PriceValidation
from app.services.prices import (
    find_prices,
    ignored_price_category,
    is_conditional_context,
    is_delivery_context,
    is_sale_context,
    parse_single_price,
)

_FROM_FIELD = {
    FieldStatus.NOT_FOUND: (PriceStatus.MISSING, EvidenceStatus.NOT_FOUND),
    FieldStatus.UNSUPPORTED: (PriceStatus.UNSUPPORTED, EvidenceStatus.UNVERIFIED),
    FieldStatus.EXCLUDED_SALE_PRICE: (PriceStatus.EXCLUDED_SALE_PRICE, EvidenceStatus.UNVERIFIED),
    FieldStatus.AMBIGUOUS: (PriceStatus.AMBIGUOUS, EvidenceStatus.UNVERIFIED),
    FieldStatus.CONFLICT: (PriceStatus.CONFLICT, EvidenceStatus.CONFLICT),
    FieldStatus.INVALID: (PriceStatus.INVALID, EvidenceStatus.UNVERIFIED),
}
_NOT_EXTRACTED_REASON = {
    ExtractionStatus.SKIPPED_NO_LLM: "No LLM extraction (no-LLM mode): no price was read from the page",
    ExtractionStatus.SKIPPED_NOT_RETRIEVED: "Page was not retrieved: no price evidence",
    ExtractionStatus.FAILED: "LLM extraction failed: no price evidence",
}


def _delivery_amounts(c: CompetitorProduct) -> set:
    """Amounts listed as delivery charges, re-derived from the raw ignored-price texts (the stored
    categories are not trusted, and older records have none)."""
    return {m.amount for t in c.ignored_prices if ignored_price_category(t) == "delivery" for m in find_prices(t)}


def validate_price(c: CompetitorProduct, now: datetime, max_age_hours: int) -> PriceValidation:
    ev = next((e for e in c.field_evidence if e.field is ExtractedField.PRICE), None)
    base = dict(source_url=c.product_url, retrieved_at=c.page.retrieved_at, sale_indicated=c.sale_indicated,
                quote=ev.quote if ev else None, price_text=ev.value if ev else None)  # fmt: skip

    if c.extraction_status is not ExtractionStatus.COMPLETED:
        return PriceValidation(status=PriceStatus.NOT_EXTRACTED, evidence_status=EvidenceStatus.UNVERIFIED,
                               reasons=[_NOT_EXTRACTED_REASON.get(c.extraction_status, "Not extracted")], **base)  # fmt: skip
    if ev is None:
        return PriceValidation(status=PriceStatus.INVALID, evidence_status=EvidenceStatus.UNVERIFIED,
                               reasons=["No price evidence record"], **base)  # fmt: skip
    if ev.status is not FieldStatus.EXTRACTED:
        status, evidence = _FROM_FIELD[ev.status]
        return PriceValidation(status=status, evidence_status=evidence,
                               reasons=[f"Phase 5 price status {ev.status.value}" + (f": {ev.note}" if ev.note else "")],
                               **base)  # fmt: skip

    # EXTRACTED: re-verify independently; any inconsistency makes the record INVALID.
    problems: list[str] = []
    parsed = parse_single_price(c.price_text)
    quote = ev.quote or ""
    in_quote = [m for m in find_prices(quote) if c.price is not None and m.amount == c.price]
    if c.price is None or c.price <= 0:
        problems.append("price is missing or not positive")
    if parsed is None or parsed.amount != c.price:
        problems.append("price text does not parse to the stored amount")
    if parsed is not None and parsed.currency and parsed.currency != c.currency:
        problems.append(f"price text currency {parsed.currency} differs from stored {c.currency}")
    if not in_quote:
        problems.append("amount not found in its quote")
    elif all(is_delivery_context(quote, m) for m in in_quote):
        # Stage 6.1.1: re-checked here too, so an older run file or a client-submitted run can never
        # turn a delivery charge into a VALID product price.
        return PriceValidation(status=PriceStatus.INVALID, evidence_status=EvidenceStatus.UNVERIFIED,
                               reasons=["Amount is a delivery/shipping charge in its quote, not a product price"],
                               **base)  # fmt: skip
    elif c.price in _delivery_amounts(c):
        return PriceValidation(status=PriceStatus.AMBIGUOUS, evidence_status=EvidenceStatus.UNVERIFIED,
                               reasons=["The same amount is listed as a delivery charge on the page"], **base)  # fmt: skip
    elif all(is_sale_context(quote, m) for m in in_quote):
        return PriceValidation(status=PriceStatus.EXCLUDED_SALE_PRICE, evidence_status=EvidenceStatus.UNVERIFIED,
                               reasons=["Amount is shown as a was/RRP/original/saving figure in its quote"], **base)  # fmt: skip
    elif any(is_conditional_context(quote, m) for m in in_quote):
        return PriceValidation(status=PriceStatus.AMBIGUOUS, evidence_status=EvidenceStatus.UNVERIFIED,
                               reasons=["Conditional amount (from / per month / deposit)"], **base)  # fmt: skip
    if c.page_type is not PageType.PRODUCT_PAGE:
        problems.append("price is not from a single-product page")
    if not c.currency:
        problems.append("no currency")
    if problems:
        return PriceValidation(status=PriceStatus.INVALID, evidence_status=EvidenceStatus.CONFLICT,
                               reasons=[f"Inconsistent price record: {p}" for p in problems], **base)  # fmt: skip

    evidence = EvidenceStatus.VERIFIED
    reasons = [f"Current displayed price {c.price_text} verified in quote from the retrieved page"]
    if c.page.retrieved_at and now - c.page.retrieved_at > timedelta(hours=max_age_hours):
        evidence = EvidenceStatus.STALE
        reasons.append(f"Retrieved {c.page.retrieved_at:%Y-%m-%d %H:%M} UTC, older than {max_age_hours} h")
    if c.page.cache_source:
        reasons.append(f"Provider content source: {c.page.cache_source} (retrieval time is not the retailer's update time)")
    if c.sale_indicated:
        reasons.append("Page shows sale/was wording; only the current displayed price is used, no discount computed")
    status = PriceStatus.VALID if c.currency == "GBP" else PriceStatus.WRONG_CURRENCY
    if status is PriceStatus.WRONG_CURRENCY:
        reasons.append(f"Currency {c.currency} is not GBP: not used for UK comparison")
    return PriceValidation(status=status, evidence_status=evidence, price=c.price, currency=c.currency,
                           reasons=reasons, **{**base, "price_text": c.price_text})  # fmt: skip
