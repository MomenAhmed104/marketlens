"""Deterministic checks on LLM 2 output (Phase 5).

LLM 2 proposes values with verbatim quotes. A value is kept only if:
- its quote appears in the page text LLM 2 was given (or the page title), and
- the value itself appears in that quote.

The price gets extra checks:
- the amount is parsed in Python and must be one of the amounts in its quote;
- an amount shown as was/RRP/original/saving is excluded;
- a conditional amount ("from", per month, deposit) is ambiguous;
- the currency must be shown explicitly;
- zero or unparseable amounts are invalid.

None of this compares prices or decides whether the page is the same product
as the Stompbox item (Phase 6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.agents.intelligence.schemas import (
    ExtractedField,
    ExtractedSpec,
    FieldEvidence,
    FieldStatus,
    PageExtraction,
    PageType,
)
from app.services.prices import (
    find_prices,
    is_conditional_context,
    is_sale_context,
    mentions_sale,
    parse_single_price,
)
from app.services.text_match import mentions_model

MAX_SPECS = 15
MAX_TEXT = 300


def norm(text: str | None) -> str:
    """Comparison form: casefold, unify quotes/dashes, collapse whitespace."""
    t = (text or "").translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"}))
    return " ".join(t.split()).casefold()


@dataclass
class CheckedExtraction:
    values: dict[ExtractedField, str | None] = field(default_factory=dict)
    price: Decimal | None = None
    currency: str | None = None
    price_text: str | None = None
    specifications: list[ExtractedSpec] = field(default_factory=list)
    sale_indicated: bool = False
    evidence: list[FieldEvidence] = field(default_factory=list)


def _quote_in_page(quote: str | None, page: str) -> bool:
    q = norm(quote)
    return bool(q) and q in page


def _check_text_field(
    f: ExtractedField, value: str | None, quote: str | None, page: str, flexible: bool = False
) -> FieldEvidence:
    value = (value or "").strip()[:MAX_TEXT] or None
    quote = (quote or "").strip()[:MAX_TEXT * 2] or None
    if value is None:
        return FieldEvidence(field=f, status=FieldStatus.NOT_FOUND, value=None, quote=quote)
    if not _quote_in_page(quote, page):
        return FieldEvidence(field=f, status=FieldStatus.UNSUPPORTED, value=value, quote=quote,
                             note="quote not found in the retrieved page text")  # fmt: skip
    in_quote = mentions_model(value, quote) if flexible else norm(value) in norm(quote)
    if not in_quote:
        return FieldEvidence(field=f, status=FieldStatus.UNSUPPORTED, value=value, quote=quote,
                             note="value does not appear in its quote")  # fmt: skip
    return FieldEvidence(field=f, status=FieldStatus.EXTRACTED, value=value, quote=quote)


def _check_price(ext: PageExtraction, page: str) -> tuple[FieldEvidence, Decimal | None, str | None]:
    f = ExtractedField.PRICE
    value = (ext.displayed_price or "").strip() or None
    quote = (ext.price_quote or "").strip()[:MAX_TEXT * 2] or None

    def ev(status: FieldStatus, note: str | None = None) -> tuple[FieldEvidence, None, None]:
        return FieldEvidence(field=f, status=status, value=value, quote=quote, note=note), None, None

    if value is None:
        return ev(FieldStatus.NOT_FOUND)
    parsed = parse_single_price(value)
    if parsed is None:
        return ev(FieldStatus.INVALID, "price is not a single amount with a currency marker")
    if parsed.amount <= 0:
        return ev(FieldStatus.INVALID, "non-positive amount (a missing price is never treated as zero)")
    if not _quote_in_page(quote, page):
        return ev(FieldStatus.UNSUPPORTED, "price quote not found in the retrieved page text")
    in_quote = [m for m in find_prices(quote) if m.amount == parsed.amount]
    if not in_quote:
        return ev(FieldStatus.UNSUPPORTED, "amount does not appear in its quote")
    if all(is_sale_context(quote, m) for m in in_quote):
        return ev(FieldStatus.EXCLUDED_SALE_PRICE, "amount is shown as a was/RRP/original/saving figure")
    ignored = {m.amount for t in ext.ignored_prices for m in find_prices(t)}
    if parsed.amount in ignored:
        return ev(FieldStatus.EXCLUDED_SALE_PRICE, "LLM 2 also listed this amount as an ignored (crossed-out/RRP) price")
    if ext.price_is_conditional or any(is_conditional_context(quote, m) for m in in_quote):
        return ev(FieldStatus.AMBIGUOUS, "conditional price (from / per month / deposit / option-dependent)")
    shown = {m.currency for m in in_quote} | {parsed.currency}
    shown.discard(None)
    if not shown:
        return ev(FieldStatus.AMBIGUOUS, "no explicit currency shown with the amount")
    if len(shown) > 1:
        return ev(FieldStatus.CONFLICT, f"different currencies shown: {sorted(shown)}")
    currency = shown.pop()
    stated = (ext.currency or "").strip().upper() or None
    if stated and stated != currency:
        return ev(FieldStatus.CONFLICT, f"stated currency {stated} but the page shows {currency}")
    if ext.page_type is not PageType.PRODUCT_PAGE:
        return ev(FieldStatus.AMBIGUOUS, f"page is a {ext.page_type.value}, not a single-product page")
    return FieldEvidence(field=f, status=FieldStatus.EXTRACTED, value=value, quote=quote), parsed.amount, currency


def check_extraction(ext: PageExtraction, page_text: str, page_title: str | None) -> CheckedExtraction:
    """Apply the deterministic evidence checks to one page's LLM 2 output."""
    page = norm(f"{page_title or ''}\n{page_text}")
    out = CheckedExtraction()
    checks = [
        _check_text_field(ExtractedField.PRODUCT_TITLE, ext.product_title, ext.title_quote, page),
        _check_text_field(ExtractedField.BRAND, ext.brand, ext.brand_quote, page),
        _check_text_field(ExtractedField.MODEL, ext.model, ext.model_quote, page, flexible=True),
        _check_text_field(ExtractedField.SKU, ext.sku, ext.sku_quote, page, flexible=True),
        _check_text_field(ExtractedField.AVAILABILITY, ext.availability, ext.availability_quote, page),
    ]
    price_ev, amount, currency = _check_price(ext, page)
    checks.append(price_ev)
    for ev in checks:
        out.evidence.append(ev)
        if ev.field is not ExtractedField.PRICE:
            out.values[ev.field] = ev.value if ev.status is FieldStatus.EXTRACTED else None
    if price_ev.status is FieldStatus.EXTRACTED:
        out.price, out.currency, out.price_text = amount, currency, price_ev.value
    # Specifications: keep only name/value pairs whose value is in the page text.
    for spec in ext.specifications[:MAX_SPECS]:
        if spec.value.strip() and norm(spec.value) in page:
            out.specifications.append(ExtractedSpec(name=spec.name.strip()[:80], value=spec.value.strip()[:MAX_TEXT]))
    out.sale_indicated = bool(ext.ignored_prices) or mentions_sale(ext.price_quote)
    return out


_SKU_LIKE = re.compile(r"[A-Za-z0-9]")


def product_queries(brand: str, model: str, name: str, specifications: str | None, n: int) -> list[str]:
    """Search texts for one product: a combined brand+model+name query, then (if n=2)
    an exact "brand model" query. Variant details (handedness, finish) live in the name."""
    combined = name if mentions_model(model, name) and mentions_model(brand, name) else f"{brand} {model} {name}"
    if specifications:
        first = re.split(r"[;\n|]", specifications)[0].strip()
        if first and _SKU_LIKE.search(first) and len(first) <= 60:
            combined = f"{combined} {first}"
    queries = [" ".join(combined.split())[:300]]
    exact = f"{brand} {model}"
    if n >= 2 and norm(exact) != norm(queries[0]):
        queries.append(exact)
    return queries
