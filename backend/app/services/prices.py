"""Deterministic price-text parsing for extraction checks (Phase 5).

Only parsing and context detection live here: it turns text such as
"Was £599.00 Now £579.00" into amounts with their currency and position, and
says whether an amount is presented as a crossed-out/original/saving figure
or as a conditional price ("from £x", "£x per month").

No price comparison or validation against the catalogue happens here: that is
Phase 6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Symbol -> ISO code. "$" alone is ambiguous (USD, AUD, CAD...), so it gives no currency.
_SYMBOLS = {"£": "GBP", "€": "EUR", "$": None, "US$": "USD"}
_CODES = {"GBP", "EUR", "USD"}

_AMOUNT = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_PRICE = re.compile(
    rf"(?P<pre>US\$|£|€|\$|\b(?:GBP|EUR|USD)\b)\s?(?P<amt>{_AMOUNT})(?![\d%]|\s?[kKmM]\b)"
    rf"|(?<![\w.,])(?P<amt2>{_AMOUNT})\s?(?P<post>\b(?:GBP|EUR|USD)\b)",
    re.IGNORECASE,
)

# Words that mark an amount as NOT the current selling price when they come
# just before it ("Was £599", "RRP £649", "Save £20", "Original price £599").
# "Regular price" is deliberately absent: Shopify uses it for the normal price.
_SALE_BEFORE = re.compile(
    r"(?:\bwas|\brrp|\br\.r\.p\.?|\bsave|\bsaving|\byou save|\boriginally|\boriginal price|\blist price"
    r"|\bprevious price|\breduced from|\bcompare at|\bmsrp|\bdiscount|\bstrike)\W{0,4}(?:\bprice\W{0,3})?$",
    re.IGNORECASE,
)
# "Regular/normal price £219 Special/Sale/Offer price £197" (Shopify, Magento...):
# the regular amount is the original only when a current-price label is also shown.
_REGULAR_BEFORE = re.compile(r"\b(?:regular|normal|usual)\s+price\W{0,4}$", re.IGNORECASE)
_SALE_PRICE_LABEL = re.compile(
    r"\b(?:sale|special|offer|deal|promo(?:tional)?|reduced|now)\s+price\b|\bnow\s*(?=£|€|\$|\d)", re.IGNORECASE
)
_SALE_AFTER = re.compile(r"^\W{0,3}(?:off\b|saving\b|discount\b|less\b)", re.IGNORECASE)

# Conditional amounts: not a single standard displayed price.
_CONDITIONAL_BEFORE = re.compile(
    r"(?:\bfrom|\bstarting at|\bas low as|\bdeposit(?: of)?|\bfinance(?: from)?|\bmonthly|\bpay)\W{0,4}$", re.IGNORECASE
)
_CONDITIONAL_AFTER = re.compile(
    r"^\s*(?:/\s*(?:mo|month|m)\b|per month\b|a month\b|p/?m\b|pcm\b|monthly\b|deposit\b|x\s?\d+\b|for \d+ months\b)",
    re.IGNORECASE,
)
_WINDOW = 30


@dataclass(frozen=True)
class PriceMention:
    amount: Decimal
    currency: str | None  # ISO code, or None when no unambiguous currency is shown
    start: int
    end: int
    text: str  # the matched text, e.g. "£1,299.00"


def find_prices(text: str | None) -> list[PriceMention]:
    """Every money amount with an explicit currency marker, in order."""
    out: list[PriceMention] = []
    for m in _PRICE.finditer(text or ""):
        raw = m.group("amt") or m.group("amt2")
        marker = (m.group("pre") or m.group("post") or "").upper()
        currency = marker if marker in _CODES else _SYMBOLS.get(marker)
        try:
            amount = Decimal(raw.replace(",", ""))
        except InvalidOperation:  # pragma: no cover - regex guarantees digits
            continue
        out.append(PriceMention(amount, currency, m.start(), m.end(), m.group(0)))
    return out


def parse_single_price(text: str | None) -> PriceMention | None:
    """The amount in a short price string such as "£579.00". None unless exactly one."""
    found = find_prices(text)
    return found[0] if len(found) == 1 else None


def is_sale_context(text: str, mention: PriceMention) -> bool:
    """True if the amount is shown as a was/RRP/original/saving figure."""
    before = text[max(0, mention.start - _WINDOW): mention.start]
    after = text[mention.end: mention.end + _WINDOW]
    if _SALE_BEFORE.search(before) or _SALE_AFTER.search(after):
        return True
    return bool(_REGULAR_BEFORE.search(before) and _SALE_PRICE_LABEL.search(text))


def is_conditional_context(text: str, mention: PriceMention) -> bool:
    """True for "from £x", "£x per month", "deposit £x" and similar."""
    before = text[max(0, mention.start - _WINDOW): mention.start]
    after = text[mention.end: mention.end + _WINDOW]
    return bool(_CONDITIONAL_BEFORE.search(before) or _CONDITIONAL_AFTER.search(after))


def mentions_sale(text: str | None) -> bool:
    """Whether any amount in the text is presented as a sale/original/saving figure."""
    text = text or ""
    return any(is_sale_context(text, m) for m in find_prices(text)) or bool(
        re.search(r"\d+\s?%\s?off\b|\bsale\b|\bclearance\b", text, re.IGNORECASE))  # fmt: skip
